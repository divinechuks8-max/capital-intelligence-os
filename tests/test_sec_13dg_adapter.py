"""Adapter-level tests against a mock transport serving two real, captured
filings (see tests/fixtures/sec_13dg/__init__.py). No live network call."""

from datetime import date, datetime, timezone
from decimal import Decimal

from capint.adapters.sec_13dg import SEC13DGAdapter, SCHEDULE_13D, SCHEDULE_13G
from tests.fixtures.sec_13dg import make_test_client


def make_adapter() -> SEC13DGAdapter:
    return SEC13DGAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_recent_filings_excludes_amendments():
    adapter = make_adapter()
    leads = adapter.fetch_recent_filings(SCHEDULE_13D, date(2026, 9, 1), date(2026, 9, 13), limit=10)

    assert len(leads) == 1  # the SCHEDULE 13D/A entry must be excluded
    assert leads[0].accession_number == "0001104659-26-106446"
    assert leads[0].form_type == SCHEDULE_13D


def test_parses_13d_filing_correctly():
    adapter = make_adapter()
    lead = adapter.fetch_recent_filings(SCHEDULE_13D, date(2026, 9, 1), date(2026, 9, 13), limit=10)[0]

    filing = adapter.fetch_filing(lead)

    assert filing is not None
    assert filing.issuer_cik == "0001500435"
    assert filing.issuer_name == "GoPro, Inc."
    assert filing.issuer_cusip == "38268T103"
    assert filing.event_date == date(2026, 9, 1)
    assert filing.published_at == datetime(2026, 9, 10, 0, 22, 30, tzinfo=timezone.utc)
    assert filing.stated_purpose is not None
    assert filing.stated_purpose.startswith("The information set forth in Items 3 and 6")

    assert len(filing.reporting_persons) == 2
    woodman = next(p for p in filing.reporting_persons if p.name == "Woodman Nicholas")
    assert woodman.cik == "0001610500"
    assert woodman.type_code == "IN"
    assert woodman.shares_owned == Decimal("46238278.00")
    assert woodman.percent_of_class == Decimal("20.3")
    assert woodman.sole_voting_power == Decimal("792059.00")
    assert woodman.shared_voting_power == Decimal("45446219.00")

    trust = next(p for p in filing.reporting_persons if p.cik is None)
    assert trust.name.startswith("Woodman Family Trust")
    assert trust.type_code == "OO"


def test_parses_13g_filing_correctly():
    adapter = make_adapter()
    lead = adapter.fetch_recent_filings(SCHEDULE_13G, date(2026, 9, 1), date(2026, 9, 13), limit=10)[0]

    filing = adapter.fetch_filing(lead)

    assert filing is not None
    assert filing.issuer_cik == "0001785279"
    assert filing.issuer_name == "Metagenomi Therapeutics, Inc."
    assert filing.event_date == date(2026, 6, 30)
    assert filing.stated_purpose is None  # 13G has no "purpose" item at all

    assert len(filing.reporting_persons) == 1
    person = filing.reporting_persons[0]
    assert person.cik == "0001996097"
    assert person.name == "Thomas Brian C."
    assert person.type_code == "IN"
    assert person.shares_owned == Decimal("2762142")
    assert person.percent_of_class == Decimal("7.3")


def test_missing_user_agent_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        SEC13DGAdapter(user_agent="")
