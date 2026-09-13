"""Tests for capint.scoring.insider_conviction.

Dollar amounts and dates below are chosen so every component's value can be
checked by hand against the module docstring's formulas — not just
"assert it runs".
"""

from decimal import Decimal

from capint.scoring.insider_conviction import score_company_insider_conviction
from tests.fixtures.synthetic import dt, make_company, make_insider_purchase_event, make_person, make_sec_source


def test_no_purchases_returns_none(session):
    source = make_sec_source(session)
    company = make_company(session)
    session.commit()

    score = score_company_insider_conviction(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score is None


def test_insufficient_baseline_leaves_size_component_unscored(session):
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
        shares="100",
        price="50.00",
    )
    session.commit()

    # baseline_lookback_days=45 with window_days=30 yields exactly 1 prior
    # bucket — below min_baseline_buckets (4), so size_vs_history must
    # honestly report "not enough history" rather than compare against a
    # near-empty baseline.
    score = score_company_insider_conviction(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=30, baseline_lookback_days=45
    )
    assert score is not None
    assert score.baseline_sample_size == 1
    size_component = next(c for c in score.components if c.name == "size_vs_history")
    assert size_component.value is None
    assert score.baseline_percentile is None
    assert any("too short" in note for note in score.confidence_notes)
    # composite still computable from the other three components
    assert score.composite_score is not None


def test_size_vs_history_percentile_and_z_with_full_baseline(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    as_of = dt(2026, 6, 1)
    window_days = 30
    baseline_lookback_days = 120  # exactly 4 x 30-day buckets before window_start (2026-05-02)

    # One purchase per historical bucket: values 8000, 9000, 11000, 12000 -> median 10000, MAD 1500.
    bucket_values = [
        (dt(2026, 4, 15), "8000.00"),  # bucket [2026-04-02, 2026-05-02)
        (dt(2026, 3, 15), "9000.00"),  # bucket [2026-03-03, 2026-04-02)
        (dt(2026, 2, 15), "11000.00"),  # bucket [2026-02-01, 2026-03-03)
        (dt(2026, 1, 15), "12000.00"),  # bucket [2026-01-02, 2026-02-01)
    ]
    for event_time, price in bucket_values:
        make_insider_purchase_event(
            session,
            company=company,
            person=person,
            source=source,
            event_time=event_time,
            publication_time=event_time,
            shares="1",
            price=price,
        )

    # Current window: one large purchase, well above every historical bucket.
    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 5, 21),
        shares="1",
        price="100000.00",
    )
    session.commit()

    score = score_company_insider_conviction(
        session, company.entity_id, as_of=as_of, window_days=window_days, baseline_lookback_days=baseline_lookback_days
    )
    assert score is not None
    assert score.baseline_sample_size == 4
    assert score.window_total_dollar_value == Decimal("100000.00")
    assert score.baseline_percentile == 1.0  # above all 4 historical buckets
    assert score.baseline_z_score > 10  # 0.6745 * (100000-10000)/1500 ≈ 40.5

    size_component = next(c for c in score.components if c.name == "size_vs_history")
    assert size_component.value == 100.0


def test_breadth_component_reflects_distinct_insiders(session):
    source = make_sec_source(session)
    company = make_company(session)
    alice = make_person(session, name="Alice (SYNTHETIC)")
    bob = make_person(session, name="Bob (SYNTHETIC)")

    for person in (alice, bob):
        make_insider_purchase_event(
            session,
            company=company,
            person=person,
            source=source,
            event_time=dt(2026, 5, 20),
            publication_time=dt(2026, 5, 20),
            shares="10",
            price="100.00",
        )
    session.commit()

    score = score_company_insider_conviction(session, company.entity_id, as_of=dt(2026, 6, 1), window_days=30)
    assert score.distinct_insiders == 2
    assert score.distinct_senior_insiders == 0  # no PersonCompanyRole recorded
    breadth = next(c for c in score.components if c.name == "breadth")
    assert breadth.value == 50.0  # 25*2 + 15*0


def test_persistence_component_reflects_repeat_buying(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    for day in (10, 15, 20):
        make_insider_purchase_event(
            session,
            company=company,
            person=person,
            source=source,
            event_time=dt(2026, 5, day),
            publication_time=dt(2026, 5, day),
            shares="10",
            price="100.00",
        )
    session.commit()

    score = score_company_insider_conviction(session, company.entity_id, as_of=dt(2026, 6, 1), window_days=30)
    assert score.total_transactions == 3
    assert score.distinct_insiders == 1
    persistence = next(c for c in score.components if c.name == "persistence")
    assert persistence.value == 100.0  # (3/1 - 1) * 50 = 100, capped


def test_discretion_component_discounts_10b5_1_plan_activity(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 5, 20),
        shares="100",
        price="100.00",
        is_10b5_1_plan=True,
    )
    session.commit()

    score = score_company_insider_conviction(session, company.entity_id, as_of=dt(2026, 6, 1), window_days=30)
    discretion = next(c for c in score.components if c.name == "discretion")
    assert discretion.value == 40.0  # 100% under plan -> 100 - 60


def test_missing_price_excluded_from_dollar_totals_but_flagged(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 5, 20),
        shares="500",
        price=None,
    )
    session.commit()

    score = score_company_insider_conviction(session, company.entity_id, as_of=dt(2026, 6, 1), window_days=30)
    assert score.window_total_dollar_value == Decimal("0")
    assert score.total_transactions == 1
    assert any("no disclosed price" in note for note in score.confidence_notes)


def test_point_in_time_correctness_excludes_not_yet_published_transaction(session):
    """A transaction that happened inside the window but wasn't disclosed
    until after `as_of` must not be visible to the score — mirrors the
    Phase 1 temporal-correctness guarantee, now enforced in the scoring
    layer too."""
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 6, 15),  # disclosed after as_of
        shares="100",
        price="50.00",
    )
    session.commit()

    score = score_company_insider_conviction(session, company.entity_id, as_of=dt(2026, 6, 1), window_days=30)
    assert score is None
