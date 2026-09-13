from datetime import date

from capint.models.institution import InstitutionalPositionStatus
from capint.radar.institutional_radar import compute_institutional_radar
from tests.fixtures.synthetic import dt, make_company, make_institution, make_institutional_holding_event, make_sec_source


def test_radar_only_lists_companies_with_accumulation(session):
    source = make_sec_source(session)
    active_co = make_company(session, name="Active Co (SYNTHETIC)")
    quiet_co = make_company(session, name="Quiet Co (SYNTHETIC)")
    institution = make_institution(session)

    make_institutional_holding_event(
        session,
        company=active_co,
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

    scores = compute_institutional_radar(session, as_of=dt(2026, 6, 1), window_days=365)
    company_ids = {s.company_entity_id for s in scores}
    assert active_co.entity_id in company_ids
    assert quiet_co.entity_id not in company_ids


def test_radar_excludes_pure_distribution(session):
    """A company only institutions are exiting/reducing (no accumulation
    anywhere) must not appear — score_company_institutional_accumulation
    correctly returns None for it, and the radar must respect that."""
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
        shares_held="0",
        market_value_usd="0",
        shares_change="-1000",
        position_status=InstitutionalPositionStatus.EXITED,
    )
    session.commit()

    scores = compute_institutional_radar(session, as_of=dt(2026, 6, 1), window_days=365)
    assert scores == []


def test_radar_ranks_stronger_accumulation_first(session):
    source = make_sec_source(session)
    strong_co = make_company(session, name="Strong Co (SYNTHETIC)")
    weak_co = make_company(session, name="Weak Co (SYNTHETIC)")
    alice = make_institution(session, name="Alice Capital (SYNTHETIC)")
    bob = make_institution(session, name="Bob Capital (SYNTHETIC)")

    for institution in (alice, bob):
        make_institutional_holding_event(
            session,
            company=strong_co,
            institution=institution,
            source=source,
            period_of_report=date(2026, 3, 31),
            publication_time=dt(2026, 5, 15),
            shares_held="1000",
            market_value_usd="50000",
            shares_change=None,
            position_status=InstitutionalPositionStatus.NEW,
        )

    make_institutional_holding_event(
        session,
        company=weak_co,
        institution=alice,
        source=source,
        period_of_report=date(2026, 3, 31),
        publication_time=dt(2026, 5, 15),
        shares_held="10",
        market_value_usd="100",
        shares_change=None,
        position_status=InstitutionalPositionStatus.NEW,
    )
    session.commit()

    scores = compute_institutional_radar(session, as_of=dt(2026, 6, 1), window_days=365)
    ranked_ids = [s.company_entity_id for s in scores]
    assert ranked_ids.index(strong_co.entity_id) < ranked_ids.index(weak_co.entity_id)
