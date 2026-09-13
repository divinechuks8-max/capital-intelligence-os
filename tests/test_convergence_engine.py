from datetime import date

from capint.convergence.engine import ConvergenceLabel, compute_convergence
from capint.models.institution import InstitutionalPositionStatus
from tests.fixtures.synthetic import (
    dt,
    make_company,
    make_insider_purchase_event,
    make_institution,
    make_institutional_holding_event,
    make_person,
    make_sec_source,
)


def test_insider_and_institutional_both_accumulating_is_convergent(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)
    institution = make_institution(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        shares="1000",
        price="50.00",
    )
    make_institutional_holding_event(
        session,
        company=company,
        institution=institution,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="1000",
        market_value_usd="50000",
        shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    session.commit()

    entries = compute_convergence(session, as_of=dt(2026, 6, 1))
    entry = next(e for e in entries if e.company_entity_id == company.entity_id)
    assert entry.label == ConvergenceLabel.INSIDER_AND_INSTITUTIONAL_ACCUMULATING
    assert entry.insider_score is not None
    assert entry.institutional_score is not None


def test_insider_buying_while_institutions_distribute_is_mixed(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)
    institution = make_institution(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        shares="1000",
        price="50.00",
    )
    make_institutional_holding_event(
        session,
        company=company,
        institution=institution,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="0",
        market_value_usd="0",
        shares_change="-5000",
        position_status=InstitutionalPositionStatus.EXITED,
    )
    session.commit()

    entries = compute_convergence(session, as_of=dt(2026, 6, 1))
    entry = next(e for e in entries if e.company_entity_id == company.entity_id)
    assert entry.label == ConvergenceLabel.MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING
    assert entry.insider_score is not None
    assert entry.institutional_score is None  # no accumulation at all -> no score object
    assert entry.institutional_distributing_institutions == 1


def test_insider_only(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        shares="1000",
        price="50.00",
    )
    session.commit()

    entries = compute_convergence(session, as_of=dt(2026, 6, 1))
    entry = next(e for e in entries if e.company_entity_id == company.entity_id)
    assert entry.label == ConvergenceLabel.INSIDER_ONLY
    assert entry.institutional_score is None


def test_institutional_only(session):
    source = make_sec_source(session)
    company = make_company(session)
    institution = make_institution(session)

    make_institutional_holding_event(
        session,
        company=company,
        institution=institution,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="1000",
        market_value_usd="50000",
        shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    session.commit()

    entries = compute_convergence(session, as_of=dt(2026, 6, 1))
    entry = next(e for e in entries if e.company_entity_id == company.entity_id)
    assert entry.label == ConvergenceLabel.INSTITUTIONAL_ONLY
    assert entry.insider_score is None


def test_convergent_and_mixed_rank_above_single_signal_entries(session):
    source = make_sec_source(session)
    person = make_person(session)
    institution = make_institution(session)

    convergent_co = make_company(session, name="Convergent Co (SYNTHETIC)")
    insider_only_co = make_company(session, name="Insider Only Co (SYNTHETIC)")

    make_insider_purchase_event(
        session, company=convergent_co, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="1000", price="50.00",
    )
    make_institutional_holding_event(
        session, company=convergent_co, institution=institution, source=source,
        period_of_report=date(2026, 3, 31), publication_time=dt(2026, 5, 15),
        shares_held="1000", market_value_usd="50000", shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    make_insider_purchase_event(
        session, company=insider_only_co, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="1000", price="50.00",
    )
    session.commit()

    entries = compute_convergence(session, as_of=dt(2026, 6, 1))
    ranked_ids = [e.company_entity_id for e in entries]
    assert ranked_ids.index(convergent_co.entity_id) < ranked_ids.index(insider_only_co.entity_id)
