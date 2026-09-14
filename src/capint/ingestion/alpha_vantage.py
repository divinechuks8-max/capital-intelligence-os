"""Entity resolution + persistence for Alpha Vantage daily price bars
(Phase 13). Reuses capint.ingestion.finra_short_interest's
get_or_create_company_by_ticker directly, so a price bar and a
short-interest signal for the same ticker resolve to the same Company
entity — required for capint.backtesting.engine to join them."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.alpha_vantage import AlphaVantageAdapter, RawPriceBar
from capint.ingestion.finra_short_interest import get_or_create_company_by_ticker
from capint.models.price import PriceBar
from capint.models.source import Source, SourceTier

SOURCE_NAME = "Alpha Vantage"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(
        name=SOURCE_NAME,
        tier=SourceTier.B_LICENSED,
        base_url="https://www.alphavantage.co",
        license_notes="Free tier: outputsize=compact only (~100 trailing trading days). Full history is premium.",
    )
    session.add(source)
    session.flush()
    return source


def ingest_price_bar(session: Session, source: Source, company_entity_id, fact: RawPriceBar) -> bool:
    """Returns False if this (company, trade_date) bar was already
    ingested (idempotent no-op) — re-fetching the same recent window on a
    later day naturally re-observes already-ingested days."""
    existing = session.execute(
        select(PriceBar).where(PriceBar.company_entity_id == company_entity_id, PriceBar.trade_date == fact.trade_date)
    ).scalar_one_or_none()
    if existing is not None:
        return False

    session.add(
        PriceBar(
            company_entity_id=company_entity_id,
            source_id=source.id,
            ticker=fact.ticker,
            trade_date=fact.trade_date,
            open=fact.open,
            high=fact.high,
            low=fact.low,
            close=fact.close,
            volume=fact.volume,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    tickers_seen: int = 0
    tickers_with_no_data: int = 0
    bars_created: int = 0
    bars_skipped_duplicate: int = 0
    ticker_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: AlphaVantageAdapter, tickers: list[str]) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for ticker in tickers:
        summary.tickers_seen += 1
        try:
            bars = adapter.fetch_daily_prices(ticker)
        except Exception as exc:  # noqa: BLE001 — one bad ticker must not abort the batch
            summary.ticker_errors.append(f"{ticker}: {exc!r}")
            continue

        if not bars:
            summary.tickers_with_no_data += 1
            continue

        company = get_or_create_company_by_ticker(session, ticker, ticker)
        for bar in bars:
            created = ingest_price_bar(session, source, company.entity_id, bar)
            if created:
                summary.bars_created += 1
            else:
                summary.bars_skipped_duplicate += 1

    session.commit()
    return summary
