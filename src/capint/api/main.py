from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from capint import temporal
from capint.api.schemas import (
    AlertOut,
    AlertRuleOut,
    AnalystRecommendationTrendOut,
    BacktestSummaryOut,
    BeneficialOwnershipDisclosureOut,
    CapitalAllocationFactOut,
    CompanyOut,
    ConvergenceEntryOut,
    CorporateActionDisclosureOut,
    CryptoTreasuryMovementOut,
    EventOut,
    FundAumSnapshotOut,
    FundamentalReportOut,
    FundOut,
    GuidanceDisclosureOut,
    InsiderRadarEntryOut,
    InstitutionalHoldingOut,
    InstitutionalRadarEntryOut,
    InstitutionOut,
    InterlockingDirectorateOut,
    NewsSentimentSnapshotOut,
    ShortInterestRadarEntryOut,
    ShortInterestSnapshotOut,
    UKPersonWithSignificantControlOut,
    VolatilityIndexLevelOut,
)
from capint.convergence.engine import compute_convergence
from capint.db import get_session
from capint.backtesting.engine import DEFAULT_HOLDING_TRADING_DAYS, backtest_rising_short_interest_cycles
from capint.relationships.engine import compute_interlocking_directorates
from capint.models.alert import Alert, AlertRule
from capint.models.analyst import AnalystRecommendationTrend
from capint.models.capital_allocation import CapitalAllocationFact
from capint.models.company import Company
from capint.models.corporate_action import CorporateActionDisclosure
from capint.models.crypto import CryptoTreasuryMovement
from capint.models.entity import Entity
from capint.models.event import Event, EventType
from capint.models.fund import Fund, FundAumSnapshot
from capint.models.fundamentals import FundamentalReport
from capint.models.guidance import GuidanceDisclosure
from capint.models.institution import InstitutionalHolding, InstitutionalManager
from capint.models.news_sentiment import NewsSentimentSnapshot
from capint.models.ownership import BeneficialOwnershipDisclosure
from capint.models.short_interest import ShortInterestSnapshot
from capint.models.uk_psc import UKPersonWithSignificantControl
from capint.models.volatility import VolatilityIndexLevel
from capint.radar.insider_radar import compute_insider_radar
from capint.radar.institutional_radar import compute_institutional_radar
from capint.radar.short_interest_radar import compute_short_interest_radar
from capint.scoring.insider_conviction import DEFAULT_BASELINE_LOOKBACK_DAYS as INSIDER_DEFAULT_BASELINE_LOOKBACK_DAYS
from capint.scoring.insider_conviction import DEFAULT_WINDOW_DAYS as INSIDER_DEFAULT_WINDOW_DAYS
from capint.scoring.institutional_accumulation import (
    DEFAULT_BASELINE_LOOKBACK_DAYS as INSTITUTIONAL_DEFAULT_BASELINE_LOOKBACK_DAYS,
)
from capint.scoring.institutional_accumulation import DEFAULT_WINDOW_DAYS as INSTITUTIONAL_DEFAULT_WINDOW_DAYS
from capint.scoring.short_interest_acceleration import DEFAULT_LOOKBACK_CYCLES as SHORT_INTEREST_DEFAULT_LOOKBACK_CYCLES

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


@app.get("/api/v1/fundamentals", response_model=list[FundamentalReportOut])
def list_fundamental_reports(
    company_entity_id: UUID | None = None,
    period_type: str | None = Query(default=None, pattern="^(QUARTER|FISCAL_YEAR)$"),
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[FundamentalReportOut]:
    """Revenue/earnings/margin reports (spec §21) from SEC XBRL structured
    data, point-in-time by `as_of` against Event.publication_time (the
    10-Q/10-K filing date — see capint.adapters.sec_xbrl for why this
    lags the actual earnings-release date, which isn't ingested)."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(FundamentalReport, Event)
        .join(Event, FundamentalReport.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(FundamentalReport.company_entity_id == company_entity_id)
    if period_type is not None:
        stmt = stmt.where(FundamentalReport.period_type == period_type)
    stmt = stmt.order_by(FundamentalReport.period_end.desc())

    rows = session.execute(stmt).all()
    return [
        FundamentalReportOut(
            event_id=event.id,
            company_entity_id=report.company_entity_id,
            period_type=report.period_type,
            period_start=report.period_start,
            period_end=report.period_end,
            fiscal_year=report.fiscal_year,
            fiscal_period=report.fiscal_period,
            revenue_usd=report.revenue_usd,
            net_income_usd=report.net_income_usd,
            eps_diluted=report.eps_diluted,
            gross_profit_usd=report.gross_profit_usd,
            operating_income_usd=report.operating_income_usd,
            gross_margin_pct=report.gross_margin_pct,
            operating_margin_pct=report.operating_margin_pct,
            filing_form_type=report.filing_form_type,
            filing_accession=report.filing_accession,
            publication_time=event.publication_time,
        )
        for report, event in rows
    ]


@app.get("/api/v1/funds", response_model=list[FundOut])
def list_funds(session: Session = Depends(get_session)) -> list[FundOut]:
    rows = session.execute(select(Fund, Entity).join(Entity, Fund.entity_id == Entity.id)).all()
    return [
        FundOut(
            entity_id=fund.entity_id,
            canonical_name=entity.canonical_name,
            ticker=fund.ticker,
            series_name=fund.series_name,
        )
        for fund, entity in rows
    ]


@app.get("/api/v1/fund-aum", response_model=list[FundAumSnapshotOut])
def list_fund_aum_snapshots(
    fund_entity_id: UUID | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[FundAumSnapshotOut]:
    """Fund/ETF assets-under-management snapshots from Form N-PORT (spec
    §17), point-in-time by `as_of` against Event.publication_time (the
    N-PORT's SEC acceptance time, typically ~60 days after `period_end` —
    see capint.adapters.sec_nport for why that lag is a hard ceiling of
    the source data, not an ingestion delay)."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(FundAumSnapshot, Event, Entity, Fund)
        .join(Event, FundAumSnapshot.event_id == Event.id)
        .join(Entity, FundAumSnapshot.fund_entity_id == Entity.id)
        .join(Fund, FundAumSnapshot.fund_entity_id == Fund.entity_id)
        .where(Event.publication_time <= cutoff)
    )
    if fund_entity_id is not None:
        stmt = stmt.where(FundAumSnapshot.fund_entity_id == fund_entity_id)
    stmt = stmt.order_by(FundAumSnapshot.period_end.desc())

    rows = session.execute(stmt).all()
    return [
        FundAumSnapshotOut(
            event_id=event.id,
            fund_entity_id=snapshot.fund_entity_id,
            fund_name=fund_entity.canonical_name,
            ticker=fund.ticker,
            period_end=snapshot.period_end,
            total_assets_usd=snapshot.total_assets_usd,
            total_liabilities_usd=snapshot.total_liabilities_usd,
            net_assets_usd=snapshot.net_assets_usd,
            net_assets_change_usd=snapshot.net_assets_change_usd,
            filing_form_type=snapshot.filing_form_type,
            filing_accession=snapshot.filing_accession,
            publication_time=event.publication_time,
        )
        for snapshot, event, fund_entity, fund in rows
    ]


@app.get("/api/v1/short-interest", response_model=list[ShortInterestSnapshotOut])
def list_short_interest_snapshots(
    company_entity_id: UUID | None = None,
    ticker: str | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[ShortInterestSnapshotOut]:
    """FINRA consolidated short interest snapshots (Phase 10, spec §24),
    point-in-time by `as_of` against Event.publication_time. Resolved by
    ticker, not CIK — see capint.models.short_interest's module docstring
    for the documented limitation that implies."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(ShortInterestSnapshot, Event)
        .join(Event, ShortInterestSnapshot.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(ShortInterestSnapshot.company_entity_id == company_entity_id)
    if ticker is not None:
        stmt = stmt.where(ShortInterestSnapshot.ticker == ticker.upper())
    stmt = stmt.order_by(ShortInterestSnapshot.settlement_date.desc())

    rows = session.execute(stmt).all()
    return [
        ShortInterestSnapshotOut(
            event_id=event.id,
            company_entity_id=snapshot.company_entity_id,
            ticker=snapshot.ticker,
            settlement_date=snapshot.settlement_date,
            current_short_position=snapshot.current_short_position,
            previous_short_position=snapshot.previous_short_position,
            change_percent=snapshot.change_percent,
            change_quantity=snapshot.change_quantity,
            average_daily_volume=snapshot.average_daily_volume,
            days_to_cover=snapshot.days_to_cover,
            exchange_code=snapshot.exchange_code,
            market_class_code=snapshot.market_class_code,
            publication_time=event.publication_time,
        )
        for snapshot, event in rows
    ]


@app.get("/api/v1/uk-psc", response_model=list[UKPersonWithSignificantControlOut])
def list_uk_psc_records(
    company_entity_id: UUID | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[UKPersonWithSignificantControlOut]:
    """UK Companies House Persons with Significant Control (beneficial
    ownership) disclosures (Phase 11, spec's UK extension), point-in-time
    by `as_of` against Event.publication_time. See
    capint.models.uk_psc's module docstring for why this is empty for
    LSE Main Market-listed companies (a real regulatory exemption, not a
    gap in this system)."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(UKPersonWithSignificantControl, Event)
        .join(Event, UKPersonWithSignificantControl.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(UKPersonWithSignificantControl.company_entity_id == company_entity_id)
    stmt = stmt.order_by(UKPersonWithSignificantControl.notified_on.desc())

    rows = session.execute(stmt).all()
    return [
        UKPersonWithSignificantControlOut(
            event_id=event.id,
            company_entity_id=psc.company_entity_id,
            psc_entity_id=psc.psc_entity_id,
            psc_name=psc.psc_name,
            psc_kind=psc.psc_kind,
            natures_of_control=psc.natures_of_control,
            notified_on=psc.notified_on,
            ceased_on=psc.ceased_on,
            country_of_residence=psc.country_of_residence,
            nationality=psc.nationality,
            publication_time=event.publication_time,
        )
        for psc, event in rows
    ]


@app.get("/api/v1/crypto-treasury", response_model=list[CryptoTreasuryMovementOut])
def list_crypto_treasury_movements(
    wallet_entity_id: UUID | None = None,
    address: str | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[CryptoTreasuryMovementOut]:
    """On-chain Bitcoin wallet activity for explicitly-tracked addresses
    (Phase 12, crypto extension), point-in-time by `as_of` against
    Event.publication_time (the transaction's own on-chain timestamp — no
    disclosure lag, unlike every SEC-sourced endpoint here). See
    capint.models.crypto's module docstring for why no wallet-owner
    attribution is exposed."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(CryptoTreasuryMovement, Event)
        .join(Event, CryptoTreasuryMovement.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if wallet_entity_id is not None:
        stmt = stmt.where(CryptoTreasuryMovement.wallet_entity_id == wallet_entity_id)
    if address is not None:
        stmt = stmt.where(CryptoTreasuryMovement.address == address)
    stmt = stmt.order_by(Event.publication_time.desc())

    rows = session.execute(stmt).all()
    return [
        CryptoTreasuryMovementOut(
            event_id=event.id,
            wallet_entity_id=movement.wallet_entity_id,
            chain=movement.chain,
            address=movement.address,
            tx_hash=movement.tx_hash,
            net_amount=movement.net_amount,
            block_height=movement.block_height,
            publication_time=event.publication_time,
        )
        for movement, event in rows
    ]


@app.get("/api/v1/guidance-disclosures", response_model=list[GuidanceDisclosureOut])
def list_guidance_disclosures(
    company_entity_id: UUID | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[GuidanceDisclosureOut]:
    """Guidance-relevant 8-K disclosures (Phase 12), point-in-time by
    `as_of` against Event.publication_time (the 8-K's SEC acceptance
    time). See capint.models.guidance's module docstring for why
    `item_codes` is a raw, unparsed observation, not an extracted
    guidance direction/magnitude."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(GuidanceDisclosure, Event)
        .join(Event, GuidanceDisclosure.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(GuidanceDisclosure.company_entity_id == company_entity_id)
    stmt = stmt.order_by(Event.publication_time.desc())

    rows = session.execute(stmt).all()
    return [
        GuidanceDisclosureOut(
            event_id=event.id,
            company_entity_id=disclosure.company_entity_id,
            item_codes=disclosure.item_codes,
            filing_form_type=disclosure.filing_form_type,
            filing_accession=disclosure.filing_accession,
            primary_document_url=disclosure.primary_document_url,
            publication_time=event.publication_time,
        )
        for disclosure, event in rows
    ]


@app.get("/api/v1/corporate-actions", response_model=list[CorporateActionDisclosureOut])
def list_corporate_action_disclosures(
    company_entity_id: UUID | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> list[CorporateActionDisclosureOut]:
    """M&A-relevant 8-K disclosures (Phase 14, Item 2.01), point-in-time
    by `as_of` against Event.publication_time. See
    capint.models.corporate_action's module docstring for why
    `item_codes` is a raw, unparsed observation, not extracted deal terms
    or a distinction between acquisition/spinoff/divestiture."""
    cutoff = as_of or datetime.now(tz=None).astimezone()
    stmt = (
        select(CorporateActionDisclosure, Event)
        .join(Event, CorporateActionDisclosure.event_id == Event.id)
        .where(Event.publication_time <= cutoff)
    )
    if company_entity_id is not None:
        stmt = stmt.where(CorporateActionDisclosure.company_entity_id == company_entity_id)
    stmt = stmt.order_by(Event.publication_time.desc())

    rows = session.execute(stmt).all()
    return [
        CorporateActionDisclosureOut(
            event_id=event.id,
            company_entity_id=disclosure.company_entity_id,
            item_codes=disclosure.item_codes,
            filing_form_type=disclosure.filing_form_type,
            filing_accession=disclosure.filing_accession,
            primary_document_url=disclosure.primary_document_url,
            publication_time=event.publication_time,
        )
        for disclosure, event in rows
    ]


@app.get("/api/v1/volatility-index", response_model=list[VolatilityIndexLevelOut])
def list_volatility_index_levels(
    index_code: str = "VIX",
    session: Session = Depends(get_session),
) -> list[VolatilityIndexLevelOut]:
    """Daily Cboe volatility index history (Phase 14, options/derivatives
    extension) — VIX by default. Not point-in-time gated like every other
    endpoint here (no `as_of` parameter): unlike a disclosure, an index
    level has no separate "publication" moment distinct from its own
    trade date, and Cboe publishes it same-day. See
    capint.models.volatility's module docstring for why this isn't tied
    to any Company/Entity."""
    stmt = (
        select(VolatilityIndexLevel)
        .where(VolatilityIndexLevel.index_code == index_code.upper())
        .order_by(VolatilityIndexLevel.trade_date.desc())
    )
    levels = session.execute(stmt).scalars().all()
    return [VolatilityIndexLevelOut.model_validate(level) for level in levels]


@app.get("/api/v1/analyst-recommendations", response_model=list[AnalystRecommendationTrendOut])
def list_analyst_recommendation_trends(
    company_entity_id: UUID | None = None,
    ticker: str | None = None,
    session: Session = Depends(get_session),
) -> list[AnalystRecommendationTrendOut]:
    """Aggregate analyst recommendation trends, newest period first. Not
    point-in-time gated (no `as_of` parameter), same reasoning as the
    volatility-index endpoint. See capint.models.analyst's module
    docstring for why there's no genuine disclosure timestamp to gate on,
    and why no ingestion path currently populates this table."""
    stmt = select(AnalystRecommendationTrend).order_by(AnalystRecommendationTrend.period.desc())
    if company_entity_id is not None:
        stmt = stmt.where(AnalystRecommendationTrend.company_entity_id == company_entity_id)
    if ticker is not None:
        stmt = stmt.where(AnalystRecommendationTrend.ticker == ticker.upper())
    trends = session.execute(stmt).scalars().all()
    return [AnalystRecommendationTrendOut.model_validate(t) for t in trends]


@app.get("/api/v1/relationships/interlocking-directorates", response_model=list[InterlockingDirectorateOut])
def list_interlocking_directorates(
    company_entity_id: UUID | None = None,
    session: Session = Depends(get_session),
) -> list[InterlockingDirectorateOut]:
    """Companies connected by a shared Person holding a role at both
    (Phase 15, pipeline's RELATIONSHIPS layer) — derived entirely from
    real Form 4 PersonCompanyRole data already in this system, not a new
    external data source. See capint.relationships.engine's module
    docstring for why only this relationship type is built."""
    results = compute_interlocking_directorates(session, company_entity_id=company_entity_id)
    return [
        InterlockingDirectorateOut(
            company_a_entity_id=r.company_a_entity_id,
            company_a_name=r.company_a_name,
            company_b_entity_id=r.company_b_entity_id,
            company_b_name=r.company_b_name,
            person_entity_id=r.person_entity_id,
            person_name=r.person_name,
            role_at_company_a=r.role_at_company_a,
            role_at_company_b=r.role_at_company_b,
        )
        for r in results
    ]


@app.get("/api/v1/news-sentiment", response_model=list[NewsSentimentSnapshotOut])
def list_news_sentiment_snapshots(
    company_entity_id: UUID | None = None,
    session: Session = Depends(get_session),
) -> list[NewsSentimentSnapshotOut]:
    """News-tone ("sentiment") snapshots (Phase 15), newest first. Not
    point-in-time gated (no `as_of` parameter) — `retrieved_at` is when
    this system ran the search, not a filing/disclosure timestamp. See
    capint.models.news_sentiment's module docstring for why this is
    directional sentiment context, not a precise per-company signal."""
    stmt = select(NewsSentimentSnapshot).order_by(NewsSentimentSnapshot.retrieved_at.desc())
    if company_entity_id is not None:
        stmt = stmt.where(NewsSentimentSnapshot.company_entity_id == company_entity_id)
    snapshots = session.execute(stmt).scalars().all()
    return [NewsSentimentSnapshotOut.model_validate(s) for s in snapshots]


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


@app.get("/api/v1/radar/short-interest", response_model=list[ShortInterestRadarEntryOut])
def short_interest_radar(
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    lookback_cycles: int = Query(default=SHORT_INTEREST_DEFAULT_LOOKBACK_CYCLES, ge=1, le=200),
    top_n: int = Query(default=25, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[ShortInterestRadarEntryOut]:
    """Companies with the strongest short-interest acceleration (Phase 13),
    ranked by score. Only rising short interest is scored — see
    capint.scoring.short_interest_acceleration for what "acceleration"
    means here and why short covering never appears.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    scores = compute_short_interest_radar(session, as_of=cutoff, lookback_cycles=lookback_cycles, top_n=top_n)
    return [ShortInterestRadarEntryOut.from_score(s) for s in scores]


@app.get("/api/v1/radar/convergence", response_model=list[ConvergenceEntryOut])
def convergence_radar(
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    insider_window_days: int = Query(default=INSIDER_DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    institutional_window_days: int = Query(default=INSTITUTIONAL_DEFAULT_WINDOW_DAYS, ge=1, le=3650),
    short_interest_lookback_cycles: int = Query(default=SHORT_INTEREST_DEFAULT_LOOKBACK_CYCLES, ge=1, le=200),
    top_n: int = Query(default=25, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[ConvergenceEntryOut]:
    """Companies where independent insider, institutional, and short-
    interest signals agree (or disagree) — a three-family down-scoped
    stand-in for the spec's full Convergence Engine (§29). See
    capint.convergence.engine's module docstring for exactly what that
    means and doesn't mean yet.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    entries = compute_convergence(
        session,
        as_of=cutoff,
        insider_window_days=insider_window_days,
        institutional_window_days=institutional_window_days,
        short_interest_lookback_cycles=short_interest_lookback_cycles,
        top_n=top_n,
    )
    return [ConvergenceEntryOut.from_entry(e) for e in entries]


@app.get("/api/v1/alert-rules", response_model=list[AlertRuleOut])
def list_alert_rules(session: Session = Depends(get_session)) -> list[AlertRuleOut]:
    """Configured alert rules (Phase 13). Rules are created via the CLI
    (`create-alert-rule`), not this API — this endpoint is read-only, like
    every other endpoint in this system."""
    rules = session.execute(select(AlertRule)).scalars().all()
    return [AlertRuleOut.model_validate(r) for r in rules]


@app.get("/api/v1/alerts", response_model=list[AlertOut])
def list_alerts(
    rule_id: UUID | None = None,
    company_entity_id: UUID | None = None,
    session: Session = Depends(get_session),
) -> list[AlertOut]:
    """Persisted alerts (Phase 13, pipeline's ALERT stage), newest first.
    Populated by running `evaluate-alerts` — see capint.alerting.engine
    for how each rule type is evaluated against current signal output."""
    stmt = select(Alert).order_by(Alert.triggered_at.desc())
    if rule_id is not None:
        stmt = stmt.where(Alert.rule_id == rule_id)
    if company_entity_id is not None:
        stmt = stmt.where(Alert.company_entity_id == company_entity_id)
    alerts = session.execute(stmt).scalars().all()
    return [AlertOut.model_validate(a) for a in alerts]


@app.get("/api/v1/backtest/short-interest", response_model=BacktestSummaryOut)
def backtest_short_interest_endpoint(
    holding_trading_days: int = Query(default=DEFAULT_HOLDING_TRADING_DAYS, ge=1, le=250),
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    session: Session = Depends(get_session),
) -> BacktestSummaryOut:
    """Explores what historically followed a rising FINRA short-interest
    cycle, using whatever real price data has been ingested (Phase 13,
    pipeline's HISTORICAL VALIDATION stage). Plain descriptive statistics
    only — never a trading signal or investment advice; see
    capint.backtesting.engine's module docstring, and this system's
    README for the free-tier price-history depth limitation.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    summary = backtest_rising_short_interest_cycles(session, as_of=cutoff, holding_trading_days=holding_trading_days)
    return BacktestSummaryOut.from_summary(summary)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
