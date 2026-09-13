"""Entity resolution + persistence for guidance-relevant 8-K disclosures
(Phase 12). Reuses capint.ingestion.sec_form4's get_or_create_company and
get_or_create_source directly — same "SEC EDGAR" Source row and the same
CIK-based Company resolution every other SEC adapter here uses, no
adapter-specific entity resolution needed for this one."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_guidance import RawGuidanceDisclosure, SECGuidanceDisclosureAdapter
from capint.ingestion.sec_form4 import get_or_create_company, get_or_create_source
from capint.models.event import Event, EventType
from capint.models.guidance import GuidanceDisclosure
from capint.models.source import Document, Source


def _raw_reference(accession: str) -> str:
    return f"sec-8k-item:{accession}"


def ingest_guidance_disclosure(session: Session, source: Source, fact: RawGuidanceDisclosure) -> bool:
    """Returns False if this filing was already ingested (idempotent no-op)."""
    ref = _raw_reference(fact.accession_number)
    if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
        return False

    company = get_or_create_company(session, fact.cik, fact.company_name, ticker=None)

    document = Document(
        source_id=source.id,
        external_id=fact.accession_number,
        url=fact.primary_document_url
        or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={fact.cik}&type=8-K",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=EventType.GUIDANCE_CHANGE,
        primary_entity_id=company.entity_id,
        event_time=datetime.combine(fact.filing_date, datetime.min.time(), tzinfo=timezone.utc),
        publication_time=fact.filed_at,
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=ref,
    )
    session.add(event)
    session.flush()

    session.add(
        GuidanceDisclosure(
            event_id=event.id,
            company_entity_id=company.entity_id,
            item_codes=fact.item_codes,
            filing_form_type="8-K",
            filing_accession=fact.accession_number,
            primary_document_url=fact.primary_document_url,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    companies_seen: int = 0
    companies_with_no_disclosures: int = 0
    disclosures_created: int = 0
    disclosures_skipped_duplicate: int = 0
    company_errors: list[str] = field(default_factory=list)


def run_ingestion(
    session: Session, adapter: SECGuidanceDisclosureAdapter, ciks: list[str], filing_count: int = 20
) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for cik in ciks:
        summary.companies_seen += 1
        try:
            facts = adapter.fetch_recent_guidance_disclosures(cik, limit=filing_count)
        except Exception as exc:  # noqa: BLE001 — one bad company must not abort the batch
            summary.company_errors.append(f"{cik}: {exc!r}")
            continue

        if not facts:
            summary.companies_with_no_disclosures += 1
            continue

        for fact in facts:
            created = ingest_guidance_disclosure(session, source, fact)
            if created:
                summary.disclosures_created += 1
            else:
                summary.disclosures_skipped_duplicate += 1

    session.commit()
    return summary
