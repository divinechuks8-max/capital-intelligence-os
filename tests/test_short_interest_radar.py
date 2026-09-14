from datetime import date

from capint.radar.short_interest_radar import compute_short_interest_radar
from tests.fixtures.synthetic import dt, make_company, make_sec_source, make_short_interest_snapshot_event


def test_radar_only_lists_companies_with_rising_short_interest(session):
    source = make_sec_source(session)
    rising_co = make_company(session, name="Rising Co (SYNTHETIC)")
    covering_co = make_company(session, name="Covering Co (SYNTHETIC)")

    make_short_interest_snapshot_event(
        session,
        company=rising_co,
        source=source,
        ticker="RISE",
        settlement_date=date(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        current_short_position="1100000",
        previous_short_position="1000000",
        change_percent="10.00",
        days_to_cover="2.00",
    )
    make_short_interest_snapshot_event(
        session,
        company=covering_co,
        source=source,
        ticker="COVR",
        settlement_date=date(2026, 5, 15),
        publication_time=dt(2026, 5, 16),
        current_short_position="900000",
        previous_short_position="1000000",
        change_percent="-10.00",
        days_to_cover="1.00",
    )
    session.commit()

    scores = compute_short_interest_radar(session, as_of=dt(2026, 6, 1))
    company_ids = {s.company_entity_id for s in scores}
    assert rising_co.entity_id in company_ids
    assert covering_co.entity_id not in company_ids


def test_radar_uses_only_the_most_recent_cycle_to_screen_candidates(session):
    """A company whose MOST RECENT cycle shows covering must not appear,
    even if an older cycle once rose."""
    source = make_sec_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 3, 15),
        publication_time=dt(2026, 3, 16),
        current_short_position="1100000",
        previous_short_position="1000000",
        change_percent="10.00",
        days_to_cover="2.00",
    )
    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=date(2026, 4, 15),
        publication_time=dt(2026, 4, 16),
        current_short_position="900000",
        previous_short_position="1100000",
        change_percent="-18.18",
        days_to_cover="1.00",
    )
    session.commit()

    scores = compute_short_interest_radar(session, as_of=dt(2026, 6, 1))
    assert scores == []


def test_radar_ranks_stronger_acceleration_first(session):
    source = make_sec_source(session)
    strong_co = make_company(session, name="Strong Co (SYNTHETIC)")
    weak_co = make_company(session, name="Weak Co (SYNTHETIC)")

    # 3 baseline cycles (satisfies MIN_BASELINE_CYCLES) + 1 scored cycle each.
    strong_cycles = [
        (date(2026, 1, 15), "5.00"),
        (date(2026, 2, 15), "6.00"),
        (date(2026, 3, 15), "7.00"),
        (date(2026, 4, 15), "50.00"),  # far above its own history
    ]
    weak_cycles = [
        (date(2026, 1, 15), "5.00"),
        (date(2026, 2, 15), "6.00"),
        (date(2026, 3, 15), "7.00"),
        (date(2026, 4, 15), "6.50"),  # within its own history's range, not a new high
    ]
    for settlement, change_percent in strong_cycles:
        make_short_interest_snapshot_event(
            session,
            company=strong_co,
            source=source,
            ticker="STRG",
            settlement_date=settlement,
            publication_time=dt(settlement.year, settlement.month, settlement.day + 1),
            current_short_position="1000000",
            previous_short_position="900000",
            change_percent=change_percent,
            days_to_cover="2.00",
        )
    for settlement, change_percent in weak_cycles:
        make_short_interest_snapshot_event(
            session,
            company=weak_co,
            source=source,
            ticker="WEAK",
            settlement_date=settlement,
            publication_time=dt(settlement.year, settlement.month, settlement.day + 1),
            current_short_position="1000000",
            previous_short_position="900000",
            change_percent=change_percent,
            days_to_cover="2.00",
        )
    session.commit()

    scores = compute_short_interest_radar(session, as_of=dt(2026, 6, 1))
    ranked_ids = [s.company_entity_id for s in scores]
    assert ranked_ids.index(strong_co.entity_id) < ranked_ids.index(weak_co.entity_id)
