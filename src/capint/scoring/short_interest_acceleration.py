"""Short-interest acceleration scoring (Phase 13, spec §29's third
convergence family).

Mirrors capint.scoring.insider_conviction and
capint.scoring.institutional_accumulation's shape and honesty guarantees
(named, independently-explained components; never a bare composite;
anomaly comparison against the company's own trailing history via
capint.scoring.anomaly; explicit "insufficient data" rather than
fabricated precision) — applied to FINRA short-interest snapshots
(Phase 10) instead of Form 4 transactions or 13F holdings.

**Only rising short interest is scored, mirroring Phase 3/5's "only one
direction counts" convention** (spec §84's reasoning for insiders):
short covering (falling short interest) is a real, different, arguably
bullish signal this module does not score — a company's most recent
settlement cycle must show FINRA-reported `change_percent > 0` for
anything to be returned here. This is a deliberate scope choice: "short
interest acceleration" describes a build-up, not a symmetric two-sided
"short interest momentum" score.

Unlike the other two scorers, the comparison here needs no bucketed-sum
window (`capint.scoring.anomaly.bucket_totals`): each FINRA settlement
cycle is already a single point-in-time observation with its own
FINRA-reported `change_percent`, so this module compares that reported
figure directly against the company's own trailing cycles via
`anomaly.robust_percentile_and_z`, without any additional aggregation.

FINRA's short-interest regime (Phase 10) is a genuinely independent
disclosure source from Form 4 (Phase 2/3) and 13F (Phase 4/5) — different
filers, different regulator (FINRA vs. SEC directly), different
mechanics — so folding it into the Convergence Engine (Phase 5,
capint.convergence.engine) as a third family carries no signal
double-counting risk (spec §30-31) that the two-family design didn't
already carry.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.company import Company
from capint.models.entity import Entity
from capint.models.event import Event, EventType
from capint.models.short_interest import ShortInterestSnapshot
from capint.scoring import anomaly

DEFAULT_LOOKBACK_CYCLES = 12  # FINRA reports bi-monthly -> ~2 years of history
MIN_BASELINE_CYCLES = 3

_COMPONENT_WEIGHTS = {
    "magnitude_vs_history": 0.40,
    "days_to_cover_vs_history": 0.35,
    "persistence": 0.25,
}


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: float | None
    weight: float
    explanation: str


@dataclass(frozen=True)
class ShortInterestEvidence:
    event_id: uuid.UUID
    settlement_date: date
    publication_time: datetime
    current_short_position: Decimal
    previous_short_position: Decimal | None
    change_percent: Decimal | None
    days_to_cover: Decimal | None
    source_url: str | None


@dataclass
class ShortInterestAccelerationScore:
    company_entity_id: uuid.UUID
    company_name: str
    ticker: str
    as_of: datetime

    composite_score: float | None
    components: list[ScoreComponent]

    confidence: float
    confidence_notes: list[str]

    latest_settlement_date: date
    latest_change_percent: Decimal
    latest_days_to_cover: Decimal | None

    baseline_sample_size: int
    baseline_percentile: float | None
    baseline_z_score: float | None
    consecutive_increasing_cycles: int

    evidence: list[ShortInterestEvidence] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)


def _fetch_snapshots(
    session: Session, company_entity_id: uuid.UUID, as_of: datetime, limit: int
) -> list[tuple[Event, ShortInterestSnapshot]]:
    """Most recent `limit` snapshots as of `as_of` (point-in-time gated on
    Event.publication_time, same rule every other scorer in this system
    enforces), newest settlement_date first."""
    stmt = (
        select(Event, ShortInterestSnapshot)
        .join(ShortInterestSnapshot, ShortInterestSnapshot.event_id == Event.id)
        .where(
            Event.primary_entity_id == company_entity_id,
            Event.event_type == EventType.SHORT_INTEREST_CHANGE,
            Event.publication_time <= as_of,
        )
        .order_by(ShortInterestSnapshot.settlement_date.desc())
        .limit(limit)
    )
    return list(session.execute(stmt).all())


def score_company_short_interest_acceleration(
    session: Session,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    lookback_cycles: int = DEFAULT_LOOKBACK_CYCLES,
    min_baseline_cycles: int = MIN_BASELINE_CYCLES,
) -> ShortInterestAccelerationScore | None:
    """Returns None if the most recent settlement cycle isn't a reported
    increase, or if there's no short-interest history at all for this
    company — nothing to score, same "no score of 0" reasoning as every
    other scorer here."""
    company = session.get(Company, company_entity_id)
    if company is None:
        raise ValueError(f"No Company with entity_id={company_entity_id}")
    company_entity = session.get(Entity, company_entity_id)

    rows = _fetch_snapshots(session, company_entity_id, as_of, lookback_cycles + 1)
    if not rows:
        return None

    latest_event, latest_snapshot = rows[0]
    if latest_snapshot.change_percent is None or latest_snapshot.change_percent <= 0:
        return None

    baseline_rows = rows[1:]
    evidence = [
        ShortInterestEvidence(
            event_id=event.id,
            settlement_date=snapshot.settlement_date,
            publication_time=event.publication_time,
            current_short_position=snapshot.current_short_position,
            previous_short_position=snapshot.previous_short_position,
            change_percent=snapshot.change_percent,
            days_to_cover=snapshot.days_to_cover,
            source_url=event.document.url if event.document else None,
        )
        for event, snapshot in rows
    ]

    # --- magnitude_vs_history: this cycle's reported change_percent vs. the company's own trailing cycles ---
    baseline_change_percents = [s.change_percent for _e, s in baseline_rows if s.change_percent is not None]
    baseline_percentile: float | None = None
    baseline_z: float | None = None
    if len(baseline_change_percents) >= min_baseline_cycles:
        baseline_percentile, baseline_z = anomaly.robust_percentile_and_z(
            baseline_change_percents, latest_snapshot.change_percent
        )
        magnitude_score = max(0.0, min(100.0, baseline_percentile * 100))
        magnitude_explanation = (
            f"This cycle's FINRA-reported +{latest_snapshot.change_percent:.2f}% change is at the "
            f"{baseline_percentile * 100:.0f}th percentile of this company's own trailing "
            f"{len(baseline_change_percents)} settlement cycles (robust z={baseline_z:.1f})."
        )
    else:
        magnitude_score = None
        magnitude_explanation = (
            f"Only {len(baseline_change_percents)} prior cycle(s) available (need "
            f"{min_baseline_cycles}) - not enough history to say how unusual this is."
        )

    # --- days_to_cover_vs_history: current days-to-cover level vs. the company's own trailing cycles ---
    baseline_days_to_cover = [s.days_to_cover for _e, s in baseline_rows if s.days_to_cover is not None]
    if latest_snapshot.days_to_cover is not None and len(baseline_days_to_cover) >= min_baseline_cycles:
        dtc_percentile, _dtc_z = anomaly.robust_percentile_and_z(baseline_days_to_cover, latest_snapshot.days_to_cover)
        days_to_cover_score = max(0.0, min(100.0, dtc_percentile * 100))
        days_to_cover_explanation = (
            f"Days-to-cover of {latest_snapshot.days_to_cover:.2f} is at the {dtc_percentile * 100:.0f}th "
            f"percentile of this company's own trailing {len(baseline_days_to_cover)} cycles — higher "
            "implies more time for short positions to unwind, a factor in short-squeeze potential."
        )
    else:
        days_to_cover_score = None
        days_to_cover_explanation = "Not enough days-to-cover history for this company to compare against."

    # --- persistence: consecutive prior cycles that were also net increases ---
    consecutive = 0
    for _event, snapshot in baseline_rows:
        if snapshot.change_percent is not None and snapshot.change_percent > 0:
            consecutive += 1
        else:
            break
    persistence_score = min(100.0, 34 * consecutive)
    persistence_explanation = (
        f"{consecutive} consecutive prior settlement cycle(s) also showed a net increase in short "
        "interest, before this one."
    )

    components = [
        ScoreComponent(
            "magnitude_vs_history", magnitude_score, _COMPONENT_WEIGHTS["magnitude_vs_history"], magnitude_explanation
        ),
        ScoreComponent(
            "days_to_cover_vs_history",
            days_to_cover_score,
            _COMPONENT_WEIGHTS["days_to_cover_vs_history"],
            days_to_cover_explanation,
        ),
        ScoreComponent("persistence", persistence_score, _COMPONENT_WEIGHTS["persistence"], persistence_explanation),
    ]

    usable = [c for c in components if c.value is not None]
    weight_total = sum(c.weight for c in usable)
    composite = sum(c.value * c.weight for c in usable) / weight_total if weight_total > 0 else None

    baseline_sufficiency = min(1.0, len(baseline_change_percents) / min_baseline_cycles) if min_baseline_cycles else 1.0
    confidence = 0.5 + 0.5 * baseline_sufficiency

    confidence_notes = []
    if len(baseline_change_percents) < min_baseline_cycles:
        confidence_notes.append("Historical baseline for this company is too short to trust the anomaly comparison.")
    confidence_notes.append(
        "Only rising short interest is scored — short covering (a falling cycle) returns no score "
        "here, a different signal this module does not evaluate. See module docstring."
    )

    explanation = [
        f"Short interest rose {latest_snapshot.change_percent:.2f}% in the cycle settled "
        f"{latest_snapshot.settlement_date}, to {latest_snapshot.current_short_position:,.0f} shares.",
        magnitude_explanation,
        days_to_cover_explanation,
        persistence_explanation,
    ]

    return ShortInterestAccelerationScore(
        company_entity_id=company_entity_id,
        company_name=company_entity.canonical_name if company_entity else "UNKNOWN",
        ticker=latest_snapshot.ticker,
        as_of=as_of,
        composite_score=composite,
        components=components,
        confidence=confidence,
        confidence_notes=confidence_notes,
        latest_settlement_date=latest_snapshot.settlement_date,
        latest_change_percent=latest_snapshot.change_percent,
        latest_days_to_cover=latest_snapshot.days_to_cover,
        baseline_sample_size=len(baseline_change_percents),
        baseline_percentile=baseline_percentile,
        baseline_z_score=baseline_z,
        consecutive_increasing_cycles=consecutive,
        evidence=evidence,
        explanation=explanation,
    )
