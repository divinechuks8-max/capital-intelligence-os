"""SEC XBRL company-facts adapter: corporate capital allocation (Phase 7:
buybacks/dividends/debt) and fundamentals (Phase 8: revenue/earnings/
margins, spec §21). One company-facts fetch feeds both — see
extract_capital_allocation_facts and extract_fundamental_facts.

Structurally different from every prior adapter here, and deliberately so:

- No "current filings across the universe" feed exists for this data.
  Buyback/dividend/debt figures are reported per company, quarterly and
  annually, as structured (XBRL) facts inside 10-Q/10-K filings — there is
  no analogue of "the 100 most recent buyback filings". This adapter is
  therefore per-company: give it a CIK, it returns that company's relevant
  facts. capint.ingestion.sec_xbrl drives it over the set of companies
  this system already tracks (from Form 4/13F/13D-G ingestion).
- Cash-flow-statement XBRL facts (buybacks, dividends, debt) are reported
  YEAR-TO-DATE within a fiscal year, not as discrete quarters — confirmed
  against Apple's real company facts before writing this: a Q2 10-Q's
  PaymentsForRepurchaseOfCommonStock covers the full H1, a Q3 filing
  covers 9 months, etc. Deriving a discrete quarterly number means
  subtracting consecutive YTD figures, which is fragile across fiscal-
  year boundaries, restatements, and non-calendar fiscal years. This
  adapter sidesteps that trap entirely by only ingesting facts from 10-K
  filings whose own start/end duration is ~330-380 days (a full fiscal
  year) — one non-overlapping total per completed fiscal year, at coarser
  (annual, not quarterly) granularity, rather than risk a subtly wrong
  derived number. Note this is NOT the same as filtering on the fact's
  `fp` ("FY") field: also confirmed live, a 10-K's own "selected quarterly
  financial data" footnote tags each ~90-day quarter with fp="FY" too
  (fp describes the filing's overall period, not each fact's individual
  duration) — trusting fp alone would have let 90-day figures through
  labeled as annual.
- `filed` in XBRL facts is a date only, no time-of-day — coarser publication
  precision than Form 4/13F/13D-G's exact acceptance timestamps. Documented,
  not glossed over: Event.publication_time here is midnight UTC on that date.
- One us-gaap concept per category, deliberately not a list of "aliases"
  tried in order. An earlier version of this adapter tried
  PaymentsOfDividendsCommonStock before falling back to PaymentsOfDividends
  — found live, against Apple's real facts, that this was wrong on two
  counts: (1) PaymentsOfDividendsCommonStock only had 2 annual facts vs.
  PaymentsOfDividends' 34, so treating it as the preferred "alias" silently
  discarded 32 real fiscal years of data; (2) for the one period both
  report, they disagree ($11.965B vs. $12.150B) — they are not aliases for
  the same number, PaymentsOfDividends is the broader total (includes
  non-common-stock/non-controlling-interest dividends where applicable).
  Picking one automatically per company risks quietly picking whichever
  happens to exist first, not whichever is the fuller or more standard
  measure. So: one fixed, standard concept per category, full stop — a
  company whose 10-Ks only ever tag the non-standard variant simply won't
  have a DIVIDEND_PAYMENT fact here, which is conservative and honest
  rather than guessing which of two disagreeing numbers is "the" figure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter
from capint.adapters.sec_common import RateLimitedSecClient, parse_cik
from capint.models.event import EventType

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

CONCEPT_MAP: dict[EventType, str] = {
    EventType.SHARE_BUYBACK: "PaymentsForRepurchaseOfCommonStock",
    EventType.DIVIDEND_PAYMENT: "PaymentsOfDividends",
    EventType.DEBT_ISSUANCE: "ProceedsFromIssuanceOfLongTermDebt",
    EventType.DEBT_REPAYMENT: "RepaymentsOfLongTermDebt",
}

# Fundamentals (Phase 8, spec §21): income-statement concepts, unlike the
# cash-flow ones above, ARE tagged with a genuine discrete-quarter duration
# in 10-Qs (verified live against Apple's real facts) alongside the YTD
# one — so both QUARTER and FISCAL_YEAR granularity are available, neither
# derived. "revenue" has two candidate concepts because
# RevenueFromContractWithCustomerExcludingAssessedTax was ASC 606's
# replacement for Revenues circa 2018 — confirmed, for every period Apple
# reports under both tags, they agree exactly, unlike the dividends case
# in capital-allocation ingestion. Both are still merged defensively: if
# two sources ever disagree for the same period (alias conflict or a
# genuine restatement), that period is dropped rather than guessing which
# number is right — see extract_fundamental_facts.
FUNDAMENTAL_METRICS: tuple[str, ...] = ("revenue", "net_income", "eps_diluted", "gross_profit", "operating_income")
_FUNDAMENTAL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
}
_FUNDAMENTAL_UNITS: dict[str, str] = {
    "revenue": "USD",
    "net_income": "USD",
    "eps_diluted": "USD/shares",
    "gross_profit": "USD",
    "operating_income": "USD",
}


@dataclass(frozen=True)
class RawCapitalAllocationFact:
    cik: str  # 10-digit, zero-padded
    entity_name: str
    event_type: EventType
    xbrl_concept: str
    amount_usd: Decimal
    period_start: date
    period_end: date
    fiscal_year: int
    form: str
    accession: str
    filed: date


@dataclass(frozen=True)
class RawFundamentalFact:
    cik: str
    entity_name: str
    metric: str  # one of FUNDAMENTAL_METRICS
    xbrl_concept: str
    value: Decimal
    period_start: date
    period_end: date
    period_type: str  # "QUARTER" or "FISCAL_YEAR"
    fiscal_year: int | None
    fiscal_period: str | None  # raw XBRL "fp"
    form: str
    accession: str
    filed: date


class SECXBRLFactsAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
    ) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_company_facts(self, cik: str) -> dict[str, Any] | None:
        """Returns None if this CIK has no XBRL facts on file (e.g. a
        filer that has never submitted a 10-Q/10-K — plausible for a
        foreign private issuer on 20-F, or a very new registrant)."""
        url = COMPANY_FACTS_URL.format(cik=parse_cik(cik))
        try:
            resp = self._http.get(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return resp.json()

    def extract_capital_allocation_facts(self, cik: str, facts: dict[str, Any]) -> list[RawCapitalAllocationFact]:
        """Only annual-duration USD facts from 10-K filings — see module
        docstring for why quarterly cash-flow figures are deliberately not
        derived.

        `fp == "FY"` is NOT sufficient on its own to mean "this fact covers
        the full fiscal year" — found live, in Apple's real facts: a 10-K
        also tags its "selected quarterly financial data" footnote, and
        each individual quarter in that footnote is still tagged
        `fp: "FY"` (fp describes which broad period the *filing* covers,
        not this fact's own duration). The actual duration is checked
        directly instead: 330-380 days, wide enough for a 52/53-week
        fiscal calendar's 363-372-day years, narrow enough to exclude any
        ~90-day quarter.
        """
        entity_name = facts.get("entityName", "UNKNOWN")
        gaap = facts.get("facts", {}).get("us-gaap", {})

        results: list[RawCapitalAllocationFact] = []
        for event_type, concept_name in CONCEPT_MAP.items():
            if concept_name not in gaap:
                continue

            for fact in gaap[concept_name].get("units", {}).get("USD", []):
                if fact.get("form") != "10-K":
                    continue
                try:
                    amount = Decimal(str(fact["val"]))
                    period_start = date.fromisoformat(fact["start"])
                    period_end = date.fromisoformat(fact["end"])
                    filed = date.fromisoformat(fact["filed"])
                except (KeyError, InvalidOperation, ValueError):
                    continue
                if not (330 <= (period_end - period_start).days <= 380):
                    continue

                results.append(
                    RawCapitalAllocationFact(
                        cik=parse_cik(cik),
                        entity_name=entity_name,
                        event_type=event_type,
                        xbrl_concept=concept_name,
                        amount_usd=amount,
                        period_start=period_start,
                        period_end=period_end,
                        fiscal_year=fact.get("fy"),
                        form=fact["form"],
                        accession=fact["accn"],
                        filed=filed,
                    )
                )
        return results

    def extract_fundamental_facts(self, cik: str, facts: dict[str, Any]) -> list[RawFundamentalFact]:
        """Discrete-quarter (~80-100 day) and fiscal-year (~330-380 day)
        facts from 10-Q/10-K filings — YTD-cumulative durations (e.g. a
        ~181-day H1 figure) are excluded, same non-derivation principle as
        capital-allocation facts, just with real discrete-quarter data
        available here instead of needing to derive it."""
        entity_name = facts.get("entityName", "UNKNOWN")
        gaap = facts.get("facts", {}).get("us-gaap", {})

        results: list[RawFundamentalFact] = []
        for metric in FUNDAMENTAL_METRICS:
            unit = _FUNDAMENTAL_UNITS[metric]
            by_period: dict[tuple[date, date], list[tuple[Decimal, dict[str, Any], str, str]]] = {}

            for concept_name in _FUNDAMENTAL_CONCEPTS[metric]:
                if concept_name not in gaap:
                    continue
                for fact in gaap[concept_name].get("units", {}).get(unit, []):
                    if fact.get("form") not in ("10-K", "10-Q"):
                        continue
                    try:
                        value = Decimal(str(fact["val"]))
                        period_start = date.fromisoformat(fact["start"])
                        period_end = date.fromisoformat(fact["end"])
                        date.fromisoformat(fact["filed"])
                    except (KeyError, InvalidOperation, ValueError):
                        continue

                    duration = (period_end - period_start).days
                    if 80 <= duration <= 100:
                        period_type = "QUARTER"
                    elif 330 <= duration <= 380:
                        period_type = "FISCAL_YEAR"
                    else:
                        continue  # YTD or an irregular stub period — excluded

                    by_period.setdefault((period_start, period_end), []).append(
                        (value, fact, concept_name, period_type)
                    )

            for (period_start, period_end), entries in by_period.items():
                if len({v for v, _fact, _concept, _pt in entries}) > 1:
                    # Two sources disagree for this period (alias conflict or a
                    # genuine restatement) — don't guess which number is right.
                    continue
                for value, fact, concept_name, period_type in entries:
                    results.append(
                        RawFundamentalFact(
                            cik=parse_cik(cik),
                            entity_name=entity_name,
                            metric=metric,
                            xbrl_concept=concept_name,
                            value=value,
                            period_start=period_start,
                            period_end=period_end,
                            period_type=period_type,
                            fiscal_year=fact.get("fy"),
                            fiscal_period=fact.get("fp"),
                            form=fact["form"],
                            accession=fact["accn"],
                            filed=date.fromisoformat(fact["filed"]),
                        )
                    )
        return results

    def fetch_records(self, since, until) -> Iterable[dict[str, Any]]:
        """The generic SourceAdapter interface doesn't fit this adapter
        well (it has no notion of "since/until across the universe" —
        see module docstring), so this just isn't implemented. Use
        fetch_company_facts + extract_capital_allocation_facts directly
        (capint.ingestion.sec_xbrl does)."""
        raise NotImplementedError(
            "SECXBRLFactsAdapter is per-company; use fetch_company_facts + "
            "extract_capital_allocation_facts via capint.ingestion.sec_xbrl instead."
        )
