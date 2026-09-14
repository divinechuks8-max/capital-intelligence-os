"""Entity resolution + persistence for M&A-relevant 8-K disclosures
(Phase 14). Reuses capint.ingestion.sec_form4's get_or_create_company and
get_or_create_source directly, same as capint.ingestion.sec_guidance."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_corporate_actions import RawCorporateActionDisclosure, SECCorporateActionAdapter
from capint.ingestion.sec_form4 import get_or_create_company, get_or_create_source
from capint.models.corporate_action import CorporateActionDisclosure
from capint.models.event import Event, EventType
from capint.models.source import Document, Source


def _raw_reference(accession: str) -> str:
    # Deliberately NOT the same format as capint.ingestion.sec_guidance's
    # "sec-8k-item:{accession}" — Event.raw_data_reference carries a
    # database-level UNIQUE constraint, and a single real 8-K can validly
    # carry both a guidance-relevant item (2.02/7.01) and the M&A item
    # (2.01) in the same `items` string, so both ingesters can legitimately
    # want to create their own Event for the same accession. A shared
    # prefix would collide on that constraint the first time a real
    # filing hit both categories at once; a distinct prefix per category
    # avoids that entirely.
    return f"sec-8k-item-corpaction:{accession}"


def ingest_corporate_action_disclosure(session: Session, source: Source, fact: RawCorporateActionDisclosure) -> bool:
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
        event_type=EventType.M_AND_A,
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
        CorporateActionDisclosure(
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
    session: Session, adapter: SECCorporateActionAdapter, ciks: list[str], filing_count: int = 20
) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for cik in ciks:
        summary.companies_seen += 1
        try:
            facts = adapter.fetch_recent_corporate_actions(cik, limit=filing_count)
        except Exception as exc:  # noqa: BLE001 — one bad company must not abort the batch
            summary.company_errors.append(f"{cik}: {exc!r}")
            continue

        if not facts:
            summary.companies_with_no_disclosures += 1
            continue

        for fact in facts:
            created = ingest_corporate_action_disclosure(session, source, fact)
            if created:
                summary.disclosures_created += 1
            else:
                summary.disclosures_skipped_duplicate += 1

    session.commit()
    return summary
