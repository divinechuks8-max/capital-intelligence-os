from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.alpha_vantage import AlphaVantageAdapter
from capint.ingestion.alpha_vantage import run_ingestion
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.price import PriceBar
from tests.fixtures.alpha_vantage import KNOWN_TICKER, make_test_client


def make_adapter() -> AlphaVantageAdapter:
    return AlphaVantageAdapter(api_key="test-key-not-real", client=make_test_client(), min_request_interval=0)


def test_ingestion_creates_company_and_100_bars(session):
    summary = run_ingestion(session, make_adapter(), [KNOWN_TICKER])

    assert summary.tickers_seen == 1
    assert summary.tickers_with_no_data == 0
    assert summary.bars_created == 100
    assert summary.bars_skipped_duplicate == 0
    assert summary.ticker_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
            EntityIdentifier.identifier_value == "AAPL",
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.entity_type == EntityType.COMPANY

    bars = session.execute(
        select(PriceBar).where(PriceBar.company_entity_id == entity.id).order_by(PriceBar.trade_date)
    ).scalars().all()
    assert len(bars) == 100
    assert bars[-1].trade_date == date(2026, 9, 11)
    assert bars[-1].close == Decimal("332.2700")


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), [KNOWN_TICKER])
    assert first.bars_created == 100

    second = run_ingestion(session, make_adapter(), [KNOWN_TICKER])
    assert second.bars_created == 0
    assert second.bars_skipped_duplicate == 100


def test_ticker_price_data_shares_entity_with_short_interest_data(session):
    """A price bar and a short-interest signal for the same ticker must
    resolve to the same Company entity — required for the backtesting
    engine to join them at all."""
    from capint.ingestion.finra_short_interest import get_or_create_company_by_ticker

    pre_existing = get_or_create_company_by_ticker(session, "AAPL", "Apple Inc. Common Stock")
    session.commit()

    run_ingestion(session, make_adapter(), [KNOWN_TICKER])

    bars = session.execute(select(PriceBar)).scalars().all()
    assert len(bars) == 100
    assert all(b.company_entity_id == pre_existing.entity_id for b in bars)


def test_unknown_ticker_counts_as_no_data_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["ZZZZNOTAREALTICKERZZ"])
    assert summary.tickers_with_no_data == 1
    assert summary.ticker_errors == []
    assert summary.bars_created == 0
