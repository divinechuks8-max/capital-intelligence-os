"""Cboe volatility index adapter (Phase 14, options/derivatives
extension): daily OHLC history for Cboe's own published volatility
indices (VIX, VVIX, SKEW, ...).

Confirmed live before building this: `cdn.cboe.com/api/global/
us_indices/daily_prices/{CODE}_History.csv` is free, requires no API key
or registration, and returns the complete real history (VIX: 1990 to
present). Cboe's own site
(cboe.com/tradable-products/vix/vix-historical-data) explicitly describes
this as public data, "Updated Daily" — distinct from Cboe DataShop, the
paid product for granular options-level data. See
capint.models.volatility.VolatilityIndexLevel's docstring for why this
was chosen over per-security unusual-options-activity or put/call-ratio
data, which remain genuinely gated.

No API key needed, and (unlike Alpha Vantage in Phase 13) no free-tier
depth limit either — the full multi-decade history is available in one
request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

INDEX_HISTORY_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{index_code}_History.csv"


@dataclass(frozen=True)
class RawVolatilityIndexLevel:
    index_code: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class _RateLimitedCboeClient:
    def __init__(self, client: httpx.Client | None = None, min_request_interval: float = 0.5) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def get(self, url: str) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.get(url)
        self._last_request_at = time.monotonic()
        return resp


class CBOEVolatilityIndexAdapter(SourceAdapter):
    source_name = "Cboe Global Markets"

    def __init__(self, client: httpx.Client | None = None, min_request_interval: float = 0.5) -> None:
        self._http = _RateLimitedCboeClient(client=client, min_request_interval=min_request_interval)

    def fetch_index_history(self, index_code: str) -> list[RawVolatilityIndexLevel]:
        """Returns an empty list for an index code Cboe doesn't publish
        (a 404 from the CDN), not an error."""
        url = INDEX_HISTORY_URL.format(index_code=index_code.upper())
        resp = self._http.get(url)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()

        lines = resp.text.strip().splitlines()
        if not lines or lines[0].strip().upper() != "DATE,OPEN,HIGH,LOW,CLOSE":
            return []

        levels = []
        for line in lines[1:]:
            parts = line.strip().split(",")
            if len(parts) != 5:
                continue
            date_str, open_str, high_str, low_str, close_str = parts
            levels.append(
                RawVolatilityIndexLevel(
                    index_code=index_code.upper(),
                    trade_date=datetime.strptime(date_str, "%m/%d/%Y").date(),
                    open=Decimal(open_str),
                    high=Decimal(high_str),
                    low=Decimal(low_str),
                    close=Decimal(close_str),
                )
            )
        return levels

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.finra_short_interest.FINRAShortInterestAdapter's
        identical note. This adapter is per-index, not time-windowed."""
        raise NotImplementedError(
            "CBOEVolatilityIndexAdapter is per-index; use fetch_index_history "
            "via capint.ingestion.cboe_volatility instead."
        )
