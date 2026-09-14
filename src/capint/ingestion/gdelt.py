"""Persistence for GDELT news-tone snapshots (Phase 15). Reuses
capint.ingestion.finra_short_interest's get_or_create_company_by_ticker
for entity resolution — GDELT itself has no concept of "company," only a
free-text search query, so the caller supplies both a ticker (to resolve
to this system's Entity graph) and the query string to actually search
GDELT with (see capint.models.news_sentiment's module docstring for why
these are kept separate: searching by bare ticker symbol, e.g. "F" for
Ford, would return near-random results)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.gdelt import GDELTAdapter, RawToneDistribution
from capint.ingestion.finra_short_interest import get_or_create_company_by_ticker
from capint.models.news_sentiment import NewsSentimentSnapshot
from capint.models.source import Source, SourceTier

SOURCE_NAME = "GDELT Project"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(
        name=SOURCE_NAME,
        tier=SourceTier.C_PUBLICATION,
        base_url="https://www.gdeltproject.org",
        license_notes=(
            "Free for academic, commercial, or governmental use without fee, including redistribution, "
            "per gdeltproject.org/about.html#termsofuse — attribution to the GDELT Project required."
        ),
    )
    session.add(source)
    session.flush()
    return source


@dataclass
class IngestionResult:
    created: bool
    snapshot: NewsSentimentSnapshot | None
    note: str | None = None


def ingest_news_sentiment(session: Session, adapter: GDELTAdapter, ticker: str, query: str, timespan: str = "7d") -> IngestionResult:
    """One ticker/query/timespan combination per call — GDELT's ~5-second
    per-request rate limit makes batch-ingesting many companies in one
    call impractical, so (unlike this system's other ingestion modules)
    this isn't wrapped in a multi-item run_ingestion loop."""
    source = get_or_create_source(session)
    company = get_or_create_company_by_ticker(session, ticker, ticker)

    fact = adapter.fetch_tone_distribution(query, timespan=timespan)
    if fact is None:
        return IngestionResult(created=False, snapshot=None, note=f"No GDELT coverage found for query={query!r} in the last {timespan}.")

    snapshot = NewsSentimentSnapshot(
        company_entity_id=company.entity_id,
        source_id=source.id,
        query=fact.query,
        timespan=fact.timespan,
        retrieved_at=fact.retrieved_at,
        article_count=fact.article_count,
        mean_tone=fact.mean_tone,
        tone_distribution=[{"bin": b.bin, "count": b.count} for b in fact.bins],
    )
    session.add(snapshot)
    session.commit()
    return IngestionResult(created=True, snapshot=snapshot)
