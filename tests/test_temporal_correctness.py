"""Mandatory temporal-correctness tests (spec §8, §72).

These are not ordinary unit tests — they are the regression guard against
look-ahead bias, which would silently invalidate every backtest and every
"as of" research view built on top of this data layer. If one of these
fails, nothing downstream should be trusted until it's fixed.
"""

from capint.temporal import as_of_ingested, as_of_public
from tests.fixtures.synthetic import dt, make_company, make_insider_purchase_event, make_person, make_sec_source


def test_filing_published_later_is_invisible_to_earlier_as_of_query(session):
    """'A filing published March 10 must NOT appear in a March 1 historical
    simulation' — spec §72, verbatim."""
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 2, 25, 9, 0),  # actually happened Feb 25
        publication_time=dt(2026, 3, 10, 16, 0),  # not disclosed until Mar 10
    )
    session.commit()

    as_of_mar_1 = as_of_public(session, dt(2026, 3, 1), entity_id=company.entity_id)
    assert as_of_mar_1 == []

    as_of_mar_10_evening = as_of_public(session, dt(2026, 3, 10, 23, 59), entity_id=company.entity_id)
    assert len(as_of_mar_10_evening) == 1


def test_transaction_only_visible_after_its_own_publication_date(session):
    """'A transaction occurring March 1 but published March 5 should only
    become available after March 5' — spec §72, verbatim."""
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 3, 1, 10, 0),
        publication_time=dt(2026, 3, 5, 8, 0),
    )
    session.commit()

    assert as_of_public(session, dt(2026, 3, 4, 23, 59), entity_id=company.entity_id) == []
    assert as_of_public(session, dt(2026, 3, 5, 8, 0), entity_id=company.entity_id) != []
    assert as_of_public(session, dt(2026, 3, 5, 7, 59), entity_id=company.entity_id) == []


def test_ingestion_lag_is_tracked_independently_of_publication(session):
    """Our own pipeline can lag the public disclosure — as_of_ingested must
    reflect *our* retrieval time, which as_of_public must ignore entirely."""
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 3, 1, 10, 0),
        publication_time=dt(2026, 3, 5, 8, 0),
        retrieved_at=dt(2026, 3, 7, 12, 0),  # our scraper picked it up 2 days late
    )
    session.commit()

    # Publicly known as of Mar 5, but our system hadn't ingested it yet.
    assert len(as_of_public(session, dt(2026, 3, 6), entity_id=company.entity_id)) == 1
    assert as_of_ingested(session, dt(2026, 3, 6), entity_id=company.entity_id) == []

    # By Mar 7 our pipeline had caught up.
    assert len(as_of_ingested(session, dt(2026, 3, 7, 12, 0), entity_id=company.entity_id)) == 1


def test_event_time_alone_is_never_the_gate(session):
    """A naive implementation might filter on event_time instead of
    publication_time — that would leak future-disclosed information into
    the past. This asserts the actual (correct) behavior differs from that
    naive one whenever event_time and publication_time diverge."""
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 1, 1),
        publication_time=dt(2026, 6, 1),
    )
    session.commit()

    as_of_between = as_of_public(session, dt(2026, 3, 1), entity_id=company.entity_id)
    assert as_of_between == [], (
        "event_time (Jan 1) precedes as_of (Mar 1) but publication_time (Jun 1) does not — "
        "a correct point-in-time query must still exclude this event."
    )
