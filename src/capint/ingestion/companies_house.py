"""Entity resolution + persistence for Companies House data (Phase 11).

Same adapter/ingestion split as every other source. Two distinct entity-
resolution paths for a PSC record, depending on its `kind`:

- **Corporate PSC registered in the UK** (kind="corporate-entity-person-
  with-significant-control" with a real UK `registration_number`):
  resolved through `get_or_create_uk_company`, the same path used for any
  UK company ingested as an issuer in its own right — a genuine identity
  win, since Companies House gives a real stable cross-company number here.
- **Everything else** (individuals, corporate/legal-person PSCs with no
  UK registration number): Companies House gives no identifier that is
  stable *across* companies — each PSC's `links.self` path is scoped to
  one (company, PSC) relationship only. So a bare Entity is created per
  (company, psc_link), keyed by an ALIAS identifier on that scoped link.
  The same real individual serving as PSC of two different UK companies
  will therefore resolve to two different Entity rows here — a real,
  documented limitation (capint.models.uk_psc's docstring), not an
  oversight: inferring a same-person match from name/DOB alone without a
  stable identifier would be exactly the kind of unverified identity
  fabrication this project's spec prohibits.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.companies_house import CompaniesHouseAdapter, RawUKPersonWithSignificantControl
from capint.models.company import Company
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.person import Person
from capint.models.source import Document, Source, SourceTier
from capint.models.uk_psc import UKPersonWithSignificantControl

SOURCE_NAME = "Companies House"


def _raw_reference(psc_link: str) -> str:
    return f"companies-house-psc:{psc_link}"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(
        name=SOURCE_NAME,
        tier=SourceTier.A_OFFICIAL,
        base_url="https://api.company-information.service.gov.uk",
    )
    session.add(source)
    session.flush()
    return source


def get_or_create_uk_company(session: Session, company_number: str, name: str) -> Company:
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.UK_COMPANY_NUMBER,
            EntityIdentifier.identifier_value == company_number,
        )
    ).scalars().first()
    if ident is not None:
        existing = session.get(Company, ident.entity_id)
        if existing is not None:
            return existing
        company = Company(entity_id=ident.entity_id, country="GB")
        session.add(company)
        session.flush()
        return company

    entity = Entity(entity_type=EntityType.COMPANY, canonical_name=name)
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(
            entity_id=entity.id,
            identifier_type=IdentifierType.UK_COMPANY_NUMBER,
            identifier_value=company_number,
            is_primary=True,
        )
    )
    company = Company(entity_id=entity.id, country="GB")
    session.add(company)
    session.flush()
    return company


def get_or_create_psc_entity(session: Session, fact: RawUKPersonWithSignificantControl) -> Entity:
    """See this module's docstring for why a corporate PSC with a real UK
    registration number resolves through the Company path (genuine
    cross-company identity), while everything else is scoped to this one
    PSC relationship only."""
    if fact.kind == "corporate-entity-person-with-significant-control" and fact.identification_registration_number:
        company = get_or_create_uk_company(session, fact.identification_registration_number, fact.name)
        return session.get(Entity, company.entity_id)

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.ALIAS,
            EntityIdentifier.identifier_value == fact.psc_link,
        )
    ).scalars().first()
    if ident is not None:
        entity = session.get(Entity, ident.entity_id)
        if entity is not None:
            return entity

    entity_type = EntityType.PERSON if fact.kind == "individual-person-with-significant-control" else EntityType.OTHER
    entity = Entity(entity_type=entity_type, canonical_name=fact.name)
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.ALIAS, identifier_value=fact.psc_link, is_primary=True)
    )
    if entity_type == EntityType.PERSON:
        session.add(Person(entity_id=entity.id))
    session.flush()
    return entity


def ingest_psc_record(
    session: Session, source: Source, company: Company, fact: RawUKPersonWithSignificantControl
) -> bool:
    """Returns False if this PSC relationship was already ingested (idempotent no-op)."""
    ref = _raw_reference(fact.psc_link)
    if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
        return False

    psc_entity = get_or_create_psc_entity(session, fact)

    document = Document(
        source_id=source.id,
        external_id=fact.psc_link,
        url=f"https://find-and-update.company-information.service.gov.uk/company/{fact.company_number}/persons-with-significant-control",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=EventType.MAJOR_HOLDER_CHANGE,
        primary_entity_id=company.entity_id,
        event_time=datetime.combine(fact.notified_on, datetime.min.time(), tzinfo=timezone.utc),
        publication_time=datetime.combine(fact.notified_on, datetime.min.time(), tzinfo=timezone.utc),
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=ref,
    )
    session.add(event)
    session.flush()

    session.add(
        UKPersonWithSignificantControl(
            event_id=event.id,
            company_entity_id=company.entity_id,
            psc_entity_id=psc_entity.id,
            psc_name=fact.name,
            psc_kind=fact.kind,
            natures_of_control=fact.natures_of_control,
            notified_on=fact.notified_on,
            ceased_on=fact.ceased_on,
            country_of_residence=fact.country_of_residence,
            nationality=fact.nationality,
            companies_house_psc_link=fact.psc_link,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    companies_seen: int = 0
    psc_records_created: int = 0
    psc_records_skipped_duplicate: int = 0
    companies_with_no_psc: int = 0
    company_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: CompaniesHouseAdapter, company_numbers: list[str]) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for company_number in company_numbers:
        summary.companies_seen += 1
        try:
            profile = adapter.fetch_company_profile(company_number)
            if profile is None:
                summary.company_errors.append(f"{company_number}: no such company")
                continue
            company = get_or_create_uk_company(session, profile.company_number, profile.name)
            facts = adapter.fetch_psc_register(company_number)
        except Exception as exc:  # noqa: BLE001 — one bad company must not abort the batch
            summary.company_errors.append(f"{company_number}: {exc!r}")
            continue

        if not facts:
            summary.companies_with_no_psc += 1
            continue

        for fact in facts:
            created = ingest_psc_record(session, source, company, fact)
            if created:
                summary.psc_records_created += 1
            else:
                summary.psc_records_skipped_duplicate += 1

    session.commit()
    return summary
