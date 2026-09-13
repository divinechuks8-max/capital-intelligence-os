"""Insider-conviction scoring (Phase 3, spec §12).

Scores *discretionary* open-market insider buying for one company over a
trailing window, as of a point in time. Deliberately narrow in scope:

- Only InsiderTransactionType.OPEN_MARKET_PURCHASE counts as "conviction"
  buying (spec §11/§12 — option exercises, tax-withholding, gifts, and
  compensation awards are not a discretionary bet on the stock and must
  never be folded into the same number).
- Never produces a bare opaque score (spec §33): every composite is
  returned alongside its named components, each with a plain-English
  explanation, so "why is this here?" (spec §69) always has an answer.
- "How unusual is this" is answered by comparing this window's total
  dollar value against the company's OWN trailing history of equal-length
  windows (spec §28), using the median/MAD (a robust statistic that isn't
  thrown off by one huge historical outlier) rather than mean/stdev. If
  there isn't enough history to trust that comparison, the result says so
  explicitly (spec §78) instead of guessing.
- This module computes on demand from current DB state; it does not
  persist a "signals" table. Nothing here prevents adding a snapshot/cache
  layer later (e.g. for historical-analog search), but that's a deliberate
  Phase 4+ concern, not built prematurely.

Conviction weights below (0.35 / 0.30 / 0.15 / 0.20) are a configurable
starting heuristic, not statistically fitted — spec §48 ("who is right?")
is the later phase that would validate or revise them against realized
outcomes. Don't read more precision into them than that.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.company import Company
from capint.models.entity import Entity
from capint.models.event import Event, EventType
from capint.models.insider import InsiderTransaction, InsiderTransactionType
from capint.models.person import PersonCompanyRole

DEFAULT_WINDOW_DAYS = 90
DEFAULT_BASELINE_LOOKBACK_DAYS = 730
MIN_BASELINE_BUCKETS = 4

_COMPONENT_WEIGHTS = {
    "size_vs_history": 0.35,
    "breadth": 0.30,
    "persistence": 0.15,
    "discretion": 0.20,
}


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: float | None  # 0-100, or None when not computable
    weight: float  # weight actually used in the composite (0 if excluded)
    explanation: str


@dataclass(frozen=True)
class TransactionEvidence:
    event_id: uuid.UUID
    insider_entity_id: uuid.UUID
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


@dataclass
class InsiderConvictionScore:
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
    distinct_insiders: int
    distinct_senior_insiders: int
    total_transactions: int

    baseline_sample_size: int
    baseline_percentile: float | None
    baseline_z_score: float | None

    evidence: list[TransactionEvidence] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)


def _dollar_value(shares: Decimal, price: Decimal | None) -> Decimal | None:
    return shares * price if price is not None else None


def _fetch_purchase_rows(
    session: Session,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[Event, InsiderTransaction, Entity]]:
    """Discretionary open-market purchases whose *event_time* falls in
    [window_start, window_end) — gated on Event.publication_time <= as_of,
    the same rule enforced by capint.temporal.as_of_public, so this never
    sees a filing that wasn't yet public knowledge at `as_of`."""
    stmt = (
        select(Event, InsiderTransaction, Entity)
        .join(InsiderTransaction, InsiderTransaction.event_id == Event.id)
        .join(Entity, Entity.id == InsiderTransaction.insider_entity_id)
        .where(
            Event.primary_entity_id == company_entity_id,
            Event.event_type == EventType.INSIDER_PURCHASE,
            InsiderTransaction.transaction_type == InsiderTransactionType.OPEN_MARKET_PURCHASE,
            Event.publication_time <= as_of,
            Event.event_time >= window_start,
            Event.event_time < window_end,
        )
        .order_by(Event.event_time)
    )
    return list(session.execute(stmt).all())


def _window_total(session: Session, company_entity_id: uuid.UUID, as_of, start, end) -> Decimal:
    total = Decimal("0")
    for _event, txn, _person in _fetch_purchase_rows(session, company_entity_id, as_of, start, end):
        value = _dollar_value(txn.shares_transacted, txn.price_per_share)
        if value is not None:
            total += value
    return total


def _baseline_bucket_totals(
    session: Session,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    window_start: datetime,
    window_days: int,
    baseline_lookback_days: int,
) -> list[Decimal]:
    """Non-overlapping historical windows of the same length immediately
    preceding `window_start`, each summed the same way as the scoring
    window — so the comparison is window-total vs window-total, not
    single-transaction vs window-total."""
    totals: list[Decimal] = []
    earliest = window_start - timedelta(days=baseline_lookback_days)
    bucket_end = window_start
    while True:
        bucket_start = bucket_end - timedelta(days=window_days)
        if bucket_start < earliest:
            break
        totals.append(_window_total(session, company_entity_id, as_of, bucket_start, bucket_end))
        bucket_end = bucket_start
    return totals


def _robust_percentile_and_z(baseline: list[Decimal], value: Decimal) -> tuple[float, float]:
    values = [float(v) for v in baseline]
    v = float(value)
    percentile = sum(1 for x in values if x <= v) / len(values)
    median = statistics.median(values)
    mad = statistics.median([abs(x - median) for x in values])
    if mad == 0:
        z = 0.0 if v == median else (10.0 if v > median else -10.0)
    else:
        z = 0.6745 * (v - median) / mad
    return percentile, z


def score_company_insider_conviction(
    session: Session,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    window_days: int = DEFAULT_WINDOW_DAYS,
    baseline_lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
    min_baseline_buckets: int = MIN_BASELINE_BUCKETS,
) -> InsiderConvictionScore | None:
    """Returns None if the company had zero discretionary open-market
    purchases in the window — there is nothing to score, and a radar
    should simply not list it, not show a score of 0 (0 would imply
    "actively un-bought", which isn't what "no data" means)."""
    company = session.get(Company, company_entity_id)
    if company is None:
        raise ValueError(f"No Company with entity_id={company_entity_id}")
    company_entity = session.get(Entity, company_entity_id)

    window_start = as_of - timedelta(days=window_days)
    rows = _fetch_purchase_rows(session, company_entity_id, as_of, window_start, as_of)
    if not rows:
        return None

    evidence: list[TransactionEvidence] = []
    window_total = Decimal("0")
    insiders_seen: dict[uuid.UUID, dict] = {}
    priced_count = 0
    dollar_10b5_1 = Decimal("0")

    for event, txn, insider in rows:
        value = _dollar_value(txn.shares_transacted, txn.price_per_share)
        if value is not None:
            window_total += value
            priced_count += 1
            if txn.is_10b5_1_plan:
                dollar_10b5_1 += value

        role = session.execute(
            select(PersonCompanyRole).where(
                PersonCompanyRole.person_entity_id == insider.id,
                PersonCompanyRole.company_entity_id == company_entity_id,
            )
        ).scalar_one_or_none()
        is_officer = bool(role and role.is_officer)
        is_director = bool(role and role.is_director)
        is_ten_pct = bool(role and role.is_ten_percent_owner)

        rec = insiders_seen.setdefault(
            insider.id, {"count": 0, "senior": is_officer or is_director or is_ten_pct}
        )
        rec["count"] += 1

        evidence.append(
            TransactionEvidence(
                event_id=event.id,
                insider_entity_id=insider.id,
                insider_name=insider.canonical_name,
                is_officer=is_officer,
                is_director=is_director,
                is_ten_percent_owner=is_ten_pct,
                transaction_date=event.event_time.date() if event.event_time else as_of.date(),
                shares_transacted=txn.shares_transacted,
                price_per_share=txn.price_per_share,
                dollar_value=value,
                is_10b5_1_plan=txn.is_10b5_1_plan,
                source_url=event.document.url if event.document else None,
                accession=event.document.external_id if event.document else None,
            )
        )

    distinct_insiders = len(insiders_seen)
    distinct_senior = sum(1 for r in insiders_seen.values() if r["senior"])
    total_transactions = len(rows)

    # --- size_vs_history: this window's total vs. the company's own trailing windows ---
    baseline = _baseline_bucket_totals(
        session, company_entity_id, as_of, window_start, window_days, baseline_lookback_days
    )
    baseline_percentile: float | None = None
    baseline_z: float | None = None
    if len(baseline) >= min_baseline_buckets:
        baseline_percentile, baseline_z = _robust_percentile_and_z(baseline, window_total)
        size_score = max(0.0, min(100.0, baseline_percentile * 100))
        size_explanation = (
            f"This window's ${window_total:,.0f} in open-market buying is at the "
            f"{baseline_percentile * 100:.0f}th percentile of this company's own trailing "
            f"{len(baseline)} comparable {window_days}-day windows (robust z={baseline_z:.1f})."
        )
    else:
        size_score = None
        size_explanation = (
            f"Only {len(baseline)} prior comparable window(s) available (need "
            f"{min_baseline_buckets}) - not enough history to say how unusual this is."
        )

    # --- breadth: how many insiders, and how senior ---
    breadth_score = min(100.0, 25 * distinct_insiders + 15 * distinct_senior)
    breadth_explanation = (
        f"{distinct_insiders} distinct insider(s) bought, {distinct_senior} of them "
        f"officer/director/10%-owner."
    )

    # --- persistence: repeat buying by the same insiders, not a single trade ---
    avg_txns_per_insider = total_transactions / distinct_insiders if distinct_insiders else 0.0
    persistence_score = max(0.0, min(100.0, (avg_txns_per_insider - 1) * 50))
    persistence_explanation = (
        f"Averaged {avg_txns_per_insider:.1f} purchase(s) per insider in the window "
        f"({total_transactions} transaction(s) across {distinct_insiders} insider(s))."
    )

    # --- discretion: discount buying done under a pre-scheduled 10b5-1 plan ---
    pct_10b5_1 = float(dollar_10b5_1 / window_total) if window_total > 0 else 0.0
    discretion_score = max(0.0, 100.0 - pct_10b5_1 * 60)
    discretion_explanation = (
        f"{pct_10b5_1 * 100:.0f}% of priced dollar volume was under a disclosed Rule 10b5-1 "
        f"plan (pre-scheduled, so less indicative of a spontaneous decision)."
    )

    components = [
        ScoreComponent("size_vs_history", size_score, _COMPONENT_WEIGHTS["size_vs_history"], size_explanation),
        ScoreComponent("breadth", breadth_score, _COMPONENT_WEIGHTS["breadth"], breadth_explanation),
        ScoreComponent("persistence", persistence_score, _COMPONENT_WEIGHTS["persistence"], persistence_explanation),
        ScoreComponent("discretion", discretion_score, _COMPONENT_WEIGHTS["discretion"], discretion_explanation),
    ]

    usable = [c for c in components if c.value is not None]
    weight_total = sum(c.weight for c in usable)
    composite = sum(c.value * c.weight for c in usable) / weight_total if weight_total > 0 else None

    price_completeness = priced_count / total_transactions if total_transactions else 0.0
    baseline_sufficiency = min(1.0, len(baseline) / min_baseline_buckets) if min_baseline_buckets else 1.0
    confidence = min(price_completeness, 0.5 + 0.5 * baseline_sufficiency) if total_transactions else 0.0

    confidence_notes = []
    if price_completeness < 1.0:
        confidence_notes.append(
            f"{total_transactions - priced_count} of {total_transactions} transaction(s) had no "
            "disclosed price and were excluded from dollar-value totals."
        )
    if len(baseline) < min_baseline_buckets:
        confidence_notes.append("Historical baseline for this company is too short to trust the anomaly comparison.")

    explanation = [
        f"{distinct_insiders} insider(s) made {total_transactions} discretionary open-market "
        f"purchase(s) totaling ${window_total:,.0f} in the {window_days} days before {as_of.date()}.",
        size_explanation,
        breadth_explanation,
        persistence_explanation,
        discretion_explanation,
    ]

    return InsiderConvictionScore(
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
        distinct_insiders=distinct_insiders,
        distinct_senior_insiders=distinct_senior,
        total_transactions=total_transactions,
        baseline_sample_size=len(baseline),
        baseline_percentile=baseline_percentile,
        baseline_z_score=baseline_z,
        evidence=evidence,
        explanation=explanation,
    )
