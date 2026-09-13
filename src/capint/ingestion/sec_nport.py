"""Entity resolution + persistence for SEC Form N-PORT data (Phase 9).

Same adapter/ingestion split as every other source here. Simpler than
Phase 7/8's XBRL ingestion in one respect — each N-PORT filing is its own
distinct point-in-time snapshot (no comparative-year restatement problem
to canonicalize away) — but adds its own derived field: each snapshot's
`net_assets_change_usd` vs. the fund's immediately preceding snapshot,
computed here at ingestion time. See capint.models.fund.FundAumSnapshot's
docstring for why that's a dollar delta, not an isolated flow figure.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_nport import NPortFilingLead, RawFundAumFact, SECNPortAdapter
from capint.ingestion.sec_form4 import get_or_create_source
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.fund import Fund, FundAumSnapshot
from capint.models.source import Document, Source


def _raw_reference(accession: str) -> str:
    return f"sec-nport:{accession}"


def get_or_create_fund(session: Session, cik: str, name: str, ticker: str | None) -> Fund:
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == cik,
        )
    ).scalar_one_or_none()
    if ident is not None:
        existing = session.get(Fund, ident.entity_id)
        if existing is not None:
            return existing
        fund = Fund(entity_id=ident.entity_id, ticker=ticker)
        session.add(fund)
        session.flush()
        return fund

    entity = Entity(entity_type=EntityType.FUND, canonical_name=name)
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value=cik, is_primary=True)
    )
    if ticker:
        session.add(
            EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.TICKER, identifier_value=ticker)
        )
    fund = Fund(entity_id=entity.id, ticker=ticker)
    session.add(fund)
    session.flush()
    return fund


def _previous_snapshot(session: Session, fund_entity_id, before_period) -> FundAumSnapshot | None:
    return session.execute(
        select(FundAumSnapshot)
        .where(FundAumSnapshot.fund_entity_id == fund_entity_id, FundAumSnapshot.period_end < before_period)
        .order_by(FundAumSnapshot.period_end.desc())
        .limit(1)
    ).scalar_one_or_none()


def ingest_fund_snapshot(session: Session, source: Source, fact: RawFundAumFact) -> bool:
    """Returns False if this accession was already ingested (idempotent no-op)."""
    ref = _raw_reference(fact.accession)
    if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
        return False

    fund = get_or_create_fund(session, fact.cik, fact.fund_name, fact.ticker)
    if fact.series_name and fund.series_name != fact.series_name:
        fund.series_name = fact.series_name

    prior = _previous_snapshot(session, fund.entity_id, fact.period_end)
    net_assets_change = fact.net_assets_usd - prior.net_assets_usd if prior is not None else None

    document = Document(
        source_id=source.id,
        external_id=fact.accession,
        url=(
            f"https://www.sec.gov/Archives/edgar/data/{fact.cik.lstrip('0') or '0'}/"
            f"{fact.accession.replace('-', '')}/primary_doc.xml"
        ),
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=EventType.ETF_FLOW,
        primary_entity_id=fund.entity_id,
        event_time=datetime.combine(fact.period_end, datetime.min.time(), tzinfo=timezone.utc),
        publication_time=fact.filed_at,
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=ref,
    )
    session.add(event)
    session.flush()

    session.add(
        FundAumSnapshot(
            event_id=event.id,
            fund_entity_id=fund.entity_id,
            period_end=fact.period_end,
            total_assets_usd=fact.total_assets_usd,
            total_liabilities_usd=fact.total_liabilities_usd,
            net_assets_usd=fact.net_assets_usd,
            net_assets_change_usd=net_assets_change,
            filing_form_type=fact.form,
            filing_accession=fact.accession,
        )
    )
    # Without this flush, the next snapshot for the same fund (processed
    # oldest-first within the same session/autoflush=False) would not see
    # this one via _previous_snapshot's query, and net_assets_change_usd
    # would go stuck at None for every period after the first.
    session.flush()
    return True


@dataclass
class IngestionSummary:
    funds_seen: int = 0
    funds_with_no_filings: int = 0
    snapshots_created: int = 0
    snapshots_skipped_duplicate: int = 0
    fund_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: SECNPortAdapter, ciks: list[str], filing_count: int = 8) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for cik in ciks:
        summary.funds_seen += 1
        try:
            leads: list[NPortFilingLead] = adapter.fetch_recent_filings(cik, limit=filing_count)
        except Exception as exc:  # noqa: BLE001 — one bad fund must not abort the batch
            summary.fund_errors.append(f"{cik}: {exc!r}")
            continue
        if not leads:
            summary.funds_with_no_filings += 1
            continue

        # Oldest first, so net_assets_change_usd always has a real prior
        # snapshot to compare against by the time later periods are ingested.
        for lead in sorted(leads, key=lambda leading: leading.filed_at):
            try:
                fact = adapter.fetch_fund_snapshot(lead)
            except Exception as exc:  # noqa: BLE001
                summary.fund_errors.append(f"{lead.accession_number}: {exc!r}")
                continue
            if fact is None:
                summary.fund_errors.append(f"{lead.accession_number}: could not parse expected fund/genInfo data")
                continue

            created = ingest_fund_snapshot(session, source, fact)
            if created:
                summary.snapshots_created += 1
            else:
                summary.snapshots_skipped_duplicate += 1

    session.commit()
    return summary
