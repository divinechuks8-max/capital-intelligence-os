"""GDELT Project adapter (Phase 15, news/sentiment extension): news tone
("sentiment") distribution for a search query, via the free DOC 2.0 API.

Confirmed live before building this: `api.gdeltproject.org/api/v2/doc/doc`
requires no API key or registration, and GDELT's own Terms of Use
(gdeltproject.org/about.html#termsofuse) explicitly permit "academic,
commercial, or governmental use of any kind without fee" and
redistribution "in any form" (attribution required) — the opposite
finding from Alpha Vantage, Twelve Data, Finnhub, Polygon.io, and
Etherscan, all checked and found to restrict free-tier use to personal,
non-commercial purposes only. GDELT is built and funded specifically as
an open research dataset, not a commercial data-API product with a paywalled
tier.

**Rate limit confirmed live**: GDELT asks for no more than one request
every 5 seconds (a real HTTP 429 with that exact guidance was returned
during testing when requests came faster) — this adapter's default
pacing respects that.

See capint.models.news_sentiment.NewsSentimentSnapshot's docstring for
why this is a directional signal, not a precise per-company one — GDELT
has no concept of "company," only full-text search over global news.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


@dataclass(frozen=True)
class RawToneBin:
    bin: int
    count: int


@dataclass(frozen=True)
class RawToneDistribution:
    query: str
    timespan: str
    retrieved_at: datetime
    article_count: int
    mean_tone: Decimal | None
    bins: list[RawToneBin]


class _RateLimitedGdeltClient:
    def __init__(self, client: httpx.Client | None = None, min_request_interval: float = 5.5) -> None:
        self._client = client or httpx.Client(timeout=30.0)
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def get(self, params: dict[str, Any]) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.get(DOC_API_URL, params=params)
        self._last_request_at = time.monotonic()
        return resp


class GDELTAdapter(SourceAdapter):
    source_name = "GDELT Project"

    def __init__(self, client: httpx.Client | None = None, min_request_interval: float = 5.5) -> None:
        self._http = _RateLimitedGdeltClient(client=client, min_request_interval=min_request_interval)

    def fetch_tone_distribution(self, query: str, timespan: str = "7d") -> RawToneDistribution | None:
        """`timespan` uses GDELT's own format (e.g. "7d", "24h", "1m").
        Returns None if GDELT has no matching coverage for this query in
        the window — not necessarily an error, just nothing to report."""
        resp = self._http.get({"query": query, "mode": "tonechart", "timespan": timespan, "format": "json"})
        resp.raise_for_status()
        data = resp.json()
        tonechart = data.get("tonechart")
        if not tonechart:
            return None

        bins = [RawToneBin(bin=int(entry["bin"]), count=int(entry["count"])) for entry in tonechart]
        total_articles = sum(b.count for b in bins)
        if total_articles == 0:
            return None

        weighted_sum = sum(b.bin * b.count for b in bins)
        mean_tone = Decimal(str(round(weighted_sum / total_articles, 3)))

        return RawToneDistribution(
            query=query,
            timespan=timespan,
            retrieved_at=datetime.now(timezone.utc),
            article_count=total_articles,
            mean_tone=mean_tone,
            bins=bins,
        )

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.finra_short_interest.FINRAShortInterestAdapter's
        identical note. This adapter is per-query, not time-windowed."""
        raise NotImplementedError(
            "GDELTAdapter is per-query; use fetch_tone_distribution via capint.ingestion.gdelt instead."
        )
