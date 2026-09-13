"""FINRA consolidated short interest adapter (Phase 10, spec §24).

First non-SEC data source in this system. FINRA Rule 4560 requires member
firms to report short positions in all equity securities; FINRA publishes
a consolidated (across all exchanges) view via a genuinely public REST
API — confirmed live, no API key or registration needed:
https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest
(despite the "otcMarket" group name, this covers NYSE/Nasdaq-listed
securities too — confirmed against real AAPL/Agilent/Alcoa data).

Entity resolution wrinkle unique to this adapter: FINRA's data has no
issuer CIK at all, only a ticker symbol (`symbolCode`). Every prior
adapter in this system resolved by CIK or CUSIP; this one must resolve by
bare ticker — exactly what spec §5 warns against relying on solely, since
tickers get reassigned over time. See capint.ingestion.finra_short_interest
for how this is handled and its documented limitation.

Settlement-date discovery: FINRA reports short interest bi-monthly, on
settlement dates that fall near the 15th and the last calendar day of
each month, shifted around holidays/weekends in a way this adapter does
not try to compute exactly (that needs a full market holiday calendar).
Instead, `fetch_recent_settlement_dates` generates a small window of
candidate dates around both anchors per month and probes each against the
real API, keeping whichever actually have data — an unanticipated holiday
shift is harmless, the wrong candidate is simply skipped.

Confirmed live before writing this: the API returns pipe-quoted CSV by
default, but honors `Accept: application/json` for much easier parsing
with correctly-typed numbers — used here instead of writing a CSV parser.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

SHORT_INTEREST_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"


def _candidate_settlement_dates(as_of: date, num_months: int = 4) -> list[date]:
    candidates: set[date] = set()
    year, month = as_of.year, as_of.month
    for _ in range(num_months):
        mid = date(year, month, 15)
        for offset in range(-3, 3):
            candidate = mid + timedelta(days=offset)
            if candidate.month == month and candidate.year == year:
                candidates.add(candidate)

        first_of_next_month = date(year, month, 28) + timedelta(days=4)
        month_end = first_of_next_month - timedelta(days=first_of_next_month.day)
        for offset in range(-4, 1):
            candidate = month_end + timedelta(days=offset)
            if candidate.month == month and candidate.year == year:
                candidates.add(candidate)

        month -= 1
        if month == 0:
            month = 12
            year -= 1

    return sorted((c for c in candidates if c <= as_of), reverse=True)


@dataclass(frozen=True)
class RawShortInterestFact:
    ticker: str
    issue_name: str
    settlement_date: date
    current_short_position: Decimal
    previous_short_position: Decimal | None
    change_percent: Decimal | None
    change_quantity: Decimal | None
    average_daily_volume: Decimal | None
    days_to_cover: Decimal | None
    exchange_code: str | None
    market_class_code: str | None


class _RateLimitedFinraClient:
    """Thin HTTP wrapper for FINRA's API — a courteous identifying
    User-Agent and self-imposed rate limit, same spirit as
    capint.adapters.sec_common.RateLimitedSecClient but kept separate
    since FINRA is a different organization with its own (much lighter,
    no-registration) access policy, not SEC's."""

    def __init__(self, user_agent: str, client: httpx.Client | None = None, min_request_interval: float = 0.2) -> None:
        if not user_agent.strip():
            raise ValueError("An identifying User-Agent is required — refusing to send anonymous requests.")
        self._client = client or httpx.Client(timeout=20.0)
        self._headers = {"User-Agent": user_agent, "Accept": "application/json", "Content-Type": "application/json"}
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def post(self, url: str, json_body: dict[str, Any]) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.post(url, headers=self._headers, json=json_body)
        self._last_request_at = time.monotonic()
        return resp


class FINRAShortInterestAdapter(SourceAdapter):
    source_name = "FINRA"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
    ) -> None:
        self._http = _RateLimitedFinraClient(user_agent, client=client, min_request_interval=min_request_interval)

    def _query(self, settlement_date: date, ticker: str | None, limit: int) -> list[dict[str, Any]]:
        compare_filters = [{"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": settlement_date.isoformat()}]
        if ticker is not None:
            compare_filters.append({"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": ticker})
        resp = self._http.post(SHORT_INTEREST_URL, {"limit": limit, "compareFilters": compare_filters})
        if resp.status_code == 204:
            return []
        resp.raise_for_status()
        if not resp.content:
            return []
        return resp.json()

    def fetch_recent_settlement_dates(self, as_of: date | None = None, num_cycles: int = 6) -> list[date]:
        """Probes candidate settlement dates (newest first) against the
        real API, one cheap limit=1 request each, and returns up to
        `num_cycles` that actually have data."""
        as_of = as_of or date.today()
        confirmed: list[date] = []
        for candidate in _candidate_settlement_dates(as_of):
            if len(confirmed) >= num_cycles:
                break
            rows = self._query(candidate, ticker=None, limit=1)
            if rows:
                confirmed.append(candidate)
        return confirmed

    def fetch_short_interest(self, ticker: str, settlement_date: date) -> RawShortInterestFact | None:
        """Returns None if FINRA has no short-interest record for this
        ticker on this settlement date (not necessarily an error — a
        thinly-traded or unlisted symbol may simply not be reported)."""
        rows = self._query(settlement_date, ticker=ticker.upper(), limit=1)
        if not rows:
            return None
        row = rows[0]

        def _decimal(key: str) -> Decimal | None:
            value = row.get(key)
            if value is None:
                return None
            try:
                return Decimal(str(value))
            except InvalidOperation:
                return None

        current = _decimal("currentShortPositionQuantity")
        if current is None:
            return None

        return RawShortInterestFact(
            ticker=row.get("symbolCode", ticker.upper()),
            issue_name=row.get("issueName", "UNKNOWN"),
            settlement_date=settlement_date,
            current_short_position=current,
            previous_short_position=_decimal("previousShortPositionQuantity"),
            change_percent=_decimal("changePercent"),
            change_quantity=_decimal("changePreviousNumber"),
            average_daily_volume=_decimal("averageDailyVolumeQuantity"),
            days_to_cover=_decimal("daysToCoverQuantity"),
            exchange_code=row.get("issuerServicesGroupExchangeCode"),
            market_class_code=row.get("marketClassCode"),
        )

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.sec_xbrl.SECXBRLFactsAdapter.fetch_records for why
        the typed methods above are preferred for real ingestion."""
        raise NotImplementedError(
            "FINRAShortInterestAdapter is per-ticker; use fetch_recent_settlement_dates + "
            "fetch_short_interest via capint.ingestion.finra_short_interest instead."
        )
