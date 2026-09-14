"""API DTOs. Kept separate from ORM models so the wire format can evolve
independently of the storage schema."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from capint.convergence.engine import ConvergenceEntry, ConvergenceLabel
from capint.models.entity import EntityType
from capint.models.event import EventType
from capint.models.fundamentals import FundamentalPeriodType
from capint.models.institution import InstitutionalManagerType, InstitutionalPositionStatus
from capint.models.ownership import ScheduleType
from capint.scoring.insider_conviction import InsiderConvictionScore
from capint.scoring.institutional_accumulation import InstitutionalAccumulationScore
from capint.scoring.short_interest_acceleration import ShortInterestAccelerationScore


class CapitalAllocationFactOut(BaseModel):
    """`period_end`/`period_start` identify the disclosed fiscal year;
    `publication_time` is when that 10-K was filed — a coarser precision
    than the other adapters (XBRL only gives a filing date, not a
    timestamp) — see capint.adapters.sec_xbrl's module docstring."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    company_entity_id: UUID
    event_type: EventType
    xbrl_concept: str
    amount_usd: Decimal
    period_start: date
    period_end: date
    fiscal_year: int | None
    filing_form_type: str | None
    filing_accession: str | None
    publication_time: datetime


class FundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    canonical_name: str
    ticker: str | None = None
    series_name: str | None = None


class FundAumSnapshotOut(BaseModel):
    """`net_assets_change_usd` is a plain quarter-over-quarter dollar
    delta, not an isolated flow figure (N-PORT doesn't expose the
    shares-outstanding data a true flow calculation needs — see
    capint.models.fund.FundAumSnapshot's docstring). `publication_time` is
    when this N-PORT was accepted, typically ~60 days after `period_end`."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    fund_entity_id: UUID
    fund_name: str
    ticker: str | None
    period_end: date
    total_assets_usd: Decimal
    total_liabilities_usd: Decimal | None
    net_assets_usd: Decimal
    net_assets_change_usd: Decimal | None
    filing_form_type: str | None
    filing_accession: str | None
    publication_time: datetime


class FundamentalReportOut(BaseModel):
    """`publication_time` is the 10-Q/10-K filing date — earlier than that
    (usually by days-to-weeks) is when the actual earnings release/8-K
    happened, which this system doesn't ingest yet (see
    capint.adapters.sec_xbrl's module docstring). Margin fields are plain
    arithmetic on the disclosed figures, not a score."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    company_entity_id: UUID
    period_type: FundamentalPeriodType
    period_start: date
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    revenue_usd: Decimal | None
    net_income_usd: Decimal | None
    eps_diluted: Decimal | None
    gross_profit_usd: Decimal | None
    operating_income_usd: Decimal | None
    gross_margin_pct: Decimal | None
    operating_margin_pct: Decimal | None
    filing_form_type: str | None
    filing_accession: str | None
    publication_time: datetime


class ShortInterestSnapshotOut(BaseModel):
    """`change_percent`/`change_quantity` are reported directly by FINRA
    alongside the position itself, not derived after the fact (contrast
    with FundAumSnapshotOut.net_assets_change_usd, which this system
    computes) — see capint.models.short_interest.ShortInterestSnapshot's
    docstring. `publication_time` here is the settlement date itself,
    since FINRA's API does not expose a separate report-publication
    timestamp distinct from the settlement cycle date."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    company_entity_id: UUID
    ticker: str
    settlement_date: date
    current_short_position: Decimal
    previous_short_position: Decimal | None
    change_percent: Decimal | None
    change_quantity: Decimal | None
    average_daily_volume: Decimal | None
    days_to_cover: Decimal | None
    exchange_code: str | None
    market_class_code: str | None
    publication_time: datetime


class UKPersonWithSignificantControlOut(BaseModel):
    """`natures_of_control` and `psc_kind` are Companies House's own raw
    vocabulary strings, passed through verbatim rather than mapped into a
    closed enum — see capint.models.uk_psc.UKPersonWithSignificantControl's
    docstring. `psc_entity_id` is scoped to this one (company, PSC)
    relationship for individual/non-UK-registered PSCs (no stable
    cross-company identifier exists for those — a real, documented
    limitation), but resolves to a shared Company entity for a corporate
    PSC with a real UK Companies House number."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    company_entity_id: UUID
    psc_entity_id: UUID | None
    psc_name: str
    psc_kind: str
    natures_of_control: list[str]
    notified_on: date
    ceased_on: date | None
    country_of_residence: str | None
    nationality: str | None
    publication_time: datetime


class CryptoTreasuryMovementOut(BaseModel):
    """No wallet-owner attribution field exists here — see
    capint.models.crypto.CryptoTreasuryMovement's docstring for why. The
    wallet entity's name is simply its raw address."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    wallet_entity_id: UUID
    chain: str
    address: str
    tx_hash: str
    net_amount: Decimal
    block_height: int | None
    publication_time: datetime


class GuidanceDisclosureOut(BaseModel):
    """`item_codes` is SEC's own raw item-number string, passed through
    verbatim — see capint.models.guidance.GuidanceDisclosure's docstring
    for why no guidance direction/magnitude is extracted or claimed here."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    company_entity_id: UUID
    item_codes: str
    filing_form_type: str
    filing_accession: str
    primary_document_url: str | None
    publication_time: datetime


class AlertRuleOut(BaseModel):
    """Rules are user configuration, created via the CLI — this schema
    exists only to read back what's configured, never to create one
    (this system's API surface is read-only everywhere else)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    rule_type: str
    min_composite_score: float | None
    convergence_labels: list[str] | None
    is_active: bool


class AlertOut(BaseModel):
    """`source_event_ids` links back to the real evidence events behind
    the triggering score — never a bare number without a why (spec's
    anti-black-box requirement)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_id: UUID
    company_entity_id: UUID
    alert_date: date
    triggered_at: datetime
    composite_score: float | None
    signal_summary: str
    source_event_ids: list[str]


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    canonical_name: str
    entity_type: EntityType
    sector: str | None = None
    industry: str | None = None
    country: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: EventType
    primary_entity_id: UUID
    event_time: datetime | None
    publication_time: datetime
    confidence: float
    source_id: UUID


class InstitutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    canonical_name: str
    manager_type: InstitutionalManagerType
    form13f_file_number: str | None = None


class InstitutionalHoldingOut(BaseModel):
    """Deliberately keeps `period_of_report` and `publication_time`
    separate fields (never a single "date") — spec §13 requires every
    institutional observation to show both "as of" and "filed on", since
    13F reporting lag routinely runs 30-45 days."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    institution_entity_id: UUID
    institution_name: str
    company_entity_id: UUID
    period_of_report: date
    publication_time: datetime
    shares_held: Decimal
    market_value_usd: Decimal
    shares_change: Decimal | None
    position_status: InstitutionalPositionStatus


class BeneficialOwnershipDisclosureOut(BaseModel):
    """Keeps `event_date` (the triggering ownership event) and
    `publication_time` (when EDGAR accepted the filing) separate, same
    reasoning as InstitutionalHoldingOut — a 13D/13G can lag its event_date
    by days. `stated_purpose` is the filer's own Item 4 narrative verbatim
    (13D only) — a fact, not this system's interpretation."""

    model_config = ConfigDict(from_attributes=True)

    event_id: UUID
    filer_entity_id: UUID
    filer_name: str
    company_entity_id: UUID
    schedule_type: ScheduleType
    filer_type_code: str | None
    shares_beneficially_owned: Decimal | None
    percent_of_class: Decimal | None
    sole_voting_power: Decimal | None
    shared_voting_power: Decimal | None
    sole_dispositive_power: Decimal | None
    shared_dispositive_power: Decimal | None
    event_date: date | None
    publication_time: datetime
    stated_purpose: str | None
    is_joint_filing: bool


class ScoreComponentOut(BaseModel):
    name: str
    value: float | None
    weight: float
    explanation: str


class TransactionEvidenceOut(BaseModel):
    event_id: UUID
    insider_entity_id: UUID
    insider_name: str
    is_officer: bool
    is_director: bool
    is_ten_percent_owner: bool
    transaction_date: date
    shares_transacted: Decimal
    price_per_share: Decimal | None
    dollar_value: Decimal | None
    is_10b5_1_plan: bool
    source_url: str | None
    accession: str | None


class InsiderRadarEntryOut(BaseModel):
    company_entity_id: UUID
    company_name: str
    as_of: datetime
    window_start: datetime
    window_days: int
    composite_score: float | None
    components: list[ScoreComponentOut]
    confidence: float
    confidence_notes: list[str]
    window_total_dollar_value: Decimal
    distinct_insiders: int
    distinct_senior_insiders: int
    total_transactions: int
    baseline_sample_size: int
    baseline_percentile: float | None
    baseline_z_score: float | None
    evidence: list[TransactionEvidenceOut]
    explanation: list[str]

    @classmethod
    def from_score(cls, score: InsiderConvictionScore) -> "InsiderRadarEntryOut":
        return cls(
            company_entity_id=score.company_entity_id,
            company_name=score.company_name,
            as_of=score.as_of,
            window_start=score.window_start,
            window_days=score.window_days,
            composite_score=score.composite_score,
            components=[
                ScoreComponentOut(name=c.name, value=c.value, weight=c.weight, explanation=c.explanation)
                for c in score.components
            ],
            confidence=score.confidence,
            confidence_notes=score.confidence_notes,
            window_total_dollar_value=score.window_total_dollar_value,
            distinct_insiders=score.distinct_insiders,
            distinct_senior_insiders=score.distinct_senior_insiders,
            total_transactions=score.total_transactions,
            baseline_sample_size=score.baseline_sample_size,
            baseline_percentile=score.baseline_percentile,
            baseline_z_score=score.baseline_z_score,
            evidence=[
                TransactionEvidenceOut(
                    event_id=e.event_id,
                    insider_entity_id=e.insider_entity_id,
                    insider_name=e.insider_name,
                    is_officer=e.is_officer,
                    is_director=e.is_director,
                    is_ten_percent_owner=e.is_ten_percent_owner,
                    transaction_date=e.transaction_date,
                    shares_transacted=e.shares_transacted,
                    price_per_share=e.price_per_share,
                    dollar_value=e.dollar_value,
                    is_10b5_1_plan=e.is_10b5_1_plan,
                    source_url=e.source_url,
                    accession=e.accession,
                )
                for e in score.evidence
            ],
            explanation=score.explanation,
        )


class HoldingEvidenceOut(BaseModel):
    event_id: UUID
    institution_entity_id: UUID
    institution_name: str
    position_status: InstitutionalPositionStatus
    period_of_report: date
    publication_time: datetime
    shares_held: Decimal
    shares_change: Decimal | None
    market_value_usd: Decimal
    accumulated_value_estimate: Decimal
    source_url: str | None
    accession: str | None


class InstitutionalRadarEntryOut(BaseModel):
    company_entity_id: UUID
    company_name: str
    as_of: datetime
    window_start: datetime
    window_days: int
    composite_score: float | None
    components: list[ScoreComponentOut]
    confidence: float
    confidence_notes: list[str]
    window_total_dollar_value: Decimal
    distinct_accumulating_institutions: int
    distinct_distributing_institutions: int
    baseline_sample_size: int
    baseline_percentile: float | None
    baseline_z_score: float | None
    evidence: list[HoldingEvidenceOut]
    explanation: list[str]

    @classmethod
    def from_score(cls, score: InstitutionalAccumulationScore) -> "InstitutionalRadarEntryOut":
        return cls(
            company_entity_id=score.company_entity_id,
            company_name=score.company_name,
            as_of=score.as_of,
            window_start=score.window_start,
            window_days=score.window_days,
            composite_score=score.composite_score,
            components=[
                ScoreComponentOut(name=c.name, value=c.value, weight=c.weight, explanation=c.explanation)
                for c in score.components
            ],
            confidence=score.confidence,
            confidence_notes=score.confidence_notes,
            window_total_dollar_value=score.window_total_dollar_value,
            distinct_accumulating_institutions=score.distinct_accumulating_institutions,
            distinct_distributing_institutions=score.distinct_distributing_institutions,
            baseline_sample_size=score.baseline_sample_size,
            baseline_percentile=score.baseline_percentile,
            baseline_z_score=score.baseline_z_score,
            evidence=[
                HoldingEvidenceOut(
                    event_id=e.event_id,
                    institution_entity_id=e.institution_entity_id,
                    institution_name=e.institution_name,
                    position_status=e.position_status,
                    period_of_report=e.period_of_report,
                    publication_time=e.publication_time,
                    shares_held=e.shares_held,
                    shares_change=e.shares_change,
                    market_value_usd=e.market_value_usd,
                    accumulated_value_estimate=e.accumulated_value_estimate,
                    source_url=e.source_url,
                    accession=e.accession,
                )
                for e in score.evidence
            ],
            explanation=score.explanation,
        )


class ShortInterestEvidenceOut(BaseModel):
    event_id: UUID
    settlement_date: date
    publication_time: datetime
    current_short_position: Decimal
    previous_short_position: Decimal | None
    change_percent: Decimal | None
    days_to_cover: Decimal | None
    source_url: str | None


class ShortInterestRadarEntryOut(BaseModel):
    """Only rising short interest is scored — see
    capint.scoring.short_interest_acceleration's module docstring for why
    short covering (a falling cycle) never appears here."""

    company_entity_id: UUID
    company_name: str
    ticker: str
    as_of: datetime
    composite_score: float | None
    components: list[ScoreComponentOut]
    confidence: float
    confidence_notes: list[str]
    latest_settlement_date: date
    latest_change_percent: Decimal
    latest_days_to_cover: Decimal | None
    baseline_sample_size: int
    baseline_percentile: float | None
    baseline_z_score: float | None
    consecutive_increasing_cycles: int
    evidence: list[ShortInterestEvidenceOut]
    explanation: list[str]

    @classmethod
    def from_score(cls, score: ShortInterestAccelerationScore) -> "ShortInterestRadarEntryOut":
        return cls(
            company_entity_id=score.company_entity_id,
            company_name=score.company_name,
            ticker=score.ticker,
            as_of=score.as_of,
            composite_score=score.composite_score,
            components=[
                ScoreComponentOut(name=c.name, value=c.value, weight=c.weight, explanation=c.explanation)
                for c in score.components
            ],
            confidence=score.confidence,
            confidence_notes=score.confidence_notes,
            latest_settlement_date=score.latest_settlement_date,
            latest_change_percent=score.latest_change_percent,
            latest_days_to_cover=score.latest_days_to_cover,
            baseline_sample_size=score.baseline_sample_size,
            baseline_percentile=score.baseline_percentile,
            baseline_z_score=score.baseline_z_score,
            consecutive_increasing_cycles=score.consecutive_increasing_cycles,
            evidence=[
                ShortInterestEvidenceOut(
                    event_id=e.event_id,
                    settlement_date=e.settlement_date,
                    publication_time=e.publication_time,
                    current_short_position=e.current_short_position,
                    previous_short_position=e.previous_short_position,
                    change_percent=e.change_percent,
                    days_to_cover=e.days_to_cover,
                    source_url=e.source_url,
                )
                for e in score.evidence
            ],
            explanation=score.explanation,
        )


class ConvergenceEntryOut(BaseModel):
    """Deliberately carries every family's score side by side (or None),
    never a single blended number — spec §33. See
    capint.convergence.engine's module docstring for what this three-family
    check does and doesn't mean — in particular, `label`/`label_explanation`
    characterize only the insider/institutional relationship; they do not
    yet incorporate `short_interest_score`."""

    company_entity_id: UUID
    company_name: str
    label: ConvergenceLabel
    label_explanation: str
    insider_score: InsiderRadarEntryOut | None
    institutional_score: InstitutionalRadarEntryOut | None
    institutional_accumulating_institutions: int
    institutional_distributing_institutions: int
    short_interest_score: ShortInterestRadarEntryOut | None

    @classmethod
    def from_entry(cls, entry: ConvergenceEntry) -> "ConvergenceEntryOut":
        return cls(
            company_entity_id=entry.company_entity_id,
            company_name=entry.company_name,
            label=entry.label,
            label_explanation=entry.label_explanation,
            insider_score=InsiderRadarEntryOut.from_score(entry.insider_score) if entry.insider_score else None,
            institutional_score=(
                InstitutionalRadarEntryOut.from_score(entry.institutional_score)
                if entry.institutional_score
                else None
            ),
            institutional_accumulating_institutions=entry.institutional_accumulating_institutions,
            institutional_distributing_institutions=entry.institutional_distributing_institutions,
            short_interest_score=(
                ShortInterestRadarEntryOut.from_score(entry.short_interest_score)
                if entry.short_interest_score
                else None
            ),
        )
