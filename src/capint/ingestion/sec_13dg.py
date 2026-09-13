"""Entity resolution + persistence for SEC Schedule 13D/13G data (Phase 6).

Same adapter/ingestion split as every other source in capint.ingestion.
Reuses Phase 2's CIK-based get_or_create_company/get_or_create_person
directly (both 13D and 13G reliably give an issuer CIK, and an individual
reporting person's CIK is the same SEC-wide identifier Form 4 uses) — this
is one case where entity resolution DOES cross-link cleanly across signal
families, unlike Phase 4's 13F-vs-Form-4 CUSIP/CIK mismatch (see
capint.ingestion.sec_13f's module docstring for that gap).

A reporting person that is not an individual (a trust, holding company,
partnership — common in joint 13D filings) never gets a Person profile
forced onto it; it's resolved as a bare Entity(type=OTHER), by CIK when one
is given, otherwise by exact name match (best-effort, no identifier —
documented as coarse in capint.adapters.sec_13dg's module docstring).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_13dg import RawReportingPerson, RawScheduleFiling, SEC13DGAdapter, SCHEDULE_13D, SCHEDULE_13G
from capint.ingestion.sec_form4 import get_or_create_company, get_or_create_person, get_or_create_source
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.ownership import BeneficialOwnershipDisclosure, ScheduleType
from capint.models.source import Document, Source


def _raw_reference(accession: str, index: int) -> str:
    return f"sec-13dg:{accession}#{index}"


def _get_or_create_filer_entity(session: Session, person: RawReportingPerson) -> Entity:
    is_individual = (person.type_code or "").upper() == "IN"

    if person.cik:
        if is_individual:
            return get_or_create_person(session, person.cik, person.name).entity

        ident = session.execute(
            select(EntityIdentifier).where(
                EntityIdentifier.identifier_type == IdentifierType.CIK,
                EntityIdentifier.identifier_value == person.cik,
            )
        ).scalar_one_or_none()
        if ident is not None:
            existing = session.get(Entity, ident.entity_id)
            if existing is not None:
                return existing

        entity = Entity(entity_type=EntityType.OTHER, canonical_name=person.name)
        session.add(entity)
        session.flush()
        session.add(
            EntityIdentifier(
                entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value=person.cik, is_primary=True
            )
        )
        return entity

    # No CIK at all (e.g. a trust) — best-effort dedup by exact name, no identifier.
    existing = session.execute(
        select(Entity).where(Entity.entity_type == EntityType.OTHER, Entity.canonical_name == person.name)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    entity = Entity(entity_type=EntityType.OTHER, canonical_name=person.name)
    session.add(entity)
    session.flush()
    return entity


def ingest_filing(session: Session, source: Source, filing: RawScheduleFiling) -> "FilingIngestionResult":
    result = FilingIngestionResult()
    schedule_type = ScheduleType.SCHEDULE_13D if filing.form_type == SCHEDULE_13D else ScheduleType.SCHEDULE_13G
    event_type = EventType.ACTIVIST_STAKE if schedule_type == ScheduleType.SCHEDULE_13D else EventType.MAJOR_HOLDER_CHANGE
    is_joint = len(filing.reporting_persons) > 1

    company = get_or_create_company(session, filing.issuer_cik, filing.issuer_name, None)
    event_time = (
        datetime.combine(filing.event_date, datetime.min.time(), tzinfo=timezone.utc) if filing.event_date else None
    )

    for index, person in enumerate(filing.reporting_persons):
        ref = _raw_reference(filing.accession_number, index)
        if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
            result.skipped_duplicate += 1
            continue

        filer_entity = _get_or_create_filer_entity(session, person)

        document = Document(
            source_id=source.id,
            external_id=filing.accession_number,
            url=filing.filing_url,
            retrieved_at=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()

        event = Event(
            event_type=event_type,
            primary_entity_id=company.entity_id,
            event_time=event_time,
            publication_time=filing.published_at,
            source_id=source.id,
            document_id=document.id,
            confidence=1.0,
            raw_data_reference=ref,
        )
        session.add(event)
        session.flush()

        session.add(
            BeneficialOwnershipDisclosure(
                event_id=event.id,
                filer_entity_id=filer_entity.id,
                company_entity_id=company.entity_id,
                schedule_type=schedule_type,
                filer_type_code=person.type_code,
                shares_beneficially_owned=person.shares_owned,
                percent_of_class=person.percent_of_class,
                sole_voting_power=person.sole_voting_power,
                shared_voting_power=person.shared_voting_power,
                sole_dispositive_power=person.sole_dispositive_power,
                shared_dispositive_power=person.shared_dispositive_power,
                event_date=filing.event_date,
                stated_purpose=filing.stated_purpose,
                is_joint_filing=is_joint,
                filing_form_type=filing.form_type,
            )
        )
        result.disclosures_created += 1

    return result


@dataclass
class FilingIngestionResult:
    disclosures_created: int = 0
    skipped_duplicate: int = 0


@dataclass
class IngestionSummary:
    filings_seen: int = 0
    disclosures_created: int = 0
    disclosures_skipped_duplicate: int = 0
    filing_errors: list[str] = field(default_factory=list)


def run_ingestion(
    session: Session, adapter: SEC13DGAdapter, since, until, limit: int = 100
) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for form_type in (SCHEDULE_13D, SCHEDULE_13G):
        leads = adapter.fetch_recent_filings(form_type, since, until, limit=limit)
        summary.filings_seen += len(leads)

        for lead in leads:
            try:
                filing = adapter.fetch_filing(lead)
            except Exception as exc:  # noqa: BLE001 — one bad filing must not abort the batch
                summary.filing_errors.append(f"{lead.accession_number}: {exc!r}")
                continue
            if filing is None:
                summary.filing_errors.append(f"{lead.accession_number}: could not parse expected documents")
                continue

            result = ingest_filing(session, source, filing)
            summary.disclosures_created += result.disclosures_created
            summary.disclosures_skipped_duplicate += result.skipped_duplicate

    session.commit()
    return summary
