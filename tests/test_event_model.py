import pytest
from sqlalchemy.exc import IntegrityError

from capint.models.event import Event, EventType
from tests.fixtures.synthetic import (
    dt,
    make_company,
    make_insider_purchase_event,
    make_officer_role,
    make_person,
    make_sec_source,
)


def test_insider_purchase_event_chain(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)
    make_officer_role(session, person, company)

    event = make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 3, 1, 9, 0),
        publication_time=dt(2026, 3, 2, 21, 30),
    )
    session.commit()

    fetched = session.get(Event, event.id)
    assert fetched.event_type == EventType.INSIDER_PURCHASE
    assert fetched.primary_entity_id == company.entity_id
    assert fetched.document is not None
    assert fetched.retrieval_time == fetched.document.retrieved_at


def test_confidence_must_be_in_unit_interval(session):
    source = make_sec_source(session)
    company = make_company(session)

    bad_event = Event(
        event_type=EventType.EARNINGS,
        primary_entity_id=company.entity_id,
        publication_time=dt(2026, 1, 1),
        source_id=source.id,
        confidence=1.5,
    )
    session.add(bad_event)
    with pytest.raises(IntegrityError):
        session.commit()
