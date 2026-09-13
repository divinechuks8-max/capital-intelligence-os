"""Entity resolution + persistence for SEC XBRL data: capital-allocation
facts (Phase 7) and fundamentals (Phase 8, spec §21).

Same adapter/ingestion split as every other source here. One extra step
this module owns that no prior ingestion module needed: canonicalizing
which filing "counts" as the disclosure of a given fiscal period's number.

Confirmed against real Apple data before writing this: a company's 10-K
re-reports the prior two fiscal years' figures as comparatives, so the
SAME (event_type, period_start, period_end) fact can appear under three
different accessions, filed years apart. Keying idempotency on accession
would create three Event rows for one real economic fact. Instead, this
module groups extracted facts by (cik, event_type, period_start,
period_end) and keeps only the one filed earliest — the filing that
*first* made that fact public, which is also the temporally correct
choice for publication_time (a restatement in a later comparative table
isn't when the fact became known).

run_ingestion fetches each company's XBRL facts once and feeds both
capital-allocation and fundamentals extraction from it — one HTTP call
covers both, since they're the same underlying company-facts document.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_xbrl import RawCapitalAllocationFact, RawFundamentalFact, SECXBRLFactsAdapter
from capint.ingestion.sec_form4 import get_or_create_company, get_or_create_source
from capint.models.capital_allocation import CapitalAllocationFact
from capint.models.event import Event, EventType
from capint.models.fundamentals import FundamentalPeriodType, FundamentalReport
from capint.models.source import Document, Source


def _raw_reference(cik: str, fact: RawCapitalAllocationFact) -> str:
    return f"sec-xbrl:{cik}#{fact.event_type.value}:{fact.period_start}:{fact.period_end}"


def canonicalize_facts(facts: list[RawCapitalAllocationFact]) -> list[RawCapitalAllocationFact]:
    """One fact per (event_type, period_start, period_end): whichever was
    filed earliest."""
    best: dict[tuple, RawCapitalAllocationFact] = {}
    for fact in facts:
        key = (fact.event_type, fact.period_start, fact.period_end)
        current = best.get(key)
        if current is None or fact.filed < current.filed:
            best[key] = fact
    return list(best.values())


def ingest_company_facts(
    session: Session, source: Source, cik: str, facts: list[RawCapitalAllocationFact]
) -> "IngestionResult":
    result = IngestionResult()
    if not facts:
        return result

    company = get_or_create_company(session, cik, facts[0].entity_name, None)

    for fact in canonicalize_facts(facts):
        ref = _raw_reference(cik, fact)
        if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
            result.skipped_duplicate += 1
            continue

        document = Document(
            source_id=source.id,
            external_id=fact.accession,
            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
            retrieved_at=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()

        event = Event(
            event_type=fact.event_type,
            primary_entity_id=company.entity_id,
            event_time=datetime.combine(fact.period_end, datetime.min.time(), tzinfo=timezone.utc),
            publication_time=datetime.combine(fact.filed, datetime.min.time(), tzinfo=timezone.utc),
            source_id=source.id,
            document_id=document.id,
            confidence=1.0,
            raw_data_reference=ref,
        )
        session.add(event)
        session.flush()

        session.add(
            CapitalAllocationFact(
                event_id=event.id,
                company_entity_id=company.entity_id,
                xbrl_concept=fact.xbrl_concept,
                amount_usd=fact.amount_usd,
                period_start=fact.period_start,
                period_end=fact.period_end,
                fiscal_year=fact.fiscal_year,
                filing_form_type=fact.form,
                filing_accession=fact.accession,
            )
        )
        result.facts_created += 1

    return result


@dataclass
class IngestionResult:
    facts_created: int = 0
    skipped_duplicate: int = 0


def _fundamentals_raw_reference(cik: str, period_type: str, period_start, period_end) -> str:
    return f"sec-xbrl-fundamentals:{cik}#{period_type}:{period_start}:{period_end}"


def canonicalize_fundamental_facts(
    facts: list[RawFundamentalFact],
) -> dict[tuple, dict[str, RawFundamentalFact]]:
    """Picks the earliest-filed fact independently PER METRIC per
    (period_start, period_end, period_type), then regroups by period.

    An earlier version picked one "canonical accession" per period and
    kept only that accession's facts — wrong, found live: a period's
    earliest-filed accession for one metric isn't guaranteed to be the
    earliest (or even present) for every metric (an old fiscal year's
    filing might tag net income but not gross profit, for instance), so
    forcing a single accession per period silently dropped real, correctly
    disclosed values instead of just their provenance being occasionally
    per-metric rather than per-filing.
    """
    best_per_metric: dict[tuple, RawFundamentalFact] = {}
    for f in facts:
        key = (f.metric, f.period_start, f.period_end, f.period_type)
        current = best_per_metric.get(key)
        if current is None or f.filed < current.filed:
            best_per_metric[key] = f

    canonical: dict[tuple, dict[str, RawFundamentalFact]] = {}
    for (metric, period_start, period_end, period_type), fact in best_per_metric.items():
        canonical.setdefault((period_start, period_end, period_type), {})[metric] = fact
    return canonical


def ingest_company_fundamentals(
    session: Session, source: Source, cik: str, facts: list[RawFundamentalFact]
) -> "IngestionResult":
    result = IngestionResult()
    if not facts:
        return result

    company = get_or_create_company(session, cik, facts[0].entity_name, None)

    for (period_start, period_end, period_type), by_metric in canonicalize_fundamental_facts(facts).items():
        ref = _fundamentals_raw_reference(cik, period_type, period_start, period_end)
        if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
            result.skipped_duplicate += 1
            continue

        # Representative fact for provenance/publication_time: the earliest-filed
        # one, since different metrics can (rarely) come from different accessions
        # (see canonicalize_fundamental_facts) — the earliest is the temporally
        # correct "when did any of this period's numbers first become public".
        any_fact = min(by_metric.values(), key=lambda f: f.filed)
        document = Document(
            source_id=source.id,
            external_id=any_fact.accession,
            url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
            retrieved_at=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()

        event = Event(
            event_type=EventType.EARNINGS,
            primary_entity_id=company.entity_id,
            event_time=datetime.combine(period_end, datetime.min.time(), tzinfo=timezone.utc),
            publication_time=datetime.combine(any_fact.filed, datetime.min.time(), tzinfo=timezone.utc),
            source_id=source.id,
            document_id=document.id,
            confidence=1.0,
            raw_data_reference=ref,
        )
        session.add(event)
        session.flush()

        revenue = by_metric.get("revenue")
        gross_profit = by_metric.get("gross_profit")
        operating_income = by_metric.get("operating_income")
        gross_margin_pct = (
            (gross_profit.value / revenue.value * 100) if gross_profit and revenue and revenue.value else None
        )
        operating_margin_pct = (
            (operating_income.value / revenue.value * 100)
            if operating_income and revenue and revenue.value
            else None
        )

        session.add(
            FundamentalReport(
                event_id=event.id,
                company_entity_id=company.entity_id,
                period_type=FundamentalPeriodType(period_type),
                period_start=period_start,
                period_end=period_end,
                fiscal_year=any_fact.fiscal_year,
                fiscal_period=any_fact.fiscal_period,
                revenue_usd=revenue.value if revenue else None,
                net_income_usd=(by_metric["net_income"].value if "net_income" in by_metric else None),
                eps_diluted=(by_metric["eps_diluted"].value if "eps_diluted" in by_metric else None),
                gross_profit_usd=gross_profit.value if gross_profit else None,
                operating_income_usd=operating_income.value if operating_income else None,
                gross_margin_pct=gross_margin_pct,
                operating_margin_pct=operating_margin_pct,
                filing_form_type=any_fact.form,
                filing_accession=any_fact.accession,
            )
        )
        result.facts_created += 1

    return result


@dataclass
class IngestionSummary:
    companies_seen: int = 0
    companies_with_no_facts: int = 0
    facts_created: int = 0
    facts_skipped_duplicate: int = 0
    fundamental_reports_created: int = 0
    fundamental_reports_skipped_duplicate: int = 0
    company_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: SECXBRLFactsAdapter, ciks: list[str]) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for cik in ciks:
        summary.companies_seen += 1
        try:
            raw_facts = adapter.fetch_company_facts(cik)
        except Exception as exc:  # noqa: BLE001 — one bad company must not abort the batch
            summary.company_errors.append(f"{cik}: {exc!r}")
            continue
        if raw_facts is None:
            summary.companies_with_no_facts += 1
            continue

        capital_allocation_facts = adapter.extract_capital_allocation_facts(cik, raw_facts)
        result = ingest_company_facts(session, source, cik, capital_allocation_facts)
        summary.facts_created += result.facts_created
        summary.facts_skipped_duplicate += result.skipped_duplicate

        fundamental_facts = adapter.extract_fundamental_facts(cik, raw_facts)
        fundamentals_result = ingest_company_fundamentals(session, source, cik, fundamental_facts)
        summary.fundamental_reports_created += fundamentals_result.facts_created
        summary.fundamental_reports_skipped_duplicate += fundamentals_result.skipped_duplicate

    session.commit()
    return summary
