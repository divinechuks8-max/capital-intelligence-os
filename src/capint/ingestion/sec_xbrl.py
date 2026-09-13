"""Entity resolution + persistence for SEC XBRL capital-allocation facts
(Phase 7).

Same adapter/ingestion split as every other source here. One extra step
this module owns that no prior ingestion module needed: canonicalizing
which filing "counts" as the disclosure of a given fiscal year's number.

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
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_xbrl import RawCapitalAllocationFact, SECXBRLFactsAdapter
from capint.ingestion.sec_form4 import get_or_create_company, get_or_create_source
from capint.models.capital_allocation import CapitalAllocationFact
from capint.models.event import Event
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


@dataclass
class IngestionSummary:
    companies_seen: int = 0
    companies_with_no_facts: int = 0
    facts_created: int = 0
    facts_skipped_duplicate: int = 0
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

        facts = adapter.extract_capital_allocation_facts(cik, raw_facts)
        result = ingest_company_facts(session, source, cik, facts)
        summary.facts_created += result.facts_created
        summary.facts_skipped_duplicate += result.skipped_duplicate

    session.commit()
    return summary
