"""Entity resolution + persistence for SEC Form 13F-HR data (Phase 4).

Mirrors capint.ingestion.sec_form4's split (adapter fetches/parses,
ingestion resolves entities and persists) with one structurally different
wrinkle: 13F identifies companies by CUSIP, not CIK. This module therefore
resolves/creates Company entities by CUSIP
(get_or_create_company_by_cusip), completely independent of Phase 2's
CIK-based get_or_create_company.

Known limitation, stated plainly: nothing here cross-links a CUSIP-created
Company with a CIK-created one for the same real-world issuer. Doing that
correctly needs a maintained CUSIP<->CIK/ticker mapping (e.g. from a
licensed security-master, or SEC's own company facts data) that this phase
doesn't build. Until it exists, the same company can legitimately end up
as two different Entity rows depending on which adapter saw it first — an
honest architectural gap, not an oversight (spec §5 warns entity
resolution is exactly this hard).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.sec_13f import Filing13FData, SEC13FAdapter
from capint.models.company import Company
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.institution import (
    InstitutionalHolding,
    InstitutionalManager,
    InstitutionalManagerType,
    InstitutionalPositionStatus,
)
from capint.models.source import Document, Source, SourceTier

SOURCE_NAME = "SEC EDGAR"


def _raw_reference(accession: str, key: str) -> str:
    return f"sec-13f:{accession}#{key}"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(name=SOURCE_NAME, tier=SourceTier.A_OFFICIAL, base_url="https://www.sec.gov")
    session.add(source)
    session.flush()
    return source


def get_or_create_institution(session: Session, cik: str, name: str) -> InstitutionalManager:
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == cik,
        )
    ).scalar_one_or_none()
    if ident is not None:
        existing = session.get(InstitutionalManager, ident.entity_id)
        if existing is not None:
            return existing

    entity = Entity(entity_type=EntityType.INSTITUTION, canonical_name=name)
    session.add(entity)
    session.flush()
    session.add(EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CIK, identifier_value=cik, is_primary=True))
    manager = InstitutionalManager(entity_id=entity.id, manager_type=InstitutionalManagerType.OTHER)
    session.add(manager)
    session.flush()
    return manager


def get_or_create_company_by_cusip(session: Session, cusip: str, name: str) -> Company:
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CUSIP,
            EntityIdentifier.identifier_value == cusip,
        )
    ).scalar_one_or_none()
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
    session.add(EntityIdentifier(entity_id=entity.id, identifier_type=IdentifierType.CUSIP, identifier_value=cusip, is_primary=True))
    company = Company(entity_id=entity.id)
    session.add(company)
    session.flush()
    return company


def _previous_holdings(session: Session, institution_entity_id, before_period) -> dict:
    """The institution's most recent InstitutionalHolding per company from
    a period strictly before `before_period` — i.e. the prior filing's
    reported portfolio, used both to compute shares_change and to detect
    positions closed since then."""
    latest_period = session.execute(
        select(InstitutionalHolding.period_of_report)
        .where(
            InstitutionalHolding.institution_entity_id == institution_entity_id,
            InstitutionalHolding.period_of_report < before_period,
            InstitutionalHolding.position_status != InstitutionalPositionStatus.EXITED,
        )
        .order_by(InstitutionalHolding.period_of_report.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest_period is None:
        return {}

    rows = session.execute(
        select(InstitutionalHolding).where(
            InstitutionalHolding.institution_entity_id == institution_entity_id,
            InstitutionalHolding.period_of_report == latest_period,
            InstitutionalHolding.position_status != InstitutionalPositionStatus.EXITED,
        )
    ).scalars()
    return {row.company_entity_id: row for row in rows}


def ingest_filing(session: Session, source: Source, data: Filing13FData) -> "FilingIngestionResult":
    # Whole-filing idempotency: a 13F-HR is one atomic full-portfolio
    # snapshot, so "already ingested" is checked once per accession rather
    # than per holding row (contrast capint.ingestion.sec_form4, where each
    # transaction is independently keyed — Form 4 events aren't a snapshot).
    already_ingested = session.execute(
        select(Document).where(Document.external_id == data.accession_number)
    ).first()
    if already_ingested is not None:
        return FilingIngestionResult(skipped_duplicate=True)

    institution = get_or_create_institution(session, data.filer_cik, data.filer_name)
    previous = _previous_holdings(session, institution.entity_id, data.period_of_report)

    result = FilingIngestionResult()
    seen_company_ids = set()

    for holding in data.holdings:
        company = get_or_create_company_by_cusip(session, holding.cusip, holding.issuer_name)
        seen_company_ids.add(company.entity_id)
        prior = previous.get(company.entity_id)

        if prior is None:
            status = InstitutionalPositionStatus.NEW
            shares_change = None
        else:
            shares_change = holding.shares - prior.shares_held
            if shares_change > 0:
                status = InstitutionalPositionStatus.INCREASED
            elif shares_change < 0:
                status = InstitutionalPositionStatus.DECREASED
            else:
                status = InstitutionalPositionStatus.UNCHANGED

        document = Document(
            source_id=source.id,
            external_id=data.accession_number,
            url=data.filing_url,
            retrieved_at=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()

        event = Event(
            event_type=EventType.INSTITUTIONAL_POSITION_CHANGE,
            primary_entity_id=company.entity_id,
            event_time=datetime.combine(data.period_of_report, datetime.min.time(), tzinfo=timezone.utc),
            publication_time=data.published_at,
            source_id=source.id,
            document_id=document.id,
            confidence=1.0,
            raw_data_reference=_raw_reference(data.accession_number, holding.cusip),
        )
        session.add(event)
        session.flush()

        session.add(
            InstitutionalHolding(
                event_id=event.id,
                institution_entity_id=institution.entity_id,
                company_entity_id=company.entity_id,
                period_of_report=data.period_of_report,
                shares_held=holding.shares,
                market_value_usd=holding.market_value_usd,
                shares_change=shares_change,
                position_status=status,
                filing_form_type=data.form_type,
            )
        )
        result.holdings_created += 1

    for company_id, prior_row in previous.items():
        if company_id in seen_company_ids:
            continue
        document = Document(
            source_id=source.id,
            external_id=data.accession_number,
            url=data.filing_url,
            retrieved_at=datetime.now(timezone.utc),
        )
        session.add(document)
        session.flush()

        event = Event(
            event_type=EventType.INSTITUTIONAL_POSITION_CHANGE,
            primary_entity_id=company_id,
            event_time=datetime.combine(data.period_of_report, datetime.min.time(), tzinfo=timezone.utc),
            publication_time=data.published_at,
            source_id=source.id,
            document_id=document.id,
            confidence=1.0,
            raw_data_reference=_raw_reference(data.accession_number, f"EXIT:{prior_row.company_entity_id}"),
        )
        session.add(event)
        session.flush()

        session.add(
            InstitutionalHolding(
                event_id=event.id,
                institution_entity_id=institution.entity_id,
                company_entity_id=company_id,
                period_of_report=data.period_of_report,
                shares_held=0,
                market_value_usd=0,
                shares_change=-prior_row.shares_held,
                position_status=InstitutionalPositionStatus.EXITED,
                filing_form_type=data.form_type,
            )
        )
        result.exits_created += 1

    return result


@dataclass
class FilingIngestionResult:
    holdings_created: int = 0
    exits_created: int = 0
    skipped_duplicate: bool = False


@dataclass
class IngestionSummary:
    filings_seen: int = 0
    filings_ingested: int = 0
    filings_skipped_duplicate: int = 0
    holdings_created: int = 0
    exits_created: int = 0
    filing_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: SEC13FAdapter, filing_count: int = 100) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    filings = adapter.fetch_current_13f_filings(count=filing_count)
    summary.filings_seen = len(filings)

    for filing in filings:
        try:
            data = adapter.fetch_holdings_for_filing(filing)
        except Exception as exc:  # noqa: BLE001 — one bad filing must not abort the batch
            summary.filing_errors.append(f"{filing.accession_number}: {exc!r}")
            continue
        if data is None:
            summary.filing_errors.append(f"{filing.accession_number}: could not locate expected documents")
            continue

        result = ingest_filing(session, source, data)
        if result.skipped_duplicate:
            summary.filings_skipped_duplicate += 1
        else:
            summary.filings_ingested += 1
            summary.holdings_created += result.holdings_created
            summary.exits_created += result.exits_created

    session.commit()
    return summary
