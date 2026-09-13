from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from capint import temporal
from capint.api.schemas import (
    BeneficialOwnershipDisclosureOut,
    CapitalAllocationFactOut,
    CompanyOut,
    ConvergenceEntryOut,
    EventOut,
    InsiderRadarEntryOut,
    InstitutionalHoldingOut,
    InstitutionalRadarEntryOut,
    InstitutionOut,
)
from capint.convergence.engine import compute_convergence
from capint.db import get_session
from capint.models.capital_allocation import CapitalAllocationFact
from capint.models.company import Company
from capint.models.entity import Entity
from capint.models.event import Event, EventType
from capint.models.institution import InstitutionalHolding, InstitutionalManager
from capint.models.ownership import BeneficialOwnershipDisclosure
from capint.radar.insider_radar import compute_insider_radar
from capint.radar.institutional_radar import compute_institutional_radar
from capint.scoring.insider_conviction import DEFAULT_BASELINE_LOOKBACK_DAYS as INSIDER_DEFAULT_BASELINE_LOOKBACK_DAYS
from capint.scoring.insider_conviction import DEFAULT_WINDOW_DAYS as INSIDER_DEFAULT_WINDOW_DAYS
from capint.scoring.institutional_accumulation import (
    DEFAULT_BASELINE_LOOKBACK_DAYS as INSTITUTIONAL_DEFAULT_BASELINE_LOOKBACK_DAYS,
)
from capint.scoring.institutional_accumulation import DEFAULT_WINDOW_DAYS as INSTITUTIONAL_DEFAULT_WINDOW_DAYS

app = FastAPI(title="Capital Intelligence OS", version="0.1.0")


@app.get("/api/v1/companies", response_model=list[CompanyOut])
def list_companies(session: Session = Depends(get_session)) -> list[CompanyOut]:
    rows = session.execute(select(Company, Entity).join(Entity, Company.entity_id == Entity.id)).all()
    return [
        CompanyOut(
            entity_id=company.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            sector=company.sector,
            industry=company.industry,
            country=company.country,
        )
        for company, entity in rows
    ]


@app.get("/api/v1/companies/{entity_id}", response_model=CompanyOut)
def get_company(entity_id: UUID, session: Session = Depends(get_session)) -> CompanyOut:
    row = session.execute(
        select(Company, Entity).join(Entity, Company.entity_id == Entity.id).where(Company.entity_id == entity_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")
    company, entity = row
    return CompanyOut(
        entity_id=company.entity_id,
        canonical_name=entity.canonical_name,
        entity_type=entity.entity_type,
        sector=company.sector,
        industry=company.industry,
        country=company.country,
    )


@app.get("/api/v1/events", response_model=list[EventOut])
def list_events(
    entity_id: UUID | None = None,
    event_type: EventType | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    mode: str = Query(default="public", pattern="^(public|ingested)$"),
    session: Session = Depends(get_session),
) -> list[EventOut]:
    """Point-in-time event listing.

    mode=public  -> only events publicly disclosed by `as_of` (Event.publication_time).
    mode=ingested -> only events our own pipeline had recorded by `as_of` (Document.retrieved_at).

    This is the enforcement point for spec §8/§72: there is no way to ask
    this endpoint for "all events" without an as_of boundary — as_of simply
    defaults to now, which is the least surprising default and still safe.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    event_types = [event_type] if event_type else None
    query_fn = temporal.as_of_public if mode == "public" else temporal.as_of_ingested
    events = query_fn(session, cutoff, entity_id=entity_id, event_types=event_types)
    return [EventOut.model_validate(e) for e in events]


@app.get("/api/v1/institutions", response_model=list[InstitutionOut])
def list_institutions(session: Session = Depends(get_session)) -> list[InstitutionOut]:
    rows = session.execute(
        select(InstitutionalManager, Entity).join(Entity, InstitutionalManager.entity_id == Entity.id)
    ).all()
    return [
        InstitutionOut(
            entity_id=manager.entity_id,
            canonical_name=entity.canonical_name,
            manager_type=manager.manager_type,
            form13f_file_number=manager.form13f_file_number,
        )
        for manager, entity in rows
    ]


@app.get("/api/v1/holdings", response_model=list[InstitutionalHoldingOut])
def list_institutional_holdings(
    company_entity_id: UUID | None = None,
    institution_entity_id: UUID | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[InstitutionalHoldingOut]:
    """Institutional (13F) positions, point-in-time by `as_of` against
    Event.publication_time — i.e. what had actually been disclosed by then,
    not what the position "is" (period_of_report can trail publication_time
    by 30-45 days; both are returned separately so a caller can never
    mistake one for the other — spec §13)."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(InstitutionalHolding, Event, Entity)
        .join(Event, InstitutionalHolding.event_id == Event.id)
        .join(Entity, InstitutionalHolding.institution_entity_id == Entity.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(InstitutionalHolding.company_entity_id == company_entity_id)
    if institution_entity_id is not None:
        stmt = stmt.where(InstitutionalHolding.institution_entity_id == institution_entity_id)
    stmt = stmt.order_by(Event.publication_time)

    rows = session.execute(stmt).all()
    return [
        InstitutionalHoldingOut(
            event_id=event.id,
            institution_entity_id=holding.institution_entity_id,
            institution_name=institution_entity.canonical_name,
            company_entity_id=holding.company_entity_id,
            period_of_report=holding.period_of_report,
            publication_time=event.publication_time,
            shares_held=holding.shares_held,
            market_value_usd=holding.market_value_usd,
            shares_change=holding.shares_change,
            position_status=holding.position_status,
        )
        for holding, event, institution_entity in rows
    ]


@app.get("/api/v1/ownership-disclosures", response_model=list[BeneficialOwnershipDisclosureOut])
def list_ownership_disclosures(
    company_entity_id: UUID | None = None,
    filer_entity_id: UUID | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[BeneficialOwnershipDisclosureOut]:
    """Schedule 13D (activist stake) and 13G (passive major holder)
    disclosures, point-in-time by `as_of` against Event.publication_time —
    the actual EDGAR acceptance timestamp, kept separate from
    `event_date` (spec §13's "as of" / "filed on" distinction, same as
    /api/v1/holdings)."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(BeneficialOwnershipDisclosure, Event, Entity)
        .join(Event, BeneficialOwnershipDisclosure.event_id == Event.id)
        .join(Entity, BeneficialOwnershipDisclosure.filer_entity_id == Entity.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(BeneficialOwnershipDisclosure.company_entity_id == company_entity_id)
    if filer_entity_id is not None:
        stmt = stmt.where(BeneficialOwnershipDisclosure.filer_entity_id == filer_entity_id)
    stmt = stmt.order_by(Event.publication_time)

    rows = session.execute(stmt).all()
    return [
        BeneficialOwnershipDisclosureOut(
            event_id=event.id,
            filer_entity_id=disclosure.filer_entity_id,
            filer_name=filer_entity.canonical_name,
            company_entity_id=disclosure.company_entity_id,
            schedule_type=disclosure.schedule_type,
            filer_type_code=disclosure.filer_type_code,
            shares_beneficially_owned=disclosure.shares_beneficially_owned,
            percent_of_class=disclosure.percent_of_class,
            sole_voting_power=disclosure.sole_voting_power,
            shared_voting_power=disclosure.shared_voting_power,
            sole_dispositive_power=disclosure.sole_dispositive_power,
            shared_dispositive_power=disclosure.shared_dispositive_power,
            event_date=disclosure.event_date,
            publication_time=event.publication_time,
            stated_purpose=disclosure.stated_purpose,
            is_joint_filing=disclosure.is_joint_filing,
        )
        for disclosure, event, filer_entity in rows
    ]


@app.get("/api/v1/capital-allocation", response_model=list[CapitalAllocationFactOut])
def list_capital_allocation_facts(
    company_entity_id: UUID | None = None,
    event_type: EventType | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[CapitalAllocationFactOut]:
    """Annual buyback / dividend / debt-issuance / debt-repayment totals
    from SEC XBRL structured data, point-in-time by `as_of` against
    Event.publication_time (the 10-K's filing date — see
    capint.adapters.sec_xbrl for why this is coarser than the other
    adapters' precise timestamps)."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(CapitalAllocationFact, Event)
        .join(Event, CapitalAllocationFact.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(CapitalAllocationFact.company_entity_id == company_entity_id)
    if event_type is not None:
        stmt = stmt.where(Event.event_type == event_type)
    stmt = stmt.order_by(CapitalAllocationFact.period_end.desc())

    rows = session.execute(stmt).all()
    return [
        CapitalAllocationFactOut(
            event_id=event.id,
            company_entity_id=fact.company_entity_id,
            event_type=event.event_type,
            xbrl_concept=fact.xbrl_concept,
            amount_usd=fact.amount_usd,
            period_start=fact.period_start,
            period_end=fact.period_end,
            fiscal_year=fact.fiscal_year,
            filing_form_type=fact.filing_form_type,
            filing_accession=fact.filing_accession,
            publication_time=event.publication_time,
        )
        for fact, event in rows
    ]


@app.get("/api/v1/radar/insider", response_model=list[InsiderRadarEntryOut])
def insider_radar(
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    window_days: int = Query(default=INSIDER_DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    baseline_lookback_days: int = Query(default=INSIDER_DEFAULT_BASELINE_LOOKBACK_DAYS, ge=1, le=36500),
    top_n: int = Query(default=25, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[InsiderRadarEntryOut]:
    """Companies with the strongest discretionary open-market insider
    buying in the trailing `window_days`, ranked by conviction score.
    Every entry carries its score components and evidence — see
    capint.scoring.insider_conviction for what "conviction" means here and
    why (spec §69: a radar result must always answer "why is this here?").
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    scores = compute_insider_radar(
        session,
        as_of=cutoff,
        window_days=window_days,
        baseline_lookback_days=baseline_lookback_days,
        top_n=top_n,
    )
    return [InsiderRadarEntryOut.from_score(s) for s in scores]


@app.get("/api/v1/radar/institutional", response_model=list[InstitutionalRadarEntryOut])
def institutional_radar(
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    window_days: int = Query(default=INSTITUTIONAL_DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    baseline_lookback_days: int = Query(default=INSTITUTIONAL_DEFAULT_BASELINE_LOOKBACK_DAYS, ge=1, le=36500),
    top_n: int = Query(default=25, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[InstitutionalRadarEntryOut]:
    """Companies with the strongest 13F institutional accumulation in the
    trailing `window_days`, ranked by score. See
    capint.scoring.institutional_accumulation for what "accumulation"
    means here and why.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    scores = compute_institutional_radar(
        session,
        as_of=cutoff,
        window_days=window_days,
        baseline_lookback_days=baseline_lookback_days,
        top_n=top_n,
    )
    return [InstitutionalRadarEntryOut.from_score(s) for s in scores]


@app.get("/api/v1/radar/convergence", response_model=list[ConvergenceEntryOut])
def convergence_radar(
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    insider_window_days: int = Query(default=INSIDER_DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    institutional_window_days: int = Query(default=INSTITUTIONAL_DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    top_n: int = Query(default=25, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[ConvergenceEntryOut]:
    """Companies where independent insider and institutional signals agree
    (or disagree) — a two-family down-scoped stand-in for the spec's full
    Convergence Engine (§29). See capint.convergence.engine's module
    docstring for exactly what that means and doesn't mean yet.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    entries = compute_convergence(
        session,
        as_of=cutoff,
        insider_window_days=insider_window_days,
        institutional_window_days=institutional_window_days,
        top_n=top_n,
    )
    return [ConvergenceEntryOut.from_entry(e) for e in entries]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
