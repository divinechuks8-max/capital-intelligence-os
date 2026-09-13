"""SYNTHETIC test fixtures only — no real company, person, or transaction
data. Names like "Acme Robotics" and "J. Sample" are invented and must never
be treated as real-world facts (spec §86)."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from capint.models.company import Company
from capint.models.entity import Entity, EntityType
from capint.models.event import Event, EventType
from capint.models.insider import InsiderTransaction, InsiderTransactionType
from capint.models.person import Person, PersonCompanyRole
from capint.models.source import Document, Source, SourceTier


def dt(*args, **kwargs) -> datetime:
    return datetime(*args, tzinfo=timezone.utc, **kwargs)


def make_sec_source(session: Session) -> Source:
    source = Source(name="SEC EDGAR (synthetic)", tier=SourceTier.A_OFFICIAL, base_url="https://www.sec.gov")
    session.add(source)
    session.flush()
    return source


def make_company(session: Session, name: str = "Acme Robotics, Inc. (SYNTHETIC)") -> Company:
    entity = Entity(entity_type=EntityType.COMPANY, canonical_name=name)
    session.add(entity)
    session.flush()
    company = Company(entity_id=entity.id, sector="Industrials", country="US")
    session.add(company)
    session.flush()
    return company


def make_person(session: Session, name: str = "J. Sample (SYNTHETIC)") -> Person:
    entity = Entity(entity_type=EntityType.PERSON, canonical_name=name)
    session.add(entity)
    session.flush()
    person = Person(entity_id=entity.id)
    session.add(person)
    session.flush()
    return person


def make_officer_role(session: Session, person: Person, company: Company) -> PersonCompanyRole:
    role = PersonCompanyRole(
        person_entity_id=person.entity_id,
        company_entity_id=company.entity_id,
        role_title="Chief Executive Officer",
        is_officer=True,
    )
    session.add(role)
    session.flush()
    return role


def make_insider_purchase_event(
    session: Session,
    *,
    company: Company,
    person: Person,
    source: Source,
    event_time: datetime,
    publication_time: datetime,
    retrieved_at: datetime | None = None,
    shares: str = "10000",
    price: str | None = "12.50",
    is_10b5_1_plan: bool = False,
) -> Event:
    """Builds a full Event + Document + InsiderTransaction chain, mirroring
    what a real Form 4 ingestion would produce (minus the actual parsing)."""
    document = Document(
        source_id=source.id,
        external_id="0000000000-00-000000-SYNTHETIC",
        url="https://www.sec.gov/synthetic-fixture",
        retrieved_at=retrieved_at or publication_time,
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=EventType.INSIDER_PURCHASE,
        primary_entity_id=company.entity_id,
        event_time=event_time,
        publication_time=publication_time,
        source_id=source.id,
        document_id=document.id,
        confidence=0.99,
        raw_data_reference=f"document:{document.id}",
    )
    session.add(event)
    session.flush()

    txn = InsiderTransaction(
        event_id=event.id,
        insider_entity_id=person.entity_id,
        company_entity_id=company.entity_id,
        transaction_type=InsiderTransactionType.OPEN_MARKET_PURCHASE,
        shares_transacted=Decimal(shares),
        price_per_share=Decimal(price) if price is not None else None,
        is_10b5_1_plan=is_10b5_1_plan,
        filing_form_type="Form 4",
    )
    session.add(txn)
    session.flush()

    return event
