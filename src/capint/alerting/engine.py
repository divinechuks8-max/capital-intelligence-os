"""Alert evaluation (Phase 13, pipeline's ALERT stage — the step between
SIGNALS/CONVERGENCE and HUMAN DECISION).

Deliberately thin: every rule type reuses an existing scoring/radar/
convergence module untouched and simply checks its output against a
threshold or label set — this module computes no signal of its own, only
whether to surface one that's already been computed (spec's "never a
bare number without a why": every Alert carries `signal_summary` and
`source_event_ids` pointing back to the real evidence, never a synthetic
justification).

Rules are evaluated against the FULL candidate universe, not just each
radar's default top_n=25 — a real signal below the top-25 cutoff for its
own radar must still be able to trigger a rule if it clears the rule's
own threshold, so this module calls each radar/convergence function with
a large `top_n` rather than relying on the default.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.convergence.engine import compute_convergence
from capint.models.alert import Alert, AlertRule, AlertRuleType
from capint.radar.insider_radar import compute_insider_radar
from capint.radar.institutional_radar import compute_institutional_radar
from capint.radar.short_interest_radar import compute_short_interest_radar

_CANDIDATE_UNIVERSE_LIMIT = 10_000  # effectively "no top_n cutoff" for rule evaluation


def _upsert_alert(
    session: Session,
    rule: AlertRule,
    company_entity_id: uuid.UUID,
    as_of: datetime,
    composite_score: float | None,
    signal_summary: str,
    source_event_ids: list[uuid.UUID],
) -> bool:
    """Returns False if an alert for this (rule, company, date) already
    exists (idempotent no-op) — see capint.models.alert.Alert's docstring
    for why the dedup key is per calendar date, not per underlying score
    value."""
    alert_date = as_of.date()
    existing = session.execute(
        select(Alert).where(
            Alert.rule_id == rule.id,
            Alert.company_entity_id == company_entity_id,
            Alert.alert_date == alert_date,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    session.add(
        Alert(
            rule_id=rule.id,
            company_entity_id=company_entity_id,
            alert_date=alert_date,
            triggered_at=as_of,
            composite_score=composite_score,
            signal_summary=signal_summary,
            source_event_ids=[str(e) for e in source_event_ids],
        )
    )
    session.flush()
    return True


def evaluate_rule(session: Session, rule: AlertRule, as_of: datetime) -> int:
    """Evaluates one active rule against current signal output as of
    `as_of`, persisting a new Alert for each newly-triggering company.
    Returns the number of new alerts created."""
    created = 0

    if rule.rule_type == AlertRuleType.INSIDER_CONVICTION_THRESHOLD:
        scores = compute_insider_radar(session, as_of=as_of, top_n=_CANDIDATE_UNIVERSE_LIMIT)
        for score in scores:
            if score.composite_score is None or rule.min_composite_score is None:
                continue
            if score.composite_score < rule.min_composite_score:
                continue
            summary = (
                f"Insider conviction score {score.composite_score:.1f} met or exceeded threshold "
                f"{rule.min_composite_score:.1f} ({score.distinct_insiders} distinct insider(s), "
                f"${score.window_total_dollar_value:,.0f} over {score.window_days} days)."
            )
            if _upsert_alert(
                session, rule, score.company_entity_id, as_of, score.composite_score, summary,
                [e.event_id for e in score.evidence],
            ):
                created += 1

    elif rule.rule_type == AlertRuleType.INSTITUTIONAL_ACCUMULATION_THRESHOLD:
        scores = compute_institutional_radar(session, as_of=as_of, top_n=_CANDIDATE_UNIVERSE_LIMIT)
        for score in scores:
            if score.composite_score is None or rule.min_composite_score is None:
                continue
            if score.composite_score < rule.min_composite_score:
                continue
            summary = (
                f"Institutional accumulation score {score.composite_score:.1f} met or exceeded "
                f"threshold {rule.min_composite_score:.1f} ({score.distinct_accumulating_institutions} "
                f"distinct institution(s), ${score.window_total_dollar_value:,.0f} over "
                f"{score.window_days} days)."
            )
            if _upsert_alert(
                session, rule, score.company_entity_id, as_of, score.composite_score, summary,
                [e.event_id for e in score.evidence],
            ):
                created += 1

    elif rule.rule_type == AlertRuleType.SHORT_INTEREST_ACCELERATION_THRESHOLD:
        scores = compute_short_interest_radar(session, as_of=as_of, top_n=_CANDIDATE_UNIVERSE_LIMIT)
        for score in scores:
            if score.composite_score is None or rule.min_composite_score is None:
                continue
            if score.composite_score < rule.min_composite_score:
                continue
            summary = (
                f"Short-interest acceleration score {score.composite_score:.1f} met or exceeded "
                f"threshold {rule.min_composite_score:.1f} (+{score.latest_change_percent:.2f}% in the "
                f"cycle settled {score.latest_settlement_date})."
            )
            if _upsert_alert(
                session, rule, score.company_entity_id, as_of, score.composite_score, summary,
                [e.event_id for e in score.evidence],
            ):
                created += 1

    elif rule.rule_type == AlertRuleType.CONVERGENCE_LABEL:
        watched_labels = set(rule.convergence_labels or [])
        entries = compute_convergence(session, as_of=as_of, top_n=_CANDIDATE_UNIVERSE_LIMIT)
        for entry in entries:
            if entry.label.value not in watched_labels:
                continue
            event_ids: list[uuid.UUID] = []
            if entry.insider_score is not None:
                event_ids.extend(e.event_id for e in entry.insider_score.evidence)
            if entry.institutional_score is not None:
                event_ids.extend(e.event_id for e in entry.institutional_score.evidence)
            if entry.short_interest_score is not None:
                event_ids.extend(e.event_id for e in entry.short_interest_score.evidence)
            if _upsert_alert(
                session, rule, entry.company_entity_id, as_of, None, entry.label_explanation, event_ids
            ):
                created += 1

    return created


@dataclass
class AlertEvaluationSummary:
    rules_evaluated: int = 0
    alerts_created: int = 0
    rule_errors: list[str] = field(default_factory=list)


def evaluate_all_active_rules(session: Session, as_of: datetime | None = None) -> AlertEvaluationSummary:
    as_of = as_of or datetime.now(timezone.utc)
    summary = AlertEvaluationSummary()

    rules = session.execute(select(AlertRule).where(AlertRule.is_active.is_(True))).scalars().all()
    for rule in rules:
        summary.rules_evaluated += 1
        try:
            summary.alerts_created += evaluate_rule(session, rule, as_of)
        except Exception as exc:  # noqa: BLE001 — one bad rule must not abort the batch
            summary.rule_errors.append(f"{rule.name}: {exc!r}")

    session.commit()
    return summary
