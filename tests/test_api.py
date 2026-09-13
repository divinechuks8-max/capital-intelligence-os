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


def test_institutions_and_holdings_endpoints(session):
    from capint.adapters.sec_13f import SEC13FAdapter
    from capint.ingestion.sec_13f import run_ingestion
    from tests.fixtures.sec_13f import make_test_client as make_13f_test_client

    adapter = SEC13FAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_13f_test_client(),
        min_request_interval=0,
    )
    run_ingestion(session, adapter, filing_count=10)

    client = next(make_client(session))

    institutions = client.get("/api/v1/institutions").json()
    assert len(institutions) == 1
    assert institutions[0]["canonical_name"] == "Talon Private Wealth, LLC"

    holdings = client.get(
        "/api/v1/holdings", params={"institution_entity_id": institutions[0]["entity_id"]}
    ).json()
    assert len(holdings) == 3
    assert {h["position_status"] for h in holdings} == {"NEW"}
    # publication_time and period_of_report must both be present and distinct fields.
    assert holdings[0]["period_of_report"] == "2026-06-30"
    assert "publication_time" in holdings[0]


def test_insider_radar_endpoint_returns_explainable_entry(session):
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
        shares="1000",
        price="10.00",
    )
    session.commit()

    client = next(make_client(session))
    resp = client.get(
        "/api/v1/radar/insider", params={"as_of": "2026-06-01T00:00:00Z", "window_days": 30}
    )
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["company_entity_id"] == str(company.entity_id)
    assert entry["composite_score"] is not None
    assert len(entry["components"]) == 4
    assert len(entry["evidence"]) == 1
    assert len(entry["explanation"]) >= 1


def test_institutional_radar_and_convergence_endpoints(session):
    from datetime import date

    from capint.models.institution import InstitutionalPositionStatus

    from tests.fixtures.synthetic import make_institution, make_institutional_holding_event

    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)
    institution = make_institution(session)

    make_insider_purchase_event(
        session,
        company=company,
        person=person,
        source=source,
        event_time=dt(2026, 5, 20),
        publication_time=dt(2026, 5, 20),
        shares="1000",
        price="10.00",
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

    client = next(make_client(session))

    inst_resp = client.get(
        "/api/v1/radar/institutional", params={"as_of": "2026-06-01T00:00:00Z", "window_days": 365}
    )
    assert inst_resp.status_code == 200
    inst_entries = inst_resp.json()
    assert len(inst_entries) == 1
    assert len(inst_entries[0]["components"]) == 4

    conv_resp = client.get(
        "/api/v1/radar/convergence",
        params={"as_of": "2026-06-01T00:00:00Z", "insider_window_days": 30, "institutional_window_days": 365},
    )
    assert conv_resp.status_code == 200
    conv_entries = conv_resp.json()
    entry = next(e for e in conv_entries if e["company_entity_id"] == str(company.entity_id))
    assert entry["label"] == "INSIDER_AND_INSTITUTIONAL_ACCUMULATING"
    assert entry["insider_score"] is not None
    assert entry["institutional_score"] is not None
