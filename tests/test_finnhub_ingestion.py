from datetime import date

from sqlalchemy import select

from capint.adapters.finnhub import FinnhubAdapter
from capint.ingestion.finnhub import run_ingestion
from capint.models.analyst import AnalystRecommendationTrend
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from tests.fixtures.finnhub import KNOWN_TICKER, make_test_client


def make_adapter() -> FinnhubAdapter:
    return FinnhubAdapter(api_key="test-key-not-real", client=make_test_client(), min_request_interval=0)


def test_ingestion_creates_company_and_four_trends(session):
    summary = run_ingestion(session, make_adapter(), [KNOWN_TICKER])

    assert summary.tickers_seen == 1
    assert summary.tickers_with_no_data == 0
    assert summary.trends_created == 4
    assert summary.trends_skipped_duplicate == 0
    assert summary.ticker_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
            EntityIdentifier.identifier_value == "AAPL",
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.entity_type == EntityType.COMPANY

    trends = session.execute(
        select(AnalystRecommendationTrend)
        .where(AnalystRecommendationTrend.company_entity_id == entity.id)
        .order_by(AnalystRecommendationTrend.period)
    ).scalars().all()
    assert len(trends) == 4
    assert trends[-1].period == date(2026, 9, 1)
    assert trends[-1].strong_buy == 12


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), [KNOWN_TICKER])
    assert first.trends_created == 4

    second = run_ingestion(session, make_adapter(), [KNOWN_TICKER])
    assert second.trends_created == 0
    assert second.trends_skipped_duplicate == 4


def test_ticker_shares_entity_with_short_interest_and_price_data(session):
    """An analyst-recommendation trend and a short-interest/price signal
    for the same ticker must resolve to the same Company entity."""
    from capint.ingestion.finra_short_interest import get_or_create_company_by_ticker

    pre_existing = get_or_create_company_by_ticker(session, "AAPL", "Apple Inc. Common Stock")
    session.commit()

    run_ingestion(session, make_adapter(), [KNOWN_TICKER])

    trends = session.execute(select(AnalystRecommendationTrend)).scalars().all()
    assert len(trends) == 4
    assert all(t.company_entity_id == pre_existing.entity_id for t in trends)


def test_unknown_ticker_counts_as_no_data_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["ZZZZNOTAREALTICKERZZ"])
    assert summary.tickers_with_no_data == 1
    assert summary.ticker_errors == []
    assert summary.trends_created == 0
