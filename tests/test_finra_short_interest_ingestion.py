from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.finra_short_interest import FINRAShortInterestAdapter
from capint.ingestion.finra_short_interest import run_ingestion
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.short_interest import ShortInterestSnapshot
from tests.fixtures.finra_short_interest import make_test_client

AS_OF = date(2026, 9, 13)


def make_adapter() -> FINRAShortInterestAdapter:
    return FINRAShortInterestAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_company_and_three_snapshots(session):
    summary = run_ingestion(session, make_adapter(), ["AAPL"], num_cycles=3, as_of=AS_OF)

    assert summary.tickers_seen == 1
    assert summary.settlement_cycles_seen == 3
    assert summary.snapshots_created == 3
    assert summary.snapshots_skipped_duplicate == 0
    assert summary.snapshots_not_reported == 0
    assert summary.ticker_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
            EntityIdentifier.identifier_value == "AAPL",
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.entity_type == EntityType.COMPANY
    assert entity.canonical_name == "Apple Inc. Common Stock"

    snapshots = session.execute(
        select(ShortInterestSnapshot)
        .where(ShortInterestSnapshot.company_entity_id == entity.id)
        .order_by(ShortInterestSnapshot.settlement_date)
    ).scalars().all()
    assert len(snapshots) == 3
    assert [s.settlement_date for s in snapshots] == [date(2026, 7, 31), date(2026, 8, 14), date(2026, 8, 31)]

    latest = snapshots[-1]
    assert latest.current_short_position == Decimal("139749097")
    assert latest.previous_short_position == Decimal("116327753")
    assert latest.change_percent == Decimal("20.13")
    assert latest.days_to_cover == Decimal("3.53")

    event = session.get(Event, latest.event_id)
    assert event.event_type == EventType.SHORT_INTEREST_CHANGE
    assert event.event_time.date() == date(2026, 8, 31)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), ["AAPL"], num_cycles=3, as_of=AS_OF)
    assert first.snapshots_created == 3

    second = run_ingestion(session, make_adapter(), ["AAPL"], num_cycles=3, as_of=AS_OF)
    assert second.snapshots_created == 0
    assert second.snapshots_skipped_duplicate == 3

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 3


def test_unknown_ticker_counts_as_not_reported_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["ZZZZNOTAREALTICKERZZ"], num_cycles=3, as_of=AS_OF)
    assert summary.snapshots_not_reported == 3
    assert summary.ticker_errors == []
    assert summary.snapshots_created == 0


def test_existing_cik_resolved_entity_is_reused_by_ticker(session):
    """A company already resolved via a CIK-based adapter (Form 4, 13F,
    13D/13G) that also stamped a TICKER identifier must be reused here,
    not duplicated into a second bare Entity for the same real company."""
    entity = Entity(entity_type=EntityType.COMPANY, canonical_name="Apple Inc.")
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value="0000320193", is_primary=True)
    )
    session.add(EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.TICKER, identifier_value="AAPL"))
    from capint.models.company import Company

    session.add(Company(entity_id=entity.id))
    session.commit()

    summary = run_ingestion(session, make_adapter(), ["AAPL"], num_cycles=1, as_of=AS_OF)
    assert summary.snapshots_created == 1

    snapshot = session.execute(select(ShortInterestSnapshot)).scalar_one()
    assert snapshot.company_entity_id == entity.id

    all_entities_named_apple = session.execute(
        select(Entity).where(Entity.canonical_name.in_(["Apple Inc.", "Apple Inc. Common Stock"]))
    ).scalars().all()
    assert len(all_entities_named_apple) == 1
