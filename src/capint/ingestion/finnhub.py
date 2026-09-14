"""Persistence for Finnhub analyst recommendation trends (Phase 14).
Reuses capint.ingestion.finra_short_interest's get_or_create_company_by_ticker
directly, same reasoning as capint.ingestion.alpha_vantage (Phase 13) —
so this data lands on the same Entity as other ticker-resolved signals."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.finnhub import FinnhubAdapter, RawRecommendationTrend
from capint.ingestion.finra_short_interest import get_or_create_company_by_ticker
from capint.models.analyst import AnalystRecommendationTrend
from capint.models.source import Source, SourceTier

SOURCE_NAME = "Finnhub"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(
        name=SOURCE_NAME,
        tier=SourceTier.B_LICENSED,
        base_url="https://finnhub.io",
        license_notes="Free tier: aggregate recommendation-trend counts only, not individual analyst estimates/price targets (a separate paid feature).",
    )
    session.add(source)
    session.flush()
    return source


def ingest_recommendation_trend(session: Session, source: Source, company_entity_id, fact: RawRecommendationTrend) -> bool:
    """Returns False if this (company, period) trend was already ingested
    (idempotent no-op)."""
    existing = session.execute(
        select(AnalystRecommendationTrend).where(
            AnalystRecommendationTrend.company_entity_id == company_entity_id,
            AnalystRecommendationTrend.period == fact.period,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    session.add(
        AnalystRecommendationTrend(
            company_entity_id=company_entity_id,
            source_id=source.id,
            ticker=fact.ticker,
            period=fact.period,
            strong_buy=fact.strong_buy,
            buy=fact.buy,
            hold=fact.hold,
            sell=fact.sell,
            strong_sell=fact.strong_sell,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    tickers_seen: int = 0
    tickers_with_no_data: int = 0
    trends_created: int = 0
    trends_skipped_duplicate: int = 0
    ticker_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: FinnhubAdapter, tickers: list[str]) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for ticker in tickers:
        summary.tickers_seen += 1
        try:
            trends = adapter.fetch_recommendation_trends(ticker)
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not abort the batch
            summary.ticker_errors.append(f"{ticker}: {exc!r}")
            continue

        if not trends:
            summary.tickers_with_no_data += 1
            continue

        company = get_or_create_company_by_ticker(session, ticker, ticker)
        for trend in trends:
            created = ingest_recommendation_trend(session, source, company.entity_id, trend)
            if created:
                summary.trends_created += 1
            else:
                summary.trends_skipped_duplicate += 1

    session.commit()
    return summary
