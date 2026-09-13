"""Entity resolution + persistence for FINRA consolidated short interest
data (Phase 10, spec §24).

Same adapter/ingestion split as every other source. The one real
departure from every prior ingestion module: `get_or_create_company_by_ticker`
resolves by bare ticker, not CIK/CUSIP, because FINRA's short-interest
feed carries no issuer CIK at all. It first checks for an existing
TICKER identifier (so a company already resolved via Form 4/13F/13D-G by
CIK, which also stamps a TICKER identifier when one is known, gets
reused instead of duplicated) before creating a brand-new bare Company
entity keyed only by ticker.

This is a real, documented limitation, not an oversight: tickers are
reassigned over time (spec §5), and this module does not check
EntityIdentifier.valid_from/valid_to point-in-time validity when doing
that lookup, so a stale or reused ticker could resolve to the wrong
company. Point-in-time ticker validation is out of scope for this phase.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.finra_short_interest import FINRAShortInterestAdapter, RawShortInterestFact
from capint.models.company import Company
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.short_interest import ShortInterestSnapshot
from capint.models.source import Document, Source, SourceTier

SOURCE_NAME = "FINRA"


def _raw_reference(ticker: str, settlement_date: date) -> str:
    return f"finra-short-interest:{ticker.upper()}:{settlement_date.isoformat()}"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(name=SOURCE_NAME, tier=SourceTier.A_OFFICIAL, base_url="https://api.finra.org")
    session.add(source)
    session.flush()
    return source


def get_or_create_company_by_ticker(session: Session, ticker: str, name: str) -> Company:
    ticker = ticker.upper()
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.TICKER,
            EntityIdentifier.identifier_value == ticker,
        )
    ).scalars().first()
    if ident is not None:
        existing = session.get(Company, ident.entity_id)
        if existing is not None:
            return existing
        company = Company(entity_id=ident.entity_id)
        session.add(company)
        session.flush()
        return company

    entity = Entity(entity_type=EntityType.COMPANY, canonical_name=name)
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.TICKER, identifier_value=ticker, is_primary=True)
    )
    company = Company(entity_id=entity.id)
    session.add(company)
    session.flush()
    return company


def ingest_short_interest_fact(session: Session, source: Source, fact: RawShortInterestFact) -> bool:
    """Returns False if this (ticker, settlement_date) was already ingested (idempotent no-op)."""
    ref = _raw_reference(fact.ticker, fact.settlement_date)
    if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
        return False

    company = get_or_create_company_by_ticker(session, fact.ticker, fact.issue_name)

    document = Document(
        source_id=source.id,
        external_id=ref,
        url="https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=EventType.SHORT_INTEREST_CHANGE,
        primary_entity_id=company.entity_id,
        event_time=datetime.combine(fact.settlement_date, datetime.min.time(), tzinfo=timezone.utc),
        publication_time=datetime.combine(fact.settlement_date, datetime.min.time(), tzinfo=timezone.utc),
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=ref,
    )
    session.add(event)
    session.flush()

    session.add(
        ShortInterestSnapshot(
            event_id=event.id,
            company_entity_id=company.entity_id,
            ticker=fact.ticker,
            settlement_date=fact.settlement_date,
            current_short_position=fact.current_short_position,
            previous_short_position=fact.previous_short_position,
            change_percent=fact.change_percent,
            change_quantity=fact.change_quantity,
            average_daily_volume=fact.average_daily_volume,
            days_to_cover=fact.days_to_cover,
            exchange_code=fact.exchange_code,
            market_class_code=fact.market_class_code,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    tickers_seen: int = 0
    settlement_cycles_seen: int = 0
    snapshots_created: int = 0
    snapshots_skipped_duplicate: int = 0
    snapshots_not_reported: int = 0
    ticker_errors: list[str] = field(default_factory=list)


def run_ingestion(
    session: Session,
    adapter: FINRAShortInterestAdapter,
    tickers: list[str],
    num_cycles: int = 3,
    as_of: date | None = None,
) -> IngestionSummary:
    """Ingests the `num_cycles` most recent confirmed settlement dates for
    each ticker, oldest-first, so a future trend/acceleration score has a
    real time series to work with (mirrors the ingest-now/score-later split
    from Phases 2->3 and 4->5). `as_of` is exposed (not just defaulted to
    wall-clock `date.today()` inside the adapter) so tests can pin it
    against fixture data captured for a specific real date."""
    summary = IngestionSummary()
    source = get_or_create_source(session)

    settlement_dates = adapter.fetch_recent_settlement_dates(as_of=as_of, num_cycles=num_cycles)
    summary.settlement_cycles_seen = len(settlement_dates)

    for ticker in tickers:
        summary.tickers_seen += 1
        for settlement_date in sorted(settlement_dates):
            try:
                fact = adapter.fetch_short_interest(ticker, settlement_date)
            except Exception as exc:  # noqa: BLE001 — one bad ticker/date must not abort the batch
                summary.ticker_errors.append(f"{ticker}@{settlement_date}: {exc!r}")
                continue
            if fact is None:
                summary.snapshots_not_reported += 1
                continue

            created = ingest_short_interest_fact(session, source, fact)
            if created:
                summary.snapshots_created += 1
            else:
                summary.snapshots_skipped_duplicate += 1

    session.commit()
    return summary
