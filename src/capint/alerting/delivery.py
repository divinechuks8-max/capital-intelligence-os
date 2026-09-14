"""Webhook delivery for alerts (Phase 16, pipeline's ALERT delivery step
— the piece Phase 13's alerting layer left as poll-only).

**Deliberately webhook-only.** See capint.models.alert.AlertRule's
docstring for why: a webhook URL is outbound HTTP to an endpoint the
user supplies and controls, so this system never holds a third-party
notification-service account or API key of its own, and so has no
vendor Terms of Service to evaluate — the lesson from this project's
Alpha Vantage/Finnhub/Etherscan removals (Phase 15). Slack is supported
as a payload *format*, not a separate integration: `WebhookFormat.SLACK`
just shapes the JSON to match Slack's own documented Incoming Webhook
contract (`{"text": "..."}`), which the user points at a webhook URL
from their own Slack workspace.

**One-shot, synchronous, best-effort** — a single POST attempt made at
the moment an Alert is created, no retry queue or delivery guarantee.
See capint.models.alert.Alert's docstring for why that's an honestly-
scoped choice: the Alert row itself is the durable record regardless of
whether delivery succeeds, and `GET /api/v1/alerts` remains the reliable
way to know what fired.
"""

from __future__ import annotations

import httpx

from capint.models.alert import Alert, AlertRule, WebhookFormat

DEFAULT_TIMEOUT_SECONDS = 10.0


def _build_payload(alert: Alert, rule: AlertRule) -> dict:
    if rule.webhook_format == WebhookFormat.SLACK:
        text = (
            f"*{rule.name}* fired for company `{alert.company_entity_id}` on "
            f"{alert.alert_date}: {alert.signal_summary}"
        )
        return {"text": text}

    return {
        "rule_id": str(rule.id),
        "rule_name": rule.name,
        "alert_id": str(alert.id),
        "company_entity_id": str(alert.company_entity_id),
        "alert_date": alert.alert_date.isoformat(),
        "triggered_at": alert.triggered_at.isoformat(),
        "composite_score": alert.composite_score,
        "signal_summary": alert.signal_summary,
        "source_event_ids": alert.source_event_ids,
    }


def deliver_alert(alert: Alert, rule: AlertRule, client: httpx.Client | None = None) -> None:
    """Attempts one webhook POST for `alert`, mutating its
    `delivery_attempted`/`delivery_succeeded`/`delivery_error` fields in
    place — the caller is responsible for flushing/committing the
    session afterward. Does nothing (all three fields stay at their
    defaults) if `rule.webhook_url` isn't set."""
    if not rule.webhook_url:
        return

    alert.delivery_attempted = True
    owns_client = client is None
    http_client = client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
    try:
        resp = http_client.post(rule.webhook_url, json=_build_payload(alert, rule))
        if resp.status_code < 300:
            alert.delivery_succeeded = True
            alert.delivery_error = None
        else:
            alert.delivery_succeeded = False
            alert.delivery_error = f"HTTP {resp.status_code}"
    except Exception as exc:  # noqa: BLE001 — a bad webhook URL/network error must not crash alert evaluation
        alert.delivery_succeeded = False
        alert.delivery_error = repr(exc)
    finally:
        if owns_client:
            http_client.close()
