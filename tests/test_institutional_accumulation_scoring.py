"""Tests for capint.scoring.institutional_accumulation. Mirrors
tests/test_insider_conviction_scoring.py's structure and hand-checked-math
philosophy."""

from datetime import date
from decimal import Decimal

from capint.models.institution import InstitutionalPositionStatus
from capint.scoring.institutional_accumulation import score_company_institutional_accumulation
from tests.fixtures.synthetic import (
    dt,
    make_company,
    make_institution,
    make_institutional_holding_event,
    make_sec_source,
)


def test_no_accumulation_returns_none(session):
    source = make_sec_source(session)
    company = make_company(session)
    session.commit()

    score = score_company_institutional_accumulation(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score is None


def test_new_position_scores_full_market_value(session):
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

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=365
    )
    assert score is not None
    assert score.window_total_dollar_value == Decimal("50000")
    assert score.distinct_accumulating_institutions == 1
    new_money = next(c for c in score.components if c.name == "new_money_share")
    assert new_money.value == 100.0  # entirely a brand-new position


def test_increased_position_dollar_value_is_prorated_by_implied_price(session):
    source = make_sec_source(session)
    company = make_company(session)
    institution = make_institution(session)

    # 500 new shares out of 1000 total, total value $60,000 -> implied price $60/share
    # -> incremental value estimate = 500 * $60 = $30,000.
    make_institutional_holding_event(
        session,
        company=company,
        institution=institution,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="1000",
        market_value_usd="60000",
        shares_change="500",
        position_status=InstitutionalPositionStatus.INCREASED,
    )
    session.commit()

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=365
    )
    assert score.window_total_dollar_value == Decimal("30000")
    new_money = next(c for c in score.components if c.name == "new_money_share")
    assert new_money.value == 0.0  # entirely an addition, not a brand-new position


def test_insufficient_baseline_leaves_magnitude_component_unscored(session):
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

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=90, baseline_lookback_days=30
    )
    assert score.baseline_sample_size == 0
    magnitude = next(c for c in score.components if c.name == "magnitude_vs_history")
    assert magnitude.value is None
    assert any("too short" in note for note in score.confidence_notes)
    assert score.composite_score is not None  # still computable from the other components


def test_magnitude_vs_history_percentile_and_z_with_full_baseline(session):
    source = make_sec_source(session)
    company = make_company(session)
    institution = make_institution(session)

    as_of = dt(2026, 6, 1)
    window_days = 30
    baseline_lookback_days = 90  # exactly 3 x 30-day buckets before window_start (2026-05-02)

    bucket_values = [
        (date(2026, 4, 15), "8000"),  # bucket [2026-04-02, 2026-05-02)
        (date(2026, 3, 15), "9000"),  # bucket [2026-03-03, 2026-04-02)
        (date(2026, 2, 15), "11000"),  # bucket [2026-02-01, 2026-03-03)
    ]
    for period, value in bucket_values:
        make_institutional_holding_event(
            session,
            company=company,
            institution=institution,
            source=source,
            period_of_report=period,
            publication_time=dt(period.year, period.month, period.day),
            shares_held="1",
            market_value_usd=value,
            shares_change=None,
            position_status=InstitutionalPositionStatus.NEW,
        )

    make_institutional_holding_event(
        session,
        company=company,
        institution=institution,
        source=source,
        period_of_report=date(2026, 5, 20),
        publication_time=dt(2026, 5, 21),
        shares_held="1",
        market_value_usd="100000",
        shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    session.commit()

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=as_of, window_days=window_days, baseline_lookback_days=baseline_lookback_days
    )
    assert score.baseline_sample_size == 3
    assert score.baseline_percentile == 1.0
    assert score.baseline_z_score > 10
    magnitude = next(c for c in score.components if c.name == "magnitude_vs_history")
    assert magnitude.value == 100.0


def test_breadth_component_reflects_distinct_institutions(session):
    source = make_sec_source(session)
    company = make_company(session)
    alice_fund = make_institution(session, name="Alice Capital (SYNTHETIC)")
    bob_fund = make_institution(session, name="Bob Capital (SYNTHETIC)")

    for institution in (alice_fund, bob_fund):
        make_institutional_holding_event(
            session,
            company=company,
            institution=institution,
            source=source,
            period_of_report=date(2026, 3, 31),
            publication_time=dt(2026, 5, 15),
            shares_held="1000",
            market_value_usd="10000",
            shares_change=None,
            position_status=InstitutionalPositionStatus.NEW,
        )
    session.commit()

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=365
    )
    assert score.distinct_accumulating_institutions == 2
    breadth = next(c for c in score.components if c.name == "breadth")
    assert breadth.value == 60.0  # 30 * 2


def test_consensus_component_reflects_accumulating_vs_distributing(session):
    source = make_sec_source(session)
    company = make_company(session)
    buyer = make_institution(session, name="Buyer Capital (SYNTHETIC)")
    seller = make_institution(session, name="Seller Capital (SYNTHETIC)")

    make_institutional_holding_event(
        session,
        company=company,
        institution=buyer,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="1000",
        market_value_usd="10000",
        shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    make_institutional_holding_event(
        session,
        company=company,
        institution=seller,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="500",
        market_value_usd="5000",
        shares_change="-500",
        position_status=InstitutionalPositionStatus.DECREASED,
    )
    session.commit()

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=365
    )
    assert score.distinct_accumulating_institutions == 1
    assert score.distinct_distributing_institutions == 1
    consensus = next(c for c in score.components if c.name == "consensus")
    assert consensus.value == 50.0  # 1 accumulating / (1 accumulating + 1 distributing)


def test_point_in_time_correctness_excludes_not_yet_published_holding(session):
    source = make_sec_source(session)
    company = make_company(session)
    institution = make_institution(session)

    make_institutional_holding_event(
        session,
        company=company,
        institution=institution,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 6, 15),  # disclosed after as_of
        shares_held="1000",
        market_value_usd="50000",
        shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    session.commit()

    score = score_company_institutional_accumulation(
        session, company.entity_id, as_of=dt(2026, 6, 1), window_days=365
    )
    assert score is None
