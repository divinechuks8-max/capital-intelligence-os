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
from capint.models.institution import InstitutionalHolding, InstitutionalManager, InstitutionalManagerType, InstitutionalPositionStatus
from capint.models.person import Person, PersonCompanyRole
from capint.models.price import PriceBar
from capint.models.short_interest import ShortInterestSnapshot
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


def make_institution(session: Session, name: str = "Sample Capital Management, LLC (SYNTHETIC)") -> InstitutionalManager:
    entity = Entity(entity_type=EntityType.INSTITUTION, canonical_name=name)
    session.add(entity)
    session.flush()
    institution = InstitutionalManager(entity_id=entity.id, manager_type=InstitutionalManagerType.OTHER)
    session.add(institution)
    session.flush()
    return institution


def make_institutional_holding_event(
    session: Session,
    *,
    company: Company,
    institution: InstitutionalManager,
    source: Source,
    period_of_report,
    publication_time: datetime,
    shares_held: str,
    market_value_usd: str,
    shares_change: str | None,
    position_status: InstitutionalPositionStatus,
) -> Event:
    """Builds a full Event + Document + InstitutionalHolding chain,
    mirroring what a real 13F ingestion would produce."""
    document = Document(
        source_id=source.id,
        external_id="0000000000-00-000000-SYNTHETIC-13F",
        url="https://www.sec.gov/synthetic-fixture-13f",
        retrieved_at=publication_time,
    )
    session.add(document)
    session.flush()

    event_time = datetime.combine(period_of_report, datetime.min.time(), tzinfo=timezone.utc)
    event = Event(
        event_type=EventType.INSTITUTIONAL_POSITION_CHANGE,
        primary_entity_id=company.entity_id,
        event_time=event_time,
        publication_time=publication_time,
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=f"document:{document.id}",
    )
    session.add(event)
    session.flush()

    holding = InstitutionalHolding(
        event_id=event.id,
        institution_entity_id=institution.entity_id,
        company_entity_id=company.entity_id,
        period_of_report=period_of_report,
        shares_held=Decimal(shares_held),
        market_value_usd=Decimal(market_value_usd),
        shares_change=Decimal(shares_change) if shares_change is not None else None,
        position_status=position_status,
        filing_form_type="13F-HR",
    )
    session.add(holding)
    session.flush()

    return event


def make_short_interest_snapshot_event(
    session: Session,
    *,
    company: Company,
    source: Source,
    ticker: str,
    settlement_date,
    publication_time: datetime,
    current_short_position: str,
    previous_short_position: str | None,
    change_percent: str | None,
    days_to_cover: str | None,
) -> Event:
    """Builds a full Event + Document + ShortInterestSnapshot chain,
    mirroring what a real FINRA ingestion would produce."""
    document = Document(
        source_id=source.id,
        external_id=f"SYNTHETIC-FINRA-{ticker}-{settlement_date}",
        url="https://api.finra.org/synthetic-fixture-short-interest",
        retrieved_at=publication_time,
    )
    session.add(document)
    session.flush()

    event_time = datetime.combine(settlement_date, datetime.min.time(), tzinfo=timezone.utc)
    event = Event(
        event_type=EventType.SHORT_INTEREST_CHANGE,
        primary_entity_id=company.entity_id,
        event_time=event_time,
        publication_time=publication_time,
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=f"document:{document.id}",
    )
    session.add(event)
    session.flush()

    session.add(
        ShortInterestSnapshot(
            event_id=event.id,
            company_entity_id=company.entity_id,
            ticker=ticker,
            settlement_date=settlement_date,
            current_short_position=Decimal(current_short_position),
            previous_short_position=Decimal(previous_short_position) if previous_short_position is not None else None,
            change_percent=Decimal(change_percent) if change_percent is not None else None,
            days_to_cover=Decimal(days_to_cover) if days_to_cover is not None else None,
        )
    )
    session.flush()

    return event


def make_price_source(session: Session) -> Source:
    source = Source(name="Alpha Vantage (synthetic)", tier=SourceTier.B_LICENSED, base_url="https://www.alphavantage.co")
    session.add(source)
    session.flush()
    return source


def make_price_bar(
    session: Session,
    *,
    company: Company,
    source: Source,
    ticker: str,
    trade_date,
    close: str,
    open: str | None = None,
    high: str | None = None,
    low: str | None = None,
    volume: int = 1_000_000,
) -> PriceBar:
    bar = PriceBar(
        company_entity_id=company.entity_id,
        source_id=source.id,
        ticker=ticker,
        trade_date=trade_date,
        open=Decimal(open if open is not None else close),
        high=Decimal(high if high is not None else close),
        low=Decimal(low if low is not None else close),
        close=Decimal(close),
        volume=volume,
    )
    session.add(bar)
    session.flush()
    return bar
