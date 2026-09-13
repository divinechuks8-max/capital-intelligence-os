from fastapi.testclient import TestClient

from capint.api.main import app
from capint.db import get_session
from tests.fixtures.synthetic import dt, make_company, make_insider_purchase_event, make_person, make_sec_source


def make_client(session):
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_health(session):
    client = next(make_client(session))
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_companies_and_point_in_time_events(session):
    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)
    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 3, 1),
        publication_time=dt(2026, 3, 5),
    )
    session.commit()

    client = next(make_client(session))

    companies = client.get("/api/v1/companies").json()
    assert len(companies) == 1
    assert companies[0]["canonical_name"].startswith("Acme Robotics")

    before = client.get(
        "/api/v1/events", params={"entity_id": str(company.entity_id), "as_of": "2026-03-04T00:00:00Z"}
    ).json()
    assert before == []

    after = client.get(
        "/api/v1/events", params={"entity_id": str(company.entity_id), "as_of": "2026-03-06T00:00:00Z"}
    ).json()
    assert len(after) == 1
    assert after[0]["event_type"] == "INSIDER_PURCHASE"
