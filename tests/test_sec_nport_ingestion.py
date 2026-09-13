from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.sec_nport import SECNPortAdapter
from capint.ingestion.sec_nport import run_ingestion
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.fund import Fund, FundAumSnapshot
from tests.fixtures.sec_nport import make_test_client


def make_adapter() -> SECNPortAdapter:
    return SECNPortAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_fund_and_two_snapshots(session):
    summary = run_ingestion(session, make_adapter(), ["0000884394"], filing_count=2)

    assert summary.funds_seen == 1
    assert summary.funds_with_no_filings == 0
    assert summary.snapshots_created == 2
    assert summary.snapshots_skipped_duplicate == 0
    assert summary.fund_errors == []

    fund_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0000884394",
        )
    ).scalar_one()
    fund_entity = session.get(Entity, fund_ident.entity_id)
    assert fund_entity.entity_type == EntityType.FUND
    assert fund_entity.canonical_name == "SPDR S&P 500 ETF TRUST"

    ticker_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.entity_id == fund_entity.id,
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
        )
    ).scalar_one()
    assert ticker_ident.identifier_value == "SPY"

    fund = session.get(Fund, fund_entity.id)
    assert fund.ticker == "SPY"

    snapshots = session.execute(
        select(FundAumSnapshot).where(FundAumSnapshot.fund_entity_id == fund_entity.id).order_by(FundAumSnapshot.period_end)
    ).scalars().all()
    assert len(snapshots) == 2
    assert snapshots[0].period_end == date(2026, 3, 31)
    assert snapshots[0].net_assets_usd == Decimal("651588269947.59")
    assert snapshots[0].net_assets_change_usd is None  # no prior snapshot to compare against

    assert snapshots[1].period_end == date(2026, 6, 30)
    assert snapshots[1].net_assets_usd == Decimal("781188872106.76")
    expected_change = Decimal("781188872106.76") - Decimal("651588269947.59")
    assert snapshots[1].net_assets_change_usd == expected_change

    event = session.get(Event, snapshots[1].event_id)
    assert event.event_type == EventType.ETF_FLOW
    assert event.event_time.date() == date(2026, 6, 30)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), ["0000884394"], filing_count=2)
    assert first.snapshots_created == 2

    second = run_ingestion(session, make_adapter(), ["0000884394"], filing_count=2)
    assert second.snapshots_created == 0
    assert second.snapshots_skipped_duplicate == 2

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 2


def test_unknown_cik_counts_as_no_filings_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["0000000001"], filing_count=2)
    assert summary.funds_with_no_filings == 1
    assert summary.fund_errors == []
    assert summary.snapshots_created == 0
