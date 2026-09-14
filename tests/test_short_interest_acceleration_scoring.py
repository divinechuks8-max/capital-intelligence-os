"""Tests for capint.scoring.short_interest_acceleration. Mirrors
tests/test_institutional_accumulation_scoring.py's structure and
hand-checked-math philosophy."""

from datetime import date

from capint.scoring.short_interest_acceleration import score_company_short_interest_acceleration
from tests.fixtures.synthetic import dt, make_company, make_sec_source, make_short_interest_snapshot_event


def test_no_snapshots_returns_none(session):
    company = make_company(session)
    session.commit()

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score is None


def test_falling_short_interest_returns_none(session):
    """Only rising short interest is scored — short covering is a
    different signal this module doesn't evaluate."""
    source = make_sec_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        current_short_position="900000",
        previous_short_position="1000000",
        change_percent="-10.00",
        days_to_cover="1.50",
    )
    session.commit()

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score is None


def test_point_in_time_correctness_excludes_not_yet_published_snapshot(session):
    source = make_sec_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 5, 15),
        publication_time=dt(2026, 6, 15),  # disclosed after as_of
        current_short_position="1100000",
        previous_short_position="1000000",
        change_percent="10.00",
        days_to_cover="2.00",
    )
    session.commit()

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score is None


def test_insufficient_baseline_leaves_magnitude_and_dtc_components_unscored(session):
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

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score.baseline_sample_size == 0
    magnitude = next(c for c in score.components if c.name == "magnitude_vs_history")
    days_to_cover_component = next(c for c in score.components if c.name == "days_to_cover_vs_history")
    assert magnitude.value is None
    assert days_to_cover_component.value is None
    assert any("too short" in note for note in score.confidence_notes)
    assert score.composite_score is not None  # still computable from the persistence component


def test_magnitude_and_days_to_cover_percentile_and_z_with_full_baseline(session):
    source = make_sec_source(session)
    company = make_company(session)

    baseline_cycles = [
        (date(2026, 1, 15), "5.00", "1.00"),
        (date(2026, 2, 15), "8.00", "1.20"),
        (date(2026, 3, 15), "10.00", "1.50"),
    ]
    for settlement, change_percent, days_to_cover in baseline_cycles:
        make_short_interest_snapshot_event(
            session,
            company=company,
            source=source,
            ticker="TEST",
            settlement_date=settlement,
            publication_time=dt(settlement.year, settlement.month, settlement.day + 1),
            current_short_position="1000000",
            previous_short_position="900000",
            change_percent=change_percent,
            days_to_cover=days_to_cover,
        )

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 4, 15),
        publication_time=dt(2026, 4, 16),
        current_short_position="2000000",
        previous_short_position="1000000",
        change_percent="20.00",
        days_to_cover="3.00",
    )
    session.commit()

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score.baseline_sample_size == 3
    assert score.baseline_percentile == 1.0
    # baseline [5, 8, 10]: median=8, MAD=median(|5-8|,|8-8|,|10-8|)=median(3,0,2)=2
    # z = 0.6745 * (20 - 8) / 2 = 4.047
    assert round(score.baseline_z_score, 3) == 4.047

    magnitude = next(c for c in score.components if c.name == "magnitude_vs_history")
    days_to_cover_component = next(c for c in score.components if c.name == "days_to_cover_vs_history")
    assert magnitude.value == 100.0
    assert days_to_cover_component.value == 100.0
    assert score.consecutive_increasing_cycles == 3  # all 3 baseline cycles also rose
    persistence = next(c for c in score.components if c.name == "persistence")
    assert persistence.value == 100.0  # min(100, 34*3)


def test_persistence_breaks_at_first_non_increasing_prior_cycle(session):
    source = make_sec_source(session)
    company = make_company(session)

    # Oldest -> newest: a decrease, then two increases, then the scored (latest) increase.
    cycles = [
        (date(2026, 1, 15), "-5.00"),
        (date(2026, 2, 15), "6.00"),
        (date(2026, 3, 15), "7.00"),
    ]
    for settlement, change_percent in cycles:
        make_short_interest_snapshot_event(
            session,
            company=company,
            source=source,
            ticker="TEST",
            settlement_date=settlement,
            publication_time=dt(settlement.year, settlement.month, settlement.day + 1),
            current_short_position="1000000",
            previous_short_position="900000",
            change_percent=change_percent,
            days_to_cover=None,
        )

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 4, 15),
        publication_time=dt(2026, 4, 16),
        current_short_position="1100000",
        previous_short_position="1000000",
        change_percent="15.00",
        days_to_cover=None,
    )
    session.commit()

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    # Immediately-preceding cycle (2026-03-15, +7.00) and the one before it
    # (2026-02-15, +6.00) both rose; the cycle before THAT (2026-01-15,
    # -5.00) did not, so the streak stops there.
    assert score.consecutive_increasing_cycles == 2


def test_evidence_and_latest_fields_reflect_most_recent_cycle(session):
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
        days_to_cover="2.50",
    )
    session.commit()

    score = score_company_short_interest_acceleration(session, company.entity_id, as_of=dt(2026, 6, 1))
    assert score.latest_settlement_date == date(2026, 5, 15)
    assert score.latest_change_percent == 10
    assert score.latest_days_to_cover == 2.5
    assert score.ticker == "TEST"
    assert len(score.evidence) == 1
    assert score.evidence[0].settlement_date == date(2026, 5, 15)
