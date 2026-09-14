"""Short Interest Radar (Phase 13): "strongest short-interest acceleration,
right now (or as of some point in the past)."

Same two-step shape as capint.radar.insider_radar and
capint.radar.institutional_radar: find candidate companies (a reported
increase in the most recent settlement cycle), then score only those.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.event import Event, EventType
from capint.models.short_interest import ShortInterestSnapshot
from capint.scoring.short_interest_acceleration import (
    DEFAULT_LOOKBACK_CYCLES,
    MIN_BASELINE_CYCLES,
    ShortInterestAccelerationScore,
    score_company_short_interest_acceleration,
)


def candidate_company_ids(session: Session, as_of: datetime) -> list:
    """Every company whose MOST RECENT settlement cycle (as of `as_of`)
    reported a net increase — cheaper to check via a correlated subquery
    than fetching full history for every company that has ever appeared
    in short-interest data."""
    latest_per_company = (
        select(
            Event.primary_entity_id.label("company_entity_id"),
            ShortInterestSnapshot.settlement_date,
            ShortInterestSnapshot.change_percent,
        )
        .join(ShortInterestSnapshot, ShortInterestSnapshot.event_id == Event.id)
        .where(Event.event_type == EventType.SHORT_INTEREST_CHANGE, Event.publication_time <= as_of)
        .order_by(Event.primary_entity_id, ShortInterestSnapshot.settlement_date.desc())
    )
    rows = session.execute(latest_per_company).all()

    seen: set = set()
    candidates: list = []
    for company_entity_id, _settlement_date, change_percent in rows:
        if company_entity_id in seen:
            continue
        seen.add(company_entity_id)
        if change_percent is not None and change_percent > 0:
            candidates.append(company_entity_id)
    return candidates


def compute_short_interest_radar(
    session: Session,
    as_of: datetime | None = None,
    lookback_cycles: int = DEFAULT_LOOKBACK_CYCLES,
    min_baseline_cycles: int = MIN_BASELINE_CYCLES,
    top_n: int = 25,
) -> list[ShortInterestAccelerationScore]:
    as_of = as_of or datetime.now(timezone.utc)

    scores = []
    for company_id in candidate_company_ids(session, as_of):
        score = score_company_short_interest_acceleration(
            session,
            company_id,
            as_of,
            lookback_cycles=lookback_cycles,
            min_baseline_cycles=min_baseline_cycles,
        )
        if score is not None:
            scores.append(score)

    scores.sort(key=lambda s: (s.composite_score is None, -(s.composite_score or 0)))
    return scores[:top_n]
