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


def test_health_reports_503_when_database_unreachable():
    """Phase 18: /health must reflect real database reachability, not just
    process liveness — a broken DB connection should surface as a non-200
    response a load balancer/orchestrator can act on."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    broken_engine = create_engine("sqlite:///./__no_such_dir_xyz__/unreachable.db", future=True)
    broken_session = sessionmaker(bind=broken_engine)()

    app.dependency_overrides[get_session] = lambda: broken_session
    client = TestClient(app)
    try:
        resp = client.get("/health")
        assert resp.status_code == 503
        assert "database unreachable" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()
        broken_session.close()


def test_dashboard_serves_html(session):
    client = next(make_client(session))
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Capital Intelligence OS" in resp.text
    assert '<script src="/static/dashboard.js">' in resp.text


def test_dashboard_static_assets_are_served(session):
    client = next(make_client(session))
    css = client.get("/static/dashboard.css")
    js = client.get("/static/dashboard.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "function main" in js.text


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


def test_capital_allocation_endpoint(session):
    from capint.adapters.sec_xbrl import SECXBRLFactsAdapter
    from capint.ingestion.sec_xbrl import run_ingestion as run_xbrl_ingestion
    from tests.fixtures.sec_xbrl import make_test_client as make_xbrl_test_client

    adapter = SECXBRLFactsAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_xbrl_test_client(),
        min_request_interval=0,
    )
    run_xbrl_ingestion(session, adapter, ["0000320193"])

    client = next(make_client(session))
    resp = client.get("/api/v1/capital-allocation")
    assert resp.status_code == 200
    facts = resp.json()
    assert len(facts) > 0
    assert {f["event_type"] for f in facts} == {
        "SHARE_BUYBACK",
        "DIVIDEND_PAYMENT",
        "DEBT_ISSUANCE",
        "DEBT_REPAYMENT",
    }

    buyback_resp = client.get("/api/v1/capital-allocation", params={"event_type": "SHARE_BUYBACK"})
    buybacks = buyback_resp.json()
    assert len(buybacks) > 0
    assert all(f["event_type"] == "SHARE_BUYBACK" for f in buybacks)


def test_fundamentals_endpoint(session):
    from capint.adapters.sec_xbrl import SECXBRLFactsAdapter
    from capint.ingestion.sec_xbrl import run_ingestion as run_xbrl_ingestion
    from tests.fixtures.sec_xbrl import make_test_client as make_xbrl_test_client

    adapter = SECXBRLFactsAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_xbrl_test_client(),
        min_request_interval=0,
    )
    run_xbrl_ingestion(session, adapter, ["0000320193"])

    client = next(make_client(session))
    resp = client.get("/api/v1/fundamentals")
    assert resp.status_code == 200
    reports = resp.json()
    assert len(reports) > 0
    assert {r["period_type"] for r in reports} == {"QUARTER", "FISCAL_YEAR"}
    fy_only = client.get("/api/v1/fundamentals", params={"period_type": "FISCAL_YEAR"}).json()
    assert len(fy_only) > 0
    assert all(r["period_type"] == "FISCAL_YEAR" for r in fy_only)
    assert any(r["gross_margin_pct"] is not None for r in fy_only)


def test_funds_and_fund_aum_endpoints(session):
    from capint.adapters.sec_nport import SECNPortAdapter
    from capint.ingestion.sec_nport import run_ingestion as run_nport_ingestion
    from tests.fixtures.sec_nport import make_test_client as make_nport_test_client

    adapter = SECNPortAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_nport_test_client(),
        min_request_interval=0,
    )
    run_nport_ingestion(session, adapter, ["0000884394"], filing_count=2)

    client = next(make_client(session))

    funds = client.get("/api/v1/funds").json()
    assert len(funds) == 1
    assert funds[0]["ticker"] == "SPY"

    snapshots = client.get("/api/v1/fund-aum").json()
    assert len(snapshots) == 2
    by_period = {s["period_end"]: s for s in snapshots}
    assert by_period["2026-03-31"]["net_assets_change_usd"] is None
    assert by_period["2026-06-30"]["net_assets_change_usd"] is not None
    assert float(by_period["2026-06-30"]["net_assets_change_usd"]) > 0


def test_short_interest_endpoint(session):
    from datetime import date as _date

    from capint.adapters.finra_short_interest import FINRAShortInterestAdapter
    from capint.ingestion.finra_short_interest import run_ingestion as run_short_interest_ingestion
    from tests.fixtures.finra_short_interest import make_test_client as make_finra_test_client

    adapter = FINRAShortInterestAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_finra_test_client(),
        min_request_interval=0,
    )
    run_short_interest_ingestion(session, adapter, ["AAPL"], num_cycles=3, as_of=_date(2026, 9, 13))

    client = next(make_client(session))

    snapshots = client.get("/api/v1/short-interest").json()
    assert len(snapshots) == 3
    by_date = {s["settlement_date"]: s for s in snapshots}
    assert by_date["2026-08-31"]["current_short_position"] == "139749097.00"
    assert by_date["2026-08-31"]["change_percent"] == "20.13"

    ticker_filtered = client.get("/api/v1/short-interest", params={"ticker": "AAPL"}).json()
    assert len(ticker_filtered) == 3

    none_filtered = client.get("/api/v1/short-interest", params={"ticker": "MSFT"}).json()
    assert none_filtered == []


def test_uk_psc_endpoint(session):
    from capint.adapters.companies_house import CompaniesHouseAdapter
    from capint.ingestion.companies_house import run_ingestion as run_uk_psc_ingestion
    from tests.fixtures.companies_house import make_test_client as make_ch_test_client

    adapter = CompaniesHouseAdapter(
        api_key="test-key-not-real",
        client=make_ch_test_client(),
        min_request_interval=0,
    )
    run_uk_psc_ingestion(session, adapter, ["05151321"])

    client = next(make_client(session))

    records = client.get("/api/v1/uk-psc").json()
    assert len(records) == 4
    by_name = {r["psc_name"]: r for r in records}
    assert by_name["Gresham House Asset Management Ltd"]["psc_kind"] == "corporate-entity-person-with-significant-control"
    assert by_name["Gresham House Asset Management Ltd"]["ceased_on"] is None
    assert by_name["Gresham House Asset Management Ltd"]["natures_of_control"] == ["voting-rights-25-to-50-percent"]
    assert by_name["Mr Martyn Graham Page"]["ceased_on"] == "2018-11-12"

    company_entity_id = records[0]["company_entity_id"]
    filtered = client.get("/api/v1/uk-psc", params={"company_entity_id": company_entity_id}).json()
    assert len(filtered) == 4


def test_crypto_treasury_endpoint(session):
    from capint.adapters.blockchain_info import BlockchainInfoAdapter
    from capint.ingestion.blockchain_info import run_ingestion as run_crypto_ingestion
    from tests.fixtures.blockchain_info import KNOWN_ADDRESS, make_test_client as make_btc_test_client

    adapter = BlockchainInfoAdapter(client=make_btc_test_client(), min_request_interval=0)
    run_crypto_ingestion(session, adapter, [KNOWN_ADDRESS], limit=3)

    client = next(make_client(session))

    movements = client.get("/api/v1/crypto-treasury").json()
    assert len(movements) == 3
    assert all(m["chain"] == "bitcoin" for m in movements)
    assert all(m["address"] == KNOWN_ADDRESS for m in movements)

    filtered = client.get("/api/v1/crypto-treasury", params={"address": KNOWN_ADDRESS}).json()
    assert len(filtered) == 3

    none_filtered = client.get("/api/v1/crypto-treasury", params={"address": "1SomeOtherAddress"}).json()
    assert none_filtered == []


def test_guidance_disclosures_endpoint(session):
    from capint.adapters.sec_guidance import SECGuidanceDisclosureAdapter
    from capint.ingestion.sec_guidance import run_ingestion as run_guidance_ingestion
    from tests.fixtures.sec_guidance import KNOWN_CIK, make_test_client as make_guidance_test_client

    adapter = SECGuidanceDisclosureAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_guidance_test_client(),
        min_request_interval=0,
    )
    run_guidance_ingestion(session, adapter, [KNOWN_CIK], filing_count=20)

    client = next(make_client(session))

    disclosures = client.get("/api/v1/guidance-disclosures").json()
    assert len(disclosures) == 2
    assert all(d["item_codes"] == "2.02,9.01" for d in disclosures)

    company_entity_id = disclosures[0]["company_entity_id"]
    filtered = client.get("/api/v1/guidance-disclosures", params={"company_entity_id": company_entity_id}).json()
    assert len(filtered) == 2


def test_alert_rules_and_alerts_endpoints(session):
    from capint.alerting.engine import evaluate_all_active_rules
    from capint.alerting.rules import create_or_update_alert_rule
    from capint.models.alert import AlertRuleType

    source = make_sec_source(session)
    company = make_company(session)
    person = make_person(session)

    make_insider_purchase_event(
        session, company=company, person=person, source=source,
        event_time=dt(2026, 5, 15), publication_time=dt(2026, 5, 16), shares="100000", price="50.00",
    )
    session.commit()

    rule = create_or_update_alert_rule(
        session, name="High insider conviction", rule_type=AlertRuleType.INSIDER_CONVICTION_THRESHOLD, min_composite_score=0.0
    )
    evaluate_all_active_rules(session, as_of=dt(2026, 6, 1))

    client = next(make_client(session))

    rules_resp = client.get("/api/v1/alert-rules")
    assert rules_resp.status_code == 200
    rules = rules_resp.json()
    assert len(rules) == 1
    assert rules[0]["name"] == "High insider conviction"
    assert rules[0]["webhook_configured"] is False
    assert "webhook_url" not in rules[0]  # never served back — see AlertRuleOut docstring
    assert rules[0]["webhook_format"] == "GENERIC"

    alerts_resp = client.get("/api/v1/alerts")
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) == 1
    assert alerts[0]["rule_id"] == str(rule.id)
    assert alerts[0]["company_entity_id"] == str(company.entity_id)
    assert alerts[0]["delivery_attempted"] is False
    assert alerts[0]["delivery_succeeded"] is False
    assert alerts[0]["delivery_error"] is None

    filtered = client.get("/api/v1/alerts", params={"company_entity_id": str(company.entity_id)}).json()
    assert len(filtered) == 1

    none_filtered = client.get("/api/v1/alerts", params={"rule_id": str(company.entity_id)})
    assert none_filtered.status_code == 200
    assert none_filtered.json() == []


def test_backtest_short_interest_endpoint(session):
    from tests.fixtures.synthetic import make_price_bar, make_price_source, make_short_interest_snapshot_event

    sec_source = make_sec_source(session)
    price_source = make_price_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session, company=company, source=sec_source, ticker="TEST",
        settlement_date=dt(2026, 5, 1).date(), publication_time=dt(2026, 5, 2),
        current_short_position="1100000", previous_short_position="1000000",
        change_percent="10.00", days_to_cover="2.00",
    )
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=dt(2026, 5, 1).date(), close="100.00")
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=dt(2026, 5, 4).date(), close="120.00")
    session.commit()

    client = next(make_client(session))
    resp = client.get(
        "/api/v1/backtest/short-interest",
        params={"as_of": "2026-06-01T00:00:00Z", "holding_trading_days": 1},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["signal_count"] == 1
    assert data["computable_count"] == 1
    assert data["mean_forward_return_pct"] == 20
    assert data["results"][0]["forward_return_pct"] == 20


def test_corporate_actions_endpoint(session):
    from capint.adapters.sec_corporate_actions import SECCorporateActionAdapter
    from capint.ingestion.sec_corporate_actions import run_ingestion as run_corp_action_ingestion
    from tests.fixtures.sec_corporate_actions import KNOWN_CIK, make_test_client as make_corp_action_test_client

    adapter = SECCorporateActionAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_corp_action_test_client(),
        min_request_interval=0,
    )
    run_corp_action_ingestion(session, adapter, [KNOWN_CIK], filing_count=20)

    client = next(make_client(session))

    disclosures = client.get("/api/v1/corporate-actions").json()
    assert len(disclosures) == 1
    assert disclosures[0]["item_codes"] == "2.01"
    assert disclosures[0]["filing_accession"] == "0001193125-23-255762"

    company_entity_id = disclosures[0]["company_entity_id"]
    filtered = client.get("/api/v1/corporate-actions", params={"company_entity_id": company_entity_id}).json()
    assert len(filtered) == 1


def test_volatility_index_endpoint(session):
    from capint.adapters.cboe_volatility import CBOEVolatilityIndexAdapter
    from capint.ingestion.cboe_volatility import run_ingestion as run_volatility_ingestion
    from tests.fixtures.cboe_volatility import KNOWN_INDEX, make_test_client as make_cboe_test_client

    adapter = CBOEVolatilityIndexAdapter(client=make_cboe_test_client(), min_request_interval=0)
    run_volatility_ingestion(session, adapter, [KNOWN_INDEX])

    client = next(make_client(session))

    levels = client.get("/api/v1/volatility-index").json()
    assert len(levels) == 10
    assert levels[0]["trade_date"] == "2026-09-11"  # newest first
    assert levels[0]["close"] == "15.8400"  # Numeric(14,4) column, source CSV has 6 decimals

    none_filtered = client.get("/api/v1/volatility-index", params={"index_code": "ZZZZNOTAREAL"}).json()
    assert none_filtered == []

    # as_of (Phase 17) gates on ingestion time (created_at), not trade_date.
    future = client.get("/api/v1/volatility-index", params={"as_of": "2099-01-01T00:00:00Z"}).json()
    assert len(future) == 10
    past = client.get("/api/v1/volatility-index", params={"as_of": "2000-01-01T00:00:00Z"}).json()
    assert past == []


def test_analyst_recommendations_endpoint(session):
    """No ingestion path currently populates AnalystRecommendationTrend —
    see that model's docstring — so this test builds rows directly,
    matching capint.backtesting's synthetic-fixture approach for
    PriceBar, another currently-unpopulated table. The endpoint itself
    (serving whatever rows exist) is still real, working code worth
    covering."""
    from tests.fixtures.synthetic import make_analyst_recommendation_trend, make_price_source

    source = make_price_source(session)
    company = make_company(session)

    make_analyst_recommendation_trend(
        session, company=company, source=source, ticker="AAPL",
        period=dt(2026, 9, 1).date(), strong_buy=12, buy=22, hold=15, sell=3, strong_sell=1,
    )
    session.commit()

    client = next(make_client(session))

    trends = client.get("/api/v1/analyst-recommendations").json()
    assert len(trends) == 1
    assert trends[0]["period"] == "2026-09-01"
    assert trends[0]["strong_buy"] == 12

    filtered = client.get("/api/v1/analyst-recommendations", params={"ticker": "AAPL"}).json()
    assert len(filtered) == 1

    none_filtered = client.get("/api/v1/analyst-recommendations", params={"ticker": "MSFT"}).json()
    assert none_filtered == []


def test_interlocking_directorates_endpoint(session):
    from capint.ingestion.sec_form4 import upsert_person_company_role

    person = make_person(session, name="J. Interlocked (SYNTHETIC)")
    company_a = make_company(session, name="Company A (SYNTHETIC)")
    company_b = make_company(session, name="Company B (SYNTHETIC)")

    upsert_person_company_role(
        session, person, company_a, is_officer=True, is_director=False, is_ten_percent_owner=False, role_title="CEO",
        first_evidence_time=dt(2026, 5, 1),
    )
    upsert_person_company_role(
        session, person, company_b, is_officer=False, is_director=True, is_ten_percent_owner=False, role_title="Director",
        first_evidence_time=dt(2026, 5, 1),
    )
    session.commit()

    client = next(make_client(session))

    results = client.get("/api/v1/relationships/interlocking-directorates").json()
    assert len(results) == 1
    assert results[0]["person_name"] == "J. Interlocked (SYNTHETIC)"
    assert results[0]["role_a_start_date"] == "2026-05-01"
    assert results[0]["role_b_start_date"] == "2026-05-01"

    filtered = client.get(
        "/api/v1/relationships/interlocking-directorates", params={"company_entity_id": str(company_a.entity_id)}
    ).json()
    assert len(filtered) == 1

    none_filtered = client.get(
        "/api/v1/relationships/interlocking-directorates", params={"company_entity_id": str(make_company(session, name="Unrelated Co (SYNTHETIC)").entity_id)}
    ).json()
    assert none_filtered == []

    # as_of (Phase 17) gates on each role's start_date (earliest Form 4 disclosure).
    future = client.get(
        "/api/v1/relationships/interlocking-directorates", params={"as_of": "2099-01-01T00:00:00Z"}
    ).json()
    assert len(future) == 1
    past = client.get(
        "/api/v1/relationships/interlocking-directorates", params={"as_of": "2000-01-01T00:00:00Z"}
    ).json()
    assert past == []


def test_news_sentiment_endpoint(session):
    from capint.adapters.gdelt import GDELTAdapter
    from capint.ingestion.gdelt import ingest_news_sentiment
    from tests.fixtures.gdelt import KNOWN_QUERY, make_test_client as make_gdelt_test_client

    adapter = GDELTAdapter(client=make_gdelt_test_client(), min_request_interval=0)
    ingest_news_sentiment(session, adapter, "AAPL", KNOWN_QUERY)

    client = next(make_client(session))

    snapshots = client.get("/api/v1/news-sentiment").json()
    assert len(snapshots) == 1
    assert snapshots[0]["query"] == KNOWN_QUERY
    assert snapshots[0]["article_count"] == 3172
    assert snapshots[0]["mean_tone"] == "0.195"
    assert len(snapshots[0]["tone_distribution"]) == 22

    company_entity_id = snapshots[0]["company_entity_id"]
    filtered = client.get("/api/v1/news-sentiment", params={"company_entity_id": company_entity_id}).json()
    assert len(filtered) == 1

    # as_of (Phase 17) gates on ingestion time (retrieved_at), not article publication time.
    future = client.get("/api/v1/news-sentiment", params={"as_of": "2099-01-01T00:00:00Z"}).json()
    assert len(future) == 1
    past = client.get("/api/v1/news-sentiment", params={"as_of": "2000-01-01T00:00:00Z"}).json()
    assert past == []


def test_ownership_disclosures_endpoint(session):
    from datetime import date as _date

    from capint.adapters.sec_13dg import SEC13DGAdapter
    from capint.ingestion.sec_13dg import run_ingestion as run_13dg_ingestion
    from tests.fixtures.sec_13dg import make_test_client as make_13dg_test_client

    adapter = SEC13DGAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_13dg_test_client(),
        min_request_interval=0,
    )
    run_13dg_ingestion(session, adapter, _date(2026, 9, 1), _date(2026, 9, 13))

    client = next(make_client(session))
    resp = client.get("/api/v1/ownership-disclosures")
    assert resp.status_code == 200
    disclosures = resp.json()
    assert len(disclosures) == 3

    schedule_types = {d["schedule_type"] for d in disclosures}
    assert schedule_types == {"SCHEDULE_13D", "SCHEDULE_13G"}
    thirteen_d_entries = [d for d in disclosures if d["schedule_type"] == "SCHEDULE_13D"]
    assert all(d["stated_purpose"] for d in thirteen_d_entries)
    thirteen_g_entries = [d for d in disclosures if d["schedule_type"] == "SCHEDULE_13G"]
    assert all(d["stated_purpose"] is None for d in thirteen_g_entries)


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
    assert entry["short_interest_score"] is None  # no short-interest data ingested for this test


def test_short_interest_radar_and_convergence_third_family(session):
    from tests.fixtures.synthetic import make_short_interest_snapshot_event

    source = make_sec_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session,
        company=company,
        source=source,
        ticker="TEST",
        settlement_date=dt(2026, 5, 15).date(),
        publication_time=dt(2026, 5, 16),
        current_short_position="1100000",
        previous_short_position="1000000",
        change_percent="10.00",
        days_to_cover="2.00",
    )
    session.commit()

    client = next(make_client(session))

    si_resp = client.get("/api/v1/radar/short-interest", params={"as_of": "2026-06-01T00:00:00Z"})
    assert si_resp.status_code == 200
    si_entries = si_resp.json()
    assert len(si_entries) == 1
    assert si_entries[0]["company_entity_id"] == str(company.entity_id)
    assert si_entries[0]["latest_change_percent"] == "10.00"

    conv_resp = client.get("/api/v1/radar/convergence", params={"as_of": "2026-06-01T00:00:00Z"})
    assert conv_resp.status_code == 200
    entry = next(e for e in conv_resp.json() if e["company_entity_id"] == str(company.entity_id))
    assert entry["label"] == "SHORT_INTEREST_ONLY"
    assert entry["insider_score"] is None
    assert entry["institutional_score"] is None
    assert entry["short_interest_score"] is not None
