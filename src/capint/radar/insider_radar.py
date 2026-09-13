"""Insider Radar (spec §52): "strongest discretionary insider purchases,
right now (or as of some point in the past)."

Two steps, deliberately kept separate: (1) find which companies even have
a candidate — at least one discretionary open-market purchase in the
window — then (2) score only those candidates. Scoring every company in
the database on every radar call would be wasteful and, worse, would
produce a misleadingly-precise score of near-zero for companies with no
insider activity at all, which capint.scoring.insider_conviction correctly
refuses to do (it returns None for "nothing to score").
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.event import Event, EventType
from capint.models.insider import InsiderTransaction, InsiderTransactionType
from capint.scoring.insider_conviction import (
    DEFAULT_BASELINE_LOOKBACK_DAYS,
    DEFAULT_WINDOW_DAYS,
    MIN_BASELINE_BUCKETS,
    InsiderConvictionScore,
    score_company_insider_conviction,
)


def _candidate_company_ids(session: Session, as_of: datetime, window_start: datetime) -> list:
    stmt = (
        select(Event.primary_entity_id)
        .join(InsiderTransaction, InsiderTransaction.event_id == Event.id)
        .where(
            Event.event_type == EventType.INSIDER_PURCHASE,
            InsiderTransaction.transaction_type == InsiderTransactionType.OPEN_MARKET_PURCHASE,
            Event.publication_time <= as_of,
            Event.event_time >= window_start,
            Event.event_time < as_of,
        )
        .distinct()
    )
    return [row[0] for row in session.execute(stmt).all()]


def compute_insider_radar(
    session: Session,
    as_of: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    baseline_lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
    min_baseline_buckets: int = MIN_BASELINE_BUCKETS,
    top_n: int = 25,
) -> list[InsiderConvictionScore]:
    as_of = as_of or datetime.now(timezone.utc)
    window_start = as_of - timedelta(days=window_days)

    scores = []
    for company_id in _candidate_company_ids(session, as_of, window_start):
        score = score_company_insider_conviction(
            session,
            company_id,
            as_of,
            window_days=window_days,
            baseline_lookback_days=baseline_lookback_days,
            min_baseline_buckets=min_baseline_buckets,
        )
        if score is not None:
            scores.append(score)

    # composite_score can only be None if every component were (it never is,
    # in practice — breadth/persistence/discretion always compute once there's
    # at least one qualifying transaction). Guarded anyway rather than assumed.
    scores.sort(key=lambda s: (s.composite_score is None, -(s.composite_score or 0)))
    return scores[:top_n]
