"""Adapter-level tests against a mock transport serving real (trimmed)
SPY N-PORT data. No live network call."""

from datetime import date, datetime, timezone
from decimal import Decimal

from capint.adapters.sec_nport import SECNPortAdapter
from tests.fixtures.sec_nport import make_test_client


def make_adapter() -> SECNPortAdapter:
    return SECNPortAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_recent_filings_returns_real_metadata():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings("0000884394", limit=6)

    assert len(leads) == 6
    assert all(lead.fund_name == "SPDR S&P 500 ETF TRUST" for lead in leads)
    assert all(lead.ticker == "SPY" for lead in leads)
    assert leads[0].accession_number == "0001410368-26-089410"
    assert leads[0].filed_at == datetime(2026, 8, 28, 12, 25, 47, tzinfo=timezone.utc)


def test_fetch_recent_filings_respects_limit():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings("0000884394", limit=2)
    assert len(leads) == 2


def test_unknown_cik_returns_no_filings():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings("0000000001", limit=5)
    assert leads == []


def test_fetch_fund_snapshot_parses_real_aum_figures():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings("0000884394", limit=1)
    fact = adapter.fetch_fund_snapshot(leads[0])

    assert fact is not None
    assert fact.fund_name == "SPDR S&P 500 ETF TRUST"
    assert fact.ticker == "SPY"
    assert fact.period_end == date(2026, 6, 30)
    assert fact.total_assets_usd == Decimal("783339902049.69")
    assert fact.total_liabilities_usd == Decimal("2151029942.93")
    assert fact.net_assets_usd == Decimal("781188872106.76")
    assert fact.accession == "0001410368-26-089410"


def test_fetch_fund_snapshot_second_quarter():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings("0000884394", limit=2)
    q1_lead = next(lead for lead in leads if lead.accession_number == "0001410368-26-055357")
    fact = adapter.fetch_fund_snapshot(q1_lead)

    assert fact is not None
    assert fact.period_end == date(2026, 3, 31)
    assert fact.net_assets_usd == Decimal("651588269947.59")


def test_fetch_fund_snapshot_returns_none_for_missing_filing():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings("0000884394", limit=6)
    # Only the top 2 accessions have a primary_doc.xml fixture — the rest 404.
    missing_lead = next(lead for lead in leads if lead.accession_number not in {
        "0001410368-26-089410", "0001410368-26-055357",
    })
    assert adapter.fetch_fund_snapshot(missing_lead) is None


def test_missing_user_agent_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        SECNPortAdapter(user_agent="")
