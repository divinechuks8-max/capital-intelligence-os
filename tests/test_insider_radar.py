from capint.radar.insider_radar import compute_insider_radar
from tests.fixtures.synthetic import dt, make_company, make_insider_purchase_event, make_person, make_sec_source


def test_radar_only_lists_companies_with_qualifying_purchases(session):
    source = make_sec_source(session)
    active_co = make_company(session, name="Active Co (SYNTHETIC)")
    quiet_co = make_company(session, name="Quiet Co (SYNTHETIC)")
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=active_co,
        person=person,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 5, 20),
        shares="1000",
        price="10.00",
    )
    session.commit()

    scores = compute_insider_radar(session, as_of=dt(2026, 6, 1), window_days=30)
    company_ids = {s.company_entity_id for s in scores}
    assert active_co.entity_id in company_ids
    assert quiet_co.entity_id not in company_ids


def test_radar_ranks_stronger_conviction_first(session):
    source = make_sec_source(session)
    strong_co = make_company(session, name="Strong Co (SYNTHETIC)")
    weak_co = make_company(session, name="Weak Co (SYNTHETIC)")
    alice = make_person(session, name="Alice (SYNTHETIC)")
    bob = make_person(session, name="Bob (SYNTHETIC)")
    carol = make_person(session, name="Carol (SYNTHETIC)")

    # Strong Co: three distinct insiders buying -> high breadth.
    for person in (alice, bob, carol):
        make_insider_purchase_event(
            session,
            company=strong_co,
            person=person,
            source=source,
            event_time=dt(2026, 5, 20),
            publication_time=dt(2026, 5, 20),
            shares="1000",
            price="10.00",
        )

    # Weak Co: a single small purchase.
    make_insider_purchase_event(
        session,
        company=weak_co,
        person=alice,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 5, 20),
        shares="10",
        price="10.00",
    )
    session.commit()

    scores = compute_insider_radar(session, as_of=dt(2026, 6, 1), window_days=30)
    ranked_ids = [s.company_entity_id for s in scores]
    assert ranked_ids.index(strong_co.entity_id) < ranked_ids.index(weak_co.entity_id)


def test_radar_respects_top_n(session):
    source = make_sec_source(session)
    person = make_person(session)
    for i in range(5):
        company = make_company(session, name=f"Company {i} (SYNTHETIC)")
        make_insider_purchase_event(
            session,
            company=company,
            person=person,
            source=source,
            event_time=dt(2026, 5, 20),
            publication_time=dt(2026, 5, 20),
            shares="100",
            price="10.00",
        )
    session.commit()

    scores = compute_insider_radar(session, as_of=dt(2026, 6, 1), window_days=30, top_n=2)
    assert len(scores) == 2
