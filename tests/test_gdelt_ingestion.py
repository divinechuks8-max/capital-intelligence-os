from decimal import Decimal

from sqlalchemy import select

from capint.adapters.gdelt import GDELTAdapter
from capint.ingestion.gdelt import ingest_news_sentiment
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.news_sentiment import NewsSentimentSnapshot
from tests.fixtures.gdelt import KNOWN_QUERY, make_test_client


def make_adapter() -> GDELTAdapter:
    return GDELTAdapter(client=make_test_client(), min_request_interval=0)


def test_ingestion_creates_company_and_snapshot(session):
    result = ingest_news_sentiment(session, make_adapter(), "AAPL", KNOWN_QUERY)

    assert result.created is True
    assert result.snapshot.article_count == 3172
    assert result.snapshot.mean_tone == Decimal("0.195")
    assert result.snapshot.query == KNOWN_QUERY

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
            EntityIdentifier.identifier_value == "AAPL",
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.entity_type == EntityType.COMPANY

    snapshots = session.execute(
        select(NewsSentimentSnapshot).where(NewsSentimentSnapshot.company_entity_id == entity.id)
    ).scalars().all()
    assert len(snapshots) == 1
    assert len(snapshots[0].tone_distribution) == 22


def test_rerunning_creates_a_new_snapshot_not_a_duplicate_error(session):
    """Unlike this system's other ingestion modules, GDELT snapshots are
    append-only — each search is its own observation over a continuously
    moving window, not a disclosure with a natural dedup key."""
    first = ingest_news_sentiment(session, make_adapter(), "AAPL", KNOWN_QUERY)
    second = ingest_news_sentiment(session, make_adapter(), "AAPL", KNOWN_QUERY)

    assert first.created is True
    assert second.created is True

    snapshots = session.execute(select(NewsSentimentSnapshot)).scalars().all()
    assert len(snapshots) == 2


def test_ticker_shares_entity_with_other_ticker_resolved_data(session):
    from capint.ingestion.finra_short_interest import get_or_create_company_by_ticker

    pre_existing = get_or_create_company_by_ticker(session, "AAPL", "Apple Inc. Common Stock")
    session.commit()

    result = ingest_news_sentiment(session, make_adapter(), "AAPL", KNOWN_QUERY)
    assert result.snapshot.company_entity_id == pre_existing.entity_id


def test_no_coverage_is_reported_not_an_error(session):
    result = ingest_news_sentiment(session, make_adapter(), "AAPL", "ZZZZNoCoverageForThisQueryZZZZ")
    assert result.created is False
    assert result.snapshot is None
    assert "No GDELT coverage" in result.note
