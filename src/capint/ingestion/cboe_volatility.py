"""Persistence for Cboe volatility index levels (Phase 14). No entity
resolution needed — see capint.models.volatility.VolatilityIndexLevel's
docstring for why this data isn't tied to any Company/Entity."""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.cboe_volatility import CBOEVolatilityIndexAdapter, RawVolatilityIndexLevel
from capint.models.source import Source, SourceTier
from capint.models.volatility import VolatilityIndexLevel

SOURCE_NAME = "Cboe Global Markets"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(
        name=SOURCE_NAME,
        tier=SourceTier.A_OFFICIAL,
        base_url="https://www.cboe.com",
        license_notes="Free public daily index history (cdn.cboe.com). Granular options-level data is a separate paid DataShop product not used here.",
    )
    session.add(source)
    session.flush()
    return source


def ingest_index_level(session: Session, source: Source, fact: RawVolatilityIndexLevel) -> bool:
    """Returns False if this (index, trade_date) level was already
    ingested (idempotent no-op) — re-fetching the same full history on a
    later day naturally re-observes already-ingested days."""
    existing = session.execute(
        select(VolatilityIndexLevel).where(
            VolatilityIndexLevel.index_code == fact.index_code, VolatilityIndexLevel.trade_date == fact.trade_date
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    session.add(
        VolatilityIndexLevel(
            source_id=source.id,
            index_code=fact.index_code,
            trade_date=fact.trade_date,
            open=fact.open,
            high=fact.high,
            low=fact.low,
            close=fact.close,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    index_codes_seen: int = 0
    index_codes_with_no_data: int = 0
    levels_created: int = 0
    levels_skipped_duplicate: int = 0
    index_code_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: CBOEVolatilityIndexAdapter, index_codes: list[str]) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for index_code in index_codes:
        summary.index_codes_seen += 1
        try:
            levels = adapter.fetch_index_history(index_code)
        except Exception as exc:  # noqa: BLE001 — one bad index code must not abort the batch
            summary.index_code_errors.append(f"{index_code}: {exc!r}")
            continue

        if not levels:
            summary.index_codes_with_no_data += 1
            continue

        for level in levels:
            created = ingest_index_level(session, source, level)
            if created:
                summary.levels_created += 1
            else:
                summary.levels_skipped_duplicate += 1

    session.commit()
    return summary
