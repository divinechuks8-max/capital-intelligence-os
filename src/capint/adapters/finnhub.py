"""Finnhub adapter (Phase 14, analyst-estimates extension): aggregate
analyst recommendation trends.

Confirmed live before building this: Finnhub's free tier (self-service
API key at finnhub.io/register, instant) genuinely includes
`/stock/recommendation` — real counts of covering analysts by rating
bucket (strong buy/buy/hold/sell/strong sell) per month, for a real
ticker (confirmed against AAPL). Every other free/legal analyst-estimate
source checked during Phase 12's research (IBES/Refinitiv, Zacks,
Visible Alpha) required a paid/licensed relationship; this endpoint
doesn't, at least for this aggregate-count view (not individual named
analysts or price targets, which Finnhub gates behind a paid plan).

Auth is a plain `token` query parameter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

API_BASE = "https://finnhub.io/api/v1/stock/recommendation"


@dataclass(frozen=True)
class RawRecommendationTrend:
    ticker: str
    period: date
    strong_buy: int
    buy: int
    hold: int
    sell: int
    strong_sell: int


class _RateLimitedFinnhubClient:
    """Finnhub's free tier documents 60 calls/minute — this self-imposed
    pacing is a courteous default, same spirit as every other adapter's
    rate limiter, not an attempt to track that exact quota."""

    def __init__(self, api_key: str, client: httpx.Client | None = None, min_request_interval: float = 1.1) -> None:
        if not api_key.strip():
            raise ValueError("A Finnhub API key is required — refusing to send unauthenticated requests.")
        self._api_key = api_key
        self._client = client or httpx.Client(timeout=20.0)
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def get(self, params: dict[str, Any]) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.get(API_BASE, params={**params, "token": self._api_key})
        self._last_request_at = time.monotonic()
        return resp


class FinnhubAdapter(SourceAdapter):
    source_name = "Finnhub"

    def __init__(self, api_key: str, client: httpx.Client | None = None, min_request_interval: float = 1.1) -> None:
        self._http = _RateLimitedFinnhubClient(api_key, client=client, min_request_interval=min_request_interval)

    def fetch_recommendation_trends(self, ticker: str) -> list[RawRecommendationTrend]:
        """Returns an empty list for a ticker Finnhub has no data for
        (confirmed live: a real HTTP 200 with an empty JSON array, not an
        error)."""
        resp = self._http.get({"symbol": ticker.upper()})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return []

        trends = []
        for row in data:
            trends.append(
                RawRecommendationTrend(
                    ticker=row.get("symbol", ticker.upper()),
                    period=datetime.strptime(row["period"], "%Y-%m-%d").date(),
                    strong_buy=int(row["strongBuy"]),
                    buy=int(row["buy"]),
                    hold=int(row["hold"]),
                    sell=int(row["sell"]),
                    strong_sell=int(row["strongSell"]),
                )
            )
        return sorted(trends, key=lambda t: t.period)

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.finra_short_interest.FINRAShortInterestAdapter's
        identical note. This adapter is per-ticker, not time-windowed."""
        raise NotImplementedError(
            "FinnhubAdapter is per-ticker; use fetch_recommendation_trends "
            "via capint.ingestion.finnhub instead."
        )
