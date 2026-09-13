"""Institutional Radar (spec §52): "strongest institutional accumulation,
right now (or as of some point in the past)."

Same two-step shape as capint.radar.insider_radar: find candidate
companies (at least one accumulating position in the window), then score
only those — see that module's docstring for why.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.event import Event, EventType
from capint.models.institution import InstitutionalHolding, InstitutionalPositionStatus
from capint.scoring.institutional_accumulation import (
    DEFAULT_BASELINE_LOOKBACK_DAYS,
    DEFAULT_WINDOW_DAYS,
    MIN_BASELINE_BUCKETS,
    InstitutionalAccumulationScore,
    score_company_institutional_accumulation,
)

_ACCUMULATING = (InstitutionalPositionStatus.NEW, InstitutionalPositionStatus.INCREASED)


def candidate_company_ids(session: Session, as_of: datetime, window_start: datetime) -> list:
    stmt = (
        select(Event.primary_entity_id)
        .join(InstitutionalHolding, InstitutionalHolding.event_id == Event.id)
        .where(
            Event.event_type == EventType.INSTITUTIONAL_POSITION_CHANGE,
            InstitutionalHolding.position_status.in_(_ACCUMULATING),
            Event.publication_time <= as_of,
            Event.event_time >= window_start,
            Event.event_time < as_of,
        )
        .distinct()
    )
    return [row[0] for row in session.execute(stmt).all()]


def compute_institutional_radar(
    session: Session,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    baseline_lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
    min_baseline_buckets: int = MIN_BASELINE_BUCKETS,
    top_n: int = 25,
) -> list[InstitutionalAccumulationScore]:
    as_of = as_of or datetime.now(timezone.utc)
    window_start = as_of - timedelta(days=window_days)

    scores = []
    for company_id in candidate_company_ids(session, as_of, window_start):
        score = score_company_institutional_accumulation(
            session,
            company_id,
            as_of,
            window_days=window_days,
            baseline_lookback_days=baseline_lookback_days,
            min_baseline_buckets=min_baseline_buckets,
        )
        if score is not None:
            scores.append(score)

    scores.sort(key=lambda s: (s.composite_score is None, -(s.composite_score or 0)))
    return scores[:top_n]
