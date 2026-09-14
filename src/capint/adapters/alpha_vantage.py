"""Alpha Vantage adapter (Phase 13, backtesting extension): daily OHLCV
price bars.

Confirmed live before building this: Alpha Vantage's REST API is free
with a self-service API key (instant, no approval wait) and returns real
daily price data for `TIME_SERIES_DAILY`. Auth is a plain `apikey` query
parameter.

**A real, load-bearing limitation confirmed live**: `outputsize=full`
(complete multi-year history) is a premium-only feature on the free tier
— requesting it returns an explicit "Information" error, not data. The
free tier only supports `outputsize=compact` (the trailing ~100 trading
days). This adapter therefore cannot backfill deep history; it only ever
fetches a recent window. See capint.models.price.PriceBar's docstring and
this system's README "Known limitations" for what that means for
backtesting older signals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

API_BASE = "https://www.alphavantage.co/query"


@dataclass(frozen=True)
class RawPriceBar:
    ticker: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class _RateLimitedAlphaVantageClient:
    """Alpha Vantage's free tier documents 5 requests/minute and 25/day —
    this self-imposed pacing is a courteous default, same spirit as every
    other adapter's rate limiter, not an attempt to track that exact
    daily quota."""

    def __init__(self, api_key: str, client: httpx.Client | None = None, min_request_interval: float = 12.5) -> None:
        if not api_key.strip():
            raise ValueError("An Alpha Vantage API key is required — refusing to send unauthenticated requests.")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=20.0)
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def get(self, params: dict[str, Any]) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.get(API_BASE, params={**params, "apikey": self._api_key})
        self._last_request_at = time.monotonic()
        return resp


class AlphaVantageAdapter(SourceAdapter):
    source_name = "Alpha Vantage"

    def __init__(self, api_key: str, client: httpx.Client | None = None, min_request_interval: float = 12.5) -> None:
        self._http = _RateLimitedAlphaVantageClient(api_key, client=client, min_request_interval=min_request_interval)

    def fetch_daily_prices(self, ticker: str, outputsize: str = "compact") -> list[RawPriceBar]:
        """`outputsize="compact"` (the free-tier-supported value) returns
        the trailing ~100 trading days. Returns an empty list for a
        ticker Alpha Vantage doesn't recognize, or if the free tier
        rejects the request (e.g. `outputsize="full"` without a premium
        subscription) — both surface as a response with no
        "Time Series (Daily)" key rather than an HTTP error."""
        resp = self._http.get(
            {"function": "TIME_SERIES_DAILY", "symbol": ticker.upper(), "outputsize": outputsize}
        )
        resp.raise_for_status()
        data = resp.json()
        series = data.get("Time Series (Daily)")
        if not series:
            return []

        bars = []
        for trade_date_str, values in series.items():
            bars.append(
                RawPriceBar(
                    ticker=ticker.upper(),
                    trade_date=datetime.strptime(trade_date_str, "%Y-%m-%d").date(),
                    open=Decimal(values["1. open"]),
                    high=Decimal(values["2. high"]),
                    low=Decimal(values["3. low"]),
                    close=Decimal(values["4. close"]),
                    volume=int(values["5. volume"]),
                )
            )
        return sorted(bars, key=lambda b: b.trade_date)

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.finra_short_interest.FINRAShortInterestAdapter's
        identical note. This adapter is per-ticker, not time-windowed."""
        raise NotImplementedError(
            "AlphaVantageAdapter is per-ticker; use fetch_daily_prices via capint.ingestion.alpha_vantage instead."
        )
