from datetime import date

from sqlalchemy import select

from capint.alerting.engine import evaluate_all_active_rules, evaluate_rule
from capint.alerting.rules import create_or_update_alert_rule
from capint.models.alert import Alert, AlertRuleType
from capint.models.institution import InstitutionalPositionStatus
from tests.fixtures.synthetic import (
    dt,
    make_company,
    make_insider_purchase_event,
    make_institution,
    make_institutional_holding_event,
    make_person,
    make_sec_source,
    make_short_interest_snapshot_event,
)


def test_insider_conviction_threshold_rule_creates_alert(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="100000", price="50.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="High insider conviction", rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD, min_composite_score=0.0
    )

    created = evaluate_rule(session, rule, as_of=dt(2026, 6, 1))
    assert created == 1

    alert = session.execute(select(Alert).where(Alert.rule_id == rule.id)).scalar_one()
    assert alert.company_entity_id == company.entity_id
    assert alert.alert_date == date(2026, 6, 1)
    assert alert.composite_score is not None
    assert "Insider conviction score" in alert.signal_summary
    assert len(alert.source_event_ids) == 1


def test_rule_below_threshold_creates_no_alert(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="10", price="1.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="Very high bar", rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD, min_composite_score=99999.0
    )
    created = evaluate_rule(session, rule, as_of=dt(2026, 6, 1))
    assert created == 0
    assert session.execute(select(Alert)).scalars().all() == []


def test_reevaluating_same_day_is_idempotent(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="100000", price="50.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="High insider conviction", rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD, min_composite_score=0.0
    )

    first = evaluate_rule(session, rule, as_of=dt(2026, 6, 1, 9))
    second = evaluate_rule(session, rule, as_of=dt(2026, 6, 1, 17))  # same calendar date, later time
    assert first == 1
    assert second == 0
    assert len(session.execute(select(Alert)).scalars().all()) == 1


def test_later_day_reevaluation_creates_a_new_alert(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="100000", price="50.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="High insider conviction", rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD, min_composite_score=0.0
    )

    first = evaluate_rule(session, rule, as_of=dt(2026, 6, 1))
    second = evaluate_rule(session, rule, as_of=dt(2026, 6, 2))
    assert first == 1
    assert second == 1
    assert len(session.execute(select(Alert)).scalars().all()) == 2


def test_convergence_label_rule(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)
    institution = make_institution(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="1000", price="50.00",
    )
    make_institutional_holding_event(
        session, company=company, institution=institution, source=source,
        period_of_report=date(2026, 3, 31), publication_time=dt(2026, 5, 15),
        shares_held="1000", market_value_usd="50000", shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session,
        name="Insider+institutional agreement",
        rule_type=AlertRuleType.CONVERGENCE_LABEL,
        convergence_labels=["INSIDER_AND_INSTITUTIONAL_ACCUMULATING"],
    )
    created = evaluate_rule(session, rule, as_of=dt(2026, 6, 1))
    assert created == 1

    alert = session.execute(select(Alert).where(Alert.rule_id == rule.id)).scalar_one()
    assert alert.company_entity_id == company.entity_id
    assert alert.composite_score is None  # convergence rules don't carry one blended score
    assert len(alert.source_event_ids) == 2  # one insider evidence event + one institutional


def test_short_interest_acceleration_threshold_rule(session):
    source = make_sec_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        current_short_position="1100000",
        previous_short_position="1000000",
        change_percent="10.00",
        days_to_cover="2.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="Rising short interest", rule_type=AlertRuleType.SHORT_INTEREST_ACCELERATION_THRESHOLD, min_composite_score=0.0
    )
    created = evaluate_rule(session, rule, as_of=dt(2026, 6, 1))
    assert created == 1


def test_evaluate_all_active_rules_skips_inactive_rules(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="100000", price="50.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="High insider conviction", rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD, min_composite_score=0.0
    )
    rule.is_active = False
    session.commit()

    summary = evaluate_all_active_rules(session, as_of=dt(2026, 6, 1))
    assert summary.rules_evaluated == 0
    assert summary.alerts_created == 0
