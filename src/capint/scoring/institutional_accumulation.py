"""Institutional-accumulation scoring (Phase 5, spec §13/§29).

Mirrors capint.scoring.insider_conviction's shape and honesty guarantees —
same four-part structure (named, independently-explained components, never
a bare composite; anomaly comparison against the company's OWN trailing
history via the shared capint.scoring.anomaly helper; explicit
"insufficient data" rather than fabricated precision) — applied to 13F
institutional holdings instead of Form 4 insider transactions.

Scope, deliberately narrow:

- Only accumulation (InstitutionalPositionStatus.NEW or INCREASED) counts
  toward the score, mirroring Phase 3's "only buying" decision. DECREASED/
  EXITED positions are still read (they feed the "consensus" component,
  since how many institutions are on each side of a stock is itself
  observable and useful context) but are never treated as a symmetric
  "bearish" signal — same reasoning as spec §84 for insiders.
- 13F does not report per-share cost basis for an *increase* to an
  existing position — only the position's new total shares and total
  market value. The dollar value of an increase is therefore estimated as
  shares_change * (market_value_usd / shares_held), i.e. this period's
  implied per-share price applied to the incremental shares. This is an
  approximation (documented, not hidden): it assumes the whole position
  was valued at this period's price, which is exactly true for a NEW
  position and only approximately true for an addition to an existing one.
- No institution-quality weighting (spec §14: hedge fund vs. pension vs.
  index fund) — every InstitutionalManager defaults to OTHER/unclassified
  in Phase 4, and this module doesn't invent a classification it doesn't
  have data for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.company import Company
from capint.models.entity import Entity
from capint.models.event import Event, EventType
from capint.models.institution import InstitutionalHolding, InstitutionalPositionStatus
from capint.scoring import anomaly

DEFAULT_WINDOW_DAYS = 365  # 13F is quarterly; one year gives a real chance of catching several filings
DEFAULT_BASELINE_LOOKBACK_DAYS = 1460  # 4 years -> up to 4 non-overlapping trailing year-windows
MIN_BASELINE_BUCKETS = 3

_COMPONENT_WEIGHTS = {
    "magnitude_vs_history": 0.35,
    "breadth": 0.30,
    "consensus": 0.20,
    "new_money_share": 0.15,
}

_ACCUMULATING = (InstitutionalPositionStatus.NEW, InstitutionalPositionStatus.INCREASED)
_DISTRIBUTING = (InstitutionalPositionStatus.DECREASED, InstitutionalPositionStatus.EXITED)


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: float | None
    weight: float
    explanation: str


@dataclass(frozen=True)
class HoldingEvidence:
    event_id: uuid.UUID
    institution_entity_id: uuid.UUID
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


@dataclass
class InstitutionalAccumulationScore:
    company_entity_id: uuid.UUID
    company_name: str
    as_of: datetime
    window_start: datetime
    window_days: int

    composite_score: float | None
    components: list[ScoreComponent]

    confidence: float
    confidence_notes: list[str]

    window_total_dollar_value: Decimal
    distinct_accumulating_institutions: int
    distinct_distributing_institutions: int

    baseline_sample_size: int
    baseline_percentile: float | None
    baseline_z_score: float | None

    evidence: list[HoldingEvidence] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)


def _accumulated_value(holding: InstitutionalHolding) -> Decimal:
    if holding.position_status == InstitutionalPositionStatus.NEW:
        return holding.market_value_usd
    if holding.position_status == InstitutionalPositionStatus.INCREASED:
        if holding.shares_held and holding.shares_change:
            implied_price = holding.market_value_usd / holding.shares_held
            return holding.shares_change * implied_price
        return Decimal("0")
    return Decimal("0")


def _fetch_rows(
    session: Session,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    window_start: datetime,
    window_end: datetime,
    statuses: tuple[InstitutionalPositionStatus, ...],
) -> list[tuple[Event, InstitutionalHolding, Entity]]:
    """Holdings whose *event_time* (= period_of_report) falls in
    [window_start, window_end) — gated on Event.publication_time <= as_of,
    mirroring the same rule capint.temporal.as_of_public enforces."""
    stmt = (
        select(Event, InstitutionalHolding, Entity)
        .join(InstitutionalHolding, InstitutionalHolding.event_id == Event.id)
        .join(Entity, Entity.id == InstitutionalHolding.institution_entity_id)
        .where(
            Event.primary_entity_id == company_entity_id,
            Event.event_type == EventType.INSTITUTIONAL_POSITION_CHANGE,
            InstitutionalHolding.position_status.in_(statuses),
            Event.publication_time <= as_of,
            Event.event_time >= window_start,
            Event.event_time < window_end,
        )
        .order_by(Event.event_time)
    )
    return list(session.execute(stmt).all())


def _window_total(session: Session, company_entity_id: uuid.UUID, as_of, start, end) -> Decimal:
    total = Decimal("0")
    for _event, holding, _institution in _fetch_rows(session, company_entity_id, as_of, start, end, _ACCUMULATING):
        total += _accumulated_value(holding)
    return total


def score_company_institutional_accumulation(
    session: Session,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    baseline_lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
    min_baseline_buckets: int = MIN_BASELINE_BUCKETS,
) -> InstitutionalAccumulationScore | None:
    """Returns None if no institution accumulated (NEW or INCREASED) a
    position in this company within the window — nothing to score, and a
    radar should simply not list it (see insider_conviction's identical
    reasoning for why this isn't a score of 0)."""
    company = session.get(Company, company_entity_id)
    if company is None:
        raise ValueError(f"No Company with entity_id={company_entity_id}")
    company_entity = session.get(Entity, company_entity_id)

    window_start = as_of - timedelta(days=window_days)
    accumulating_rows = _fetch_rows(session, company_entity_id, as_of, window_start, as_of, _ACCUMULATING)
    if not accumulating_rows:
        return None
    distributing_rows = _fetch_rows(session, company_entity_id, as_of, window_start, as_of, _DISTRIBUTING)

    evidence: list[HoldingEvidence] = []
    window_total = Decimal("0")
    new_money_total = Decimal("0")
    accumulating_institutions: set[uuid.UUID] = set()

    for event, holding, institution in accumulating_rows:
        value = _accumulated_value(holding)
        window_total += value
        if holding.position_status == InstitutionalPositionStatus.NEW:
            new_money_total += value
        accumulating_institutions.add(institution.id)

        evidence.append(
            HoldingEvidence(
                event_id=event.id,
                institution_entity_id=institution.id,
                institution_name=institution.canonical_name,
                position_status=holding.position_status,
                period_of_report=holding.period_of_report,
                publication_time=event.publication_time,
                shares_held=holding.shares_held,
                shares_change=holding.shares_change,
                market_value_usd=holding.market_value_usd,
                accumulated_value_estimate=value,
                source_url=event.document.url if event.document else None,
                accession=event.document.external_id if event.document else None,
            )
        )

    distributing_institutions = {h.institution_entity_id for _e, h, _i in distributing_rows}
    distinct_accumulating = len(accumulating_institutions)
    distinct_distributing = len(distributing_institutions)

    # --- magnitude_vs_history: this window's accumulation vs. the company's own trailing windows ---
    baseline = anomaly.bucket_totals(
        window_start,
        window_days,
        baseline_lookback_days,
        lambda start, end: _window_total(session, company_entity_id, as_of, start, end),
    )
    baseline_percentile: float | None = None
    baseline_z: float | None = None
    if len(baseline) >= min_baseline_buckets:
        baseline_percentile, baseline_z = anomaly.robust_percentile_and_z(baseline, window_total)
        magnitude_score = max(0.0, min(100.0, baseline_percentile * 100))
        magnitude_explanation = (
            f"This window's ${window_total:,.0f} in estimated institutional accumulation is at "
            f"the {baseline_percentile * 100:.0f}th percentile of this company's own trailing "
            f"{len(baseline)} comparable {window_days}-day windows (robust z={baseline_z:.1f})."
        )
    else:
        magnitude_score = None
        magnitude_explanation = (
            f"Only {len(baseline)} prior comparable window(s) available (need "
            f"{min_baseline_buckets}) - not enough history to say how unusual this is."
        )

    # --- breadth: how many distinct institutions are accumulating ---
    breadth_score = min(100.0, 30 * distinct_accumulating)
    breadth_explanation = f"{distinct_accumulating} distinct institution(s) opened or added to a position."

    # --- consensus: accumulating vs. distributing institutions for the same company/window ---
    total_sides = distinct_accumulating + distinct_distributing
    consensus_score = (distinct_accumulating / total_sides * 100) if total_sides else 0.0
    consensus_explanation = (
        f"{distinct_accumulating} institution(s) accumulated vs {distinct_distributing} that "
        f"reduced or exited a position in the same window."
    )

    # --- new_money_share: brand-new positions vs. additions to an existing one ---
    new_money_share = float(new_money_total / window_total * 100) if window_total > 0 else 0.0
    new_money_explanation = (
        f"{new_money_share:.0f}% of estimated accumulated dollar value came from brand-new "
        f"positions (vs. additions to an existing one)."
    )

    components = [
        ScoreComponent(
            "magnitude_vs_history", magnitude_score, _COMPONENT_WEIGHTS["magnitude_vs_history"], magnitude_explanation
        ),
        ScoreComponent("breadth", breadth_score, _COMPONENT_WEIGHTS["breadth"], breadth_explanation),
        ScoreComponent("consensus", consensus_score, _COMPONENT_WEIGHTS["consensus"], consensus_explanation),
        ScoreComponent("new_money_share", new_money_share, _COMPONENT_WEIGHTS["new_money_share"], new_money_explanation),
    ]

    usable = [c for c in components if c.value is not None]
    weight_total = sum(c.weight for c in usable)
    composite = sum(c.value * c.weight for c in usable) / weight_total if weight_total > 0 else None

    baseline_sufficiency = min(1.0, len(baseline) / min_baseline_buckets) if min_baseline_buckets else 1.0
    confidence = 0.5 + 0.5 * baseline_sufficiency

    confidence_notes = []
    if len(baseline) < min_baseline_buckets:
        confidence_notes.append("Historical baseline for this company is too short to trust the anomaly comparison.")
    confidence_notes.append(
        "Dollar value of position increases is estimated from this period's implied per-share "
        "price, not a disclosed cost basis (13F doesn't report one) — see module docstring."
    )

    explanation = [
        f"{distinct_accumulating} institution(s) accumulated an estimated ${window_total:,.0f} "
        f"in the {window_days} days before {as_of.date()}.",
        magnitude_explanation,
        breadth_explanation,
        consensus_explanation,
        new_money_explanation,
    ]

    return InstitutionalAccumulationScore(
        company_entity_id=company_entity_id,
        company_name=company_entity.canonical_name if company_entity else "UNKNOWN",
        as_of=as_of,
        window_start=window_start,
        window_days=window_days,
        composite_score=composite,
        components=components,
        confidence=confidence,
        confidence_notes=confidence_notes,
        window_total_dollar_value=window_total,
        distinct_accumulating_institutions=distinct_accumulating,
        distinct_distributing_institutions=distinct_distributing,
        baseline_sample_size=len(baseline),
        baseline_percentile=baseline_percentile,
        baseline_z_score=baseline_z,
        evidence=evidence,
        explanation=explanation,
    )
