from decimal import Decimal

from sqlalchemy import select

from capint.adapters.sec_edgar import SECEdgarForm4Adapter
from capint.ingestion.sec_form4 import get_or_create_person, run_ingestion
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.insider import InsiderTransaction, InsiderTransactionType
from capint.models.person import PersonCompanyRole
from tests.fixtures.sec_form4 import make_test_client


def make_adapter() -> SECEdgarForm4Adapter:
    return SECEdgarForm4Adapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_full_entity_and_event_graph(session):
    summary = run_ingestion(session, make_adapter(), filing_count=10)

    assert summary.filings_seen == 1
    assert summary.transactions_created == 1
    assert summary.transactions_skipped_duplicate == 0
    assert summary.derivative_transactions_skipped == 0
    assert summary.filing_errors == []

    company_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0001824920",
        )
    ).scalar_one()
    company_entity = session.get(Entity, company_ident.entity_id)
    assert company_entity.entity_type == EntityType.COMPANY
    assert company_entity.canonical_name == "IonQ, Inc."

    ticker_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.entity_id == company_entity.id,
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
        )
    ).scalar_one()
    assert ticker_ident.identifier_value == "IONQ"

    person_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0002058452",
        )
    ).scalar_one()
    person_entity = session.get(Entity, person_ident.entity_id)
    assert person_entity.canonical_name == "Raymond John w"

    role = session.execute(
        select(PersonCompanyRole).where(
            PersonCompanyRole.person_entity_id == person_entity.id,
            PersonCompanyRole.company_entity_id == company_entity.id,
        )
    ).scalar_one()
    assert role.is_director is True
    assert role.is_officer is False

    event = session.execute(select(Event).where(Event.primary_entity_id == company_entity.id)).scalar_one()
    assert event.event_type == EventType.INSIDER_SALE  # acquired_disposed_code == "D"
    assert event.confidence == 1.0
    assert event.raw_data_reference == "sec-form4:0001193125-26-389607#0"

    txn = session.execute(
        select(InsiderTransaction).where(InsiderTransaction.event_id == event.id)
    ).scalar_one()
    assert txn.transaction_type == InsiderTransactionType.OPEN_MARKET_SALE  # code "S"
    assert txn.shares_transacted == Decimal("2407")
    assert txn.price_per_share == Decimal("37.21")
    assert txn.is_10b5_1_plan is True
    assert txn.filing_form_type == "4"


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), filing_count=10)
    assert first.transactions_created == 1

    second = run_ingestion(session, make_adapter(), filing_count=10)
    assert second.transactions_created == 0
    assert second.transactions_skipped_duplicate == 1

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 1  # no duplicate row was created


def test_get_or_create_person_attaches_profile_to_pre_existing_bare_entity(session):
    """Regression test: found live during Phase 6 validation. If some
    other adapter (capint.ingestion.sec_13dg, in practice) already
    resolved a CIK to a bare Entity(type=OTHER) with no Person profile —
    e.g. because it first saw that CIK as a non-individual reporting
    person — get_or_create_person must attach a Person profile to that
    SAME entity rather than creating a second Entity/EntityIdentifier for
    the same CIK. Before the fix, this raised MultipleResultsFound on the
    very next CIK lookup."""
    entity = Entity(entity_type=EntityType.OTHER, canonical_name="Some Trust (SYNTHETIC)")
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value="0009999999", is_primary=True)
    )
    session.flush()

    person = get_or_create_person(session, "0009999999", "Some Trust (SYNTHETIC)")
    session.commit()

    assert person.entity_id == entity.id
    session.refresh(entity)
    assert entity.entity_type == EntityType.PERSON

    identifiers = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0009999999",
        )
    ).scalars().all()
    assert len(identifiers) == 1  # no duplicate identifier row

    # A second call must not raise MultipleResultsFound and must return the same person.
    again = get_or_create_person(session, "0009999999", "Some Trust (SYNTHETIC)")
    assert again.entity_id == entity.id
