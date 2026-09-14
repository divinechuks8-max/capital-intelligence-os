"""Alert rule management (Phase 13). Rules are user configuration, not
ingested external data — created via the CLI, never the API (this
system's API surface is read-only everywhere else)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.alert import AlertRule, AlertRuleType


def create_or_update_alert_rule(
    session: Session,
    name: str,
    rule_type: AlertRuleType,
    min_composite_score: float | None = None,
    convergence_labels: list[str] | None = None,
) -> AlertRule:
    """Get-or-create by name — re-running the same CLI command updates the
    existing rule's thresholds rather than creating a duplicate."""
    rule = session.execute(select(AlertRule).where(AlertRule.name == name)).scalar_one_or_none()
    if rule is None:
        rule = AlertRule(name=name, rule_type=rule_type)
        session.add(rule)
    else:
        rule.rule_type = rule_type
    rule.min_composite_score = min_composite_score
    rule.convergence_labels = convergence_labels
    rule.is_active = True
    session.flush()
    session.commit()
    return rule
