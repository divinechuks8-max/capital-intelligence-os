import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class AlertRuleType(str, enum.Enum):
    """Which computed signal a rule watches. Each type reuses an existing
    scoring/convergence module untouched — a rule only decides what counts
    as "worth surfacing", never recomputes a score itself (spec's ALERT
    stage sits after SIGNALS/CONVERGENCE in the pipeline, consuming their
    output, not replacing it)."""

    INSIDER_CONVICTION_THRESHOLD = "INSIDER_CONVICTION_THRESHOLD"
    INSTITUTIONAL_ACCUMULATION_THRESHOLD = "INSTITUTIONAL_ACCUMULATION_THRESHOLD"
    SHORT_INTEREST_ACCELERATION_THRESHOLD = "SHORT_INTEREST_ACCELERATION_THRESHOLD"
    CONVERGENCE_LABEL = "CONVERGENCE_LABEL"


class WebhookFormat(str, enum.Enum):
    """Which JSON shape to POST when a rule delivers an alert. GENERIC is
    this system's own plain alert payload; SLACK is Slack's documented
    Incoming Webhook shape (`{"text": "..."}`), so a rule can point
    directly at a Slack workspace's own webhook URL with no separate
    Slack API account or OAuth app on this system's side — the user
    creates that URL themselves, in their own workspace, and it's
    governed by their own Slack terms, not a service this system
    integrates with under its own credentials."""

    GENERIC = "GENERIC"
    SLACK = "SLACK"


class AlertRule(UUIDPKMixin, CreatedAtMixin, Base):
    """A user-configured research criterion (Phase 13, pipeline's ALERT
    stage) — e.g. "insider conviction score >= 75" or "convergence label
    is INSIDER_AND_INSTITUTIONAL_ACCUMULATING". Created via the CLI, not
    the API — this system's API surface is read-only everywhere else, and
    a rule is user configuration, not ingested external data.

    Deliberately simple, threshold/label-based rules only — no compound
    boolean logic across multiple signal types in one rule (a research
    platform combining several rules' outputs is a human/future-phase
    responsibility, not something this MVP tries to encode declaratively).

    **Delivery (Phase 16) is webhook-only, deliberately.** `webhook_url`
    is a plain outbound HTTP endpoint the user supplies and controls —
    this system never holds an account, API key, or credential with any
    third-party notification service, so there's no vendor Terms of
    Service to evaluate (the lesson from this project's Alpha Vantage/
    Finnhub/Etherscan removals). Email delivery was considered and not
    built for the same reason: it would need either user-supplied SMTP
    credentials (a real, viable future increment) or a chosen
    transactional-email API vendor whose terms haven't been reviewed. A
    user who wants email today can point `webhook_url` at any
    webhook-to-email bridge they choose (their own account, their own
    terms) — this system's job stops at "POST a JSON payload to this
    URL."
    """

    __tablename__ = "alert_rules"

    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    rule_type: Mapped[AlertRuleType] = mapped_column(Enum(AlertRuleType, name="alert_rule_type"), nullable=False)
    min_composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Only for rule_type=CONVERGENCE_LABEL: which ConvergenceLabel string
    # values (capint.convergence.engine.ConvergenceLabel) trigger this rule.
    convergence_labels: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    webhook_format: Mapped[WebhookFormat] = mapped_column(
        Enum(WebhookFormat, name="webhook_format"), nullable=False, default=WebhookFormat.GENERIC
    )

    alerts: Mapped[list["Alert"]] = relationship(back_populates="rule")


class Alert(UUIDPKMixin, CreatedAtMixin, Base):
    """One rule firing for one company on one evaluation date (Phase 13).

    Not an extension of Event — an Alert is this system's OWN derived
    research artifact, not an observation sourced from an external
    provenance chain (Source/Document), so it doesn't participate in that
    model. `source_event_ids` instead links back to the underlying
    evidence events the triggering score was built from, for traceability
    (spec's "never a bare number without a why").

    Deduplicated per (rule, company, alert_date) — re-running evaluation
    on the same day for a still-triggering company is a no-op, but a
    later day's evaluation creates a new row if it still triggers,
    functioning as a simple "still elevated as of this date" log rather
    than a single mutable "currently active" flag.

    **Delivery (Phase 16)**: `delivery_attempted`/`delivery_succeeded`/
    `delivery_error` record the outcome of a single, synchronous, one-shot
    webhook POST attempt made at the moment this Alert was created — not a
    retry queue or delivery guarantee. If a rule has no `webhook_url`
    configured, delivery is simply never attempted (all three fields stay
    at their defaults) — this Alert row itself is still the durable
    record; delivery is a best-effort notification on top of it, and
    `GET /api/v1/alerts` remains the reliable way to know what fired.
    """

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("rule_id", "company_entity_id", "alert_date", name="uq_alert_per_rule_company_date"),)

    rule_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("alert_rules.id"), nullable=False, index=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    alert_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    delivery_attempted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivery_succeeded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    rule: Mapped["AlertRule"] = relationship(back_populates="alerts")
