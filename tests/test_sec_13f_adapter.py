"""Adapter-level tests against a mock transport serving a real, captured
13F-HR filing (see tests/fixtures/sec_13f/atom_feed_sample.xml). No live
network call."""

from datetime import date
from decimal import Decimal

from capint.adapters.sec_13f import SEC13FAdapter
from tests.fixtures.sec_13f import make_test_client


def make_adapter() -> SEC13FAdapter:
    return SEC13FAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_current_filings_excludes_amendments():
    adapter = make_adapter()
    filings = adapter.fetch_current_13f_filings(count=10)

    assert len(filings) == 1  # the 13F-HR/A entry must be excluded
    assert filings[0].accession_number == "0001990467-26-000004"
    assert filings[0].form_type == "13F-HR"


def test_parses_cover_page_and_aggregates_holdings_by_cusip():
    adapter = make_adapter()
    filing = adapter.fetch_current_13f_filings(count=10)[0]

    data = adapter.fetch_holdings_for_filing(filing)

    assert data is not None
    assert data.filer_cik == "0001990467"
    assert data.filer_name == "Talon Private Wealth, LLC"
    assert data.period_of_report == date(2026, 6, 30)

    by_cusip = {h.cusip: h for h in data.holdings}
    assert set(by_cusip) == {"007903107", "00123Q104", "02079K305"}

    amd = by_cusip["007903107"]
    assert amd.issuer_name == "ADVANCED MICRO DEVICES INC"
    assert amd.shares == Decimal("736") + Decimal("1592")
    assert amd.market_value_usd == Decimal("427550") + Decimal("924809")

    agnc = by_cusip["00123Q104"]
    assert agnc.shares == Decimal("11511")
    assert agnc.market_value_usd == Decimal("125471")

    googl = by_cusip["02079K305"]
    assert googl.shares == Decimal("998") + Decimal("9428")
