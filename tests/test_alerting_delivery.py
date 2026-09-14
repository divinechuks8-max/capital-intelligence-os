import httpx

from capint.alerting.delivery import _build_payload, deliver_alert
from capint.models.alert import Alert, AlertRule, AlertRuleType, WebhookFormat
from tests.fixtures.synthetic import dt


def _make_rule(**kwargs) -> AlertRule:
    return AlertRule(
        name=kwargs.pop("name", "Test rule"),
        rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD,
        min_composite_score=0.0,
        **kwargs,
    )


def _make_alert(**kwargs) -> Alert:
    import uuid

    return Alert(
        id=uuid.uuid4(),
        rule_id=uuid.uuid4(),
        company_entity_id=uuid.uuid4(),
        alert_date=dt(2026, 6, 1).date(),
        triggered_at=dt(2026, 6, 1, 9),
        composite_score=87.5,
        signal_summary="Insider conviction score 87.5 met or exceeded threshold 75.0.",
        source_event_ids=[str(uuid.uuid4())],
        delivery_attempted=False,
        delivery_succeeded=False,
        delivery_error=None,
        **kwargs,
    )


def test_build_payload_generic_format():
    rule = _make_rule(webhook_format=WebhookFormat.GENERIC)
    alert = _make_alert()

    payload = _build_payload(alert, rule)

    assert payload["rule_name"] == "Test rule"
    assert payload["alert_id"] == str(alert.id)
    assert payload["composite_score"] == 87.5
    assert payload["signal_summary"] == alert.signal_summary
    assert payload["source_event_ids"] == alert.source_event_ids


def test_build_payload_slack_format():
    rule = _make_rule(name="High insider conviction", webhook_format=WebhookFormat.SLACK)
    alert = _make_alert()

    payload = _build_payload(alert, rule)

    assert set(payload.keys()) == {"text"}
    assert "High insider conviction" in payload["text"]
    assert alert.signal_summary in payload["text"]


def test_deliver_alert_is_noop_when_no_webhook_url():
    rule = _make_rule(webhook_url=None)
    alert = _make_alert()

    deliver_alert(alert, rule)

    assert alert.delivery_attempted is False
    assert alert.delivery_succeeded is False
    assert alert.delivery_error is None


def test_deliver_alert_success():
    rule = _make_rule(webhook_url="https://example.com/hook", webhook_format=WebhookFormat.GENERIC)
    alert = _make_alert()

    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    deliver_alert(alert, rule, client=client)

    assert alert.delivery_attempted is True
    assert alert.delivery_succeeded is True
    assert alert.delivery_error is None
    assert len(captured_requests) == 1
    assert str(captured_requests[0].url) == "https://example.com/hook"


def test_deliver_alert_records_http_error_status():
    rule = _make_rule(webhook_url="https://example.com/hook", webhook_format=WebhookFormat.GENERIC)
    alert = _make_alert()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    deliver_alert(alert, rule, client=client)

    assert alert.delivery_attempted is True
    assert alert.delivery_succeeded is False
    assert alert.delivery_error == "HTTP 500"


def test_deliver_alert_records_network_exception():
    rule = _make_rule(webhook_url="https://example.com/hook", webhook_format=WebhookFormat.GENERIC)
    alert = _make_alert()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    deliver_alert(alert, rule, client=client)

    assert alert.delivery_attempted is True
    assert alert.delivery_succeeded is False
    assert alert.delivery_error is not None
    assert "ConnectError" in alert.delivery_error
