"""Entity resolution + persistence for SEC Form 4 data (Phase 2).

Deliberately separate from capint.adapters.sec_edgar (which only fetches
and parses): this module is where raw records become graph-connected rows
— resolving issuer/owner CIKs to canonical Entity rows (creating them on
first sight), and writing Source/Document/Event/InsiderTransaction with an
idempotency key so re-running ingestion never double-counts a transaction.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_edgar import FilingRef, RawForm4Transaction, SECEdgarForm4Adapter
from capint.models.company import Company
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.insider import InsiderTransaction, InsiderTransactionType
from capint.models.person import Person, PersonCompanyRole
from capint.models.source import Document, Source, SourceTier

SOURCE_NAME = "SEC EDGAR"

# SEC Section 16(a) transaction codes (Form 4/5 instructions, Table I).
# Deliberately not collapsed to BUY/SELL — see spec §11.
TRANSACTION_CODE_MAP: dict[str, InsiderTransactionType] = {
    "P": InsiderTransactionType.OPEN_MARKET_PURCHASE,
    "S": InsiderTransactionType.OPEN_MARKET_SALE,
    "A": InsiderTransactionType.COMPENSATION_AWARD,
    "F": InsiderTransactionType.TAX_WITHHOLDING,
    "M": InsiderTransactionType.OPTION_EXERCISE,
    "X": InsiderTransactionType.OPTION_EXERCISE,
    "C": InsiderTransactionType.CONVERSION,
    "G": InsiderTransactionType.GIFT,
    "W": InsiderTransactionType.TRANSFER,
    "Z": InsiderTransactionType.TRANSFER,
}


def _raw_reference(txn: RawForm4Transaction) -> str:
    return f"sec-form4:{txn.accession_number}#{txn.transaction_index}"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(name=SOURCE_NAME, tier=SourceTier.A_OFFICIAL, base_url="https://www.sec.gov")
    session.add(source)
    session.flush()
    return source


def get_or_create_company(session: Session, cik: str, name: str, ticker: str | None) -> Company:
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == cik,
        )
    ).scalar_one_or_none()
    if ident is not None:
        return session.get(Company, ident.entity_id) or _attach_company_profile(session, ident.entity_id, name)

    entity = Entity(entity_type=EntityType.COMPANY, canonical_name=name)
    session.add(entity)
    session.flush()
    session.add(EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value=cik, is_primary=True))
    if ticker:
        session.add(
            EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.TICKER, identifier_value=ticker)
        )
    company = Company(entity_id=entity.id)
    session.add(company)
    session.flush()
    return company


def _attach_company_profile(session: Session, entity_id, name: str) -> Company:
    """An Entity/CIK identifier can predate a Company profile row (e.g. if a
    future institutional-ownership adapter creates the Entity first)."""
    company = Company(entity_id=entity_id)
    session.add(company)
    session.flush()
    return company


def get_or_create_person(session: Session, cik: str, name: str) -> Person:
    """Resolves by CIK across every adapter that identifies people this way
    (Form 4 owners, Schedule 13D/13G individual reporting persons, ...).

    That cross-adapter reuse is exactly why the ident-exists-but-no-Person
    branch below matters: capint.ingestion.sec_13dg can first create a
    bare Entity(type=OTHER) for a CIK it saw as a non-individual reporting
    person (or one whose type code it couldn't parse), then later see the
    same CIK unambiguously marked "IN" (individual). Without
    _attach_person_profile, that second call would fall through to
    "create a new Entity", producing a second EntityIdentifier row for the
    same CIK — found the hard way, live, when that happened for real
    during Phase 6 validation and the next lookup raised
    MultipleResultsFound.
    """
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == cik,
        )
    ).scalar_one_or_none()
    if ident is not None:
        return session.get(Person, ident.entity_id) or _attach_person_profile(session, ident.entity_id)

    entity = Entity(entity_type=EntityType.PERSON, canonical_name=name)
    session.add(entity)
    session.flush()
    session.add(EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value=cik, is_primary=True))
    person = Person(entity_id=entity.id)
    session.add(person)
    session.flush()
    return person


def _attach_person_profile(session: Session, entity_id) -> Person:
    """An Entity/CIK identifier can predate a Person profile row — e.g. a
    Schedule 13D/13G reporting person first resolved as a bare
    Entity(type=OTHER) under this CIK, now confirmed to actually be an
    individual. Corrects entity_type too: whatever created the row first
    was wrong about what kind of entity this CIK identifies."""
    entity = session.get(Entity, entity_id)
    if entity is not None and entity.entity_type != EntityType.PERSON:
        entity.entity_type = EntityType.PERSON
    person = Person(entity_id=entity_id)
    session.add(person)
    session.flush()
    return person


def upsert_person_company_role(
    session: Session,
    person: Person,
    company: Company,
    *,
    is_officer: bool,
    is_director: bool,
    is_ten_percent_owner: bool,
    role_title: str | None,
) -> PersonCompanyRole:
    role = session.execute(
        select(PersonCompanyRole).where(
            PersonCompanyRole.person_entity_id == person.entity_id,
            PersonCompanyRole.company_entity_id == company.entity_id,
        )
    ).scalar_one_or_none()
    if role is None:
        role = PersonCompanyRole(person_entity_id=person.entity_id, company_entity_id=company.entity_id)
        session.add(role)
    role.is_officer = is_officer
    role.is_director = is_director
    role.is_ten_percent_owner = is_ten_percent_owner
    if role_title:
        role.role_title = role_title
    session.flush()
    return role


def _event_type_for(txn: RawForm4Transaction) -> EventType:
    return EventType.INSIDER_PURCHASE if txn.acquired_disposed_code == "A" else EventType.INSIDER_SALE


def ingest_transaction(session: Session, source: Source, txn: RawForm4Transaction) -> Event | None:
    """Persists one transaction. Returns None (no-op) if already ingested."""
    ref = _raw_reference(txn)
    existing = session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none()
    if existing is not None:
        return None

    company = get_or_create_company(session, txn.issuer_cik, txn.issuer_name, txn.issuer_ticker)
    person = get_or_create_person(session, txn.owner_cik, txn.owner_name)
    upsert_person_company_role(
        session,
        person,
        company,
        is_officer=txn.is_officer,
        is_director=txn.is_director,
        is_ten_percent_owner=txn.is_ten_percent_owner,
        role_title=txn.officer_title,
    )

    document = Document(
        source_id=source.id,
        external_id=txn.accession_number,
        url=txn.filing_url,
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=_event_type_for(txn),
        primary_entity_id=company.entity_id,
        event_time=datetime.combine(txn.transaction_date, datetime.min.time(), tzinfo=timezone.utc),
        publication_time=txn.published_at,
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=ref,
    )
    session.add(event)
    session.flush()

    session.add(
        InsiderTransaction(
            event_id=event.id,
            insider_entity_id=person.entity_id,
            company_entity_id=company.entity_id,
            transaction_type=TRANSACTION_CODE_MAP.get(txn.transaction_code, InsiderTransactionType.OTHER),
            shares_transacted=txn.shares_transacted,
            price_per_share=txn.price_per_share,
            shares_owned_after=txn.shares_owned_after,
            is_10b5_1_plan=txn.is_10b5_1_plan,
            filing_form_type=txn.document_type,
        )
    )
    session.flush()
    return event


@dataclass
class IngestionSummary:
    filings_seen: int = 0
    transactions_created: int = 0
    transactions_skipped_duplicate: int = 0
    derivative_transactions_skipped: int = 0
    filing_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: SECEdgarForm4Adapter, filing_count: int = 100) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    filings: list[FilingRef] = adapter.fetch_current_form4_filings(count=filing_count)
    summary.filings_seen = len(filings)

    for filing in filings:
        try:
            transactions, derivative_skipped = adapter.fetch_transactions_for_filing(filing)
        except Exception as exc:  # noqa: BLE001 — one bad filing must not abort the batch
            summary.filing_errors.append(f"{filing.accession_number}: {exc!r}")
            continue

        summary.derivative_transactions_skipped += derivative_skipped
        for txn in transactions:
            event = ingest_transaction(session, source, txn)
            if event is None:
                summary.transactions_skipped_duplicate += 1
            else:
                summary.transactions_created += 1

    session.commit()
    return summary
