"""Adapter-level tests against a mock transport serving a real (trimmed)
Microsoft Corp submissions.json, including its real 2023-10-13 Item 2.01
8-K (Activision Blizzard acquisition completion). No live network call."""

from datetime import date, datetime, timezone

from capint.adapters.sec_corporate_actions import SECCorporateActionAdapter
from tests.fixtures.sec_corporate_actions import KNOWN_CIK, make_test_client


def make_adapter() -> SECCorporateActionAdapter:
    return SECCorporateActionAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_recent_corporate_actions_finds_only_item_2_01():
    adapter = make_adapter()
    disclosures = adapter.fetch_recent_corporate_actions(KNOWN_CIK, limit=20)

    # The trimmed fixture window has several 8-Ks tagged "8.01,9.01" and
    # "2.02,9.01" (not M&A-relevant) plus exactly one real "2.01" filing.
    assert len(disclosures) == 1
    assert disclosures[0].item_codes == "2.01"
    assert disclosures[0].accession_number == "0001193125-23-255762"


def test_disclosure_fields_parsed_correctly():
    adapter = make_adapter()
    disclosure = adapter.fetch_recent_corporate_actions(KNOWN_CIK, limit=20)[0]

    assert disclosure.company_name == "MICROSOFT CORP"
    assert disclosure.cik == "0000789019"
    assert disclosure.filing_date == date(2023, 10, 13)
    assert disclosure.filed_at == datetime(2023, 10, 13, 12, 37, 32, tzinfo=timezone.utc)
    assert disclosure.primary_document_url == (
        "https://www.sec.gov/Archives/edgar/data/789019/000119312523255762/d537928d8k.htm"
    )


def test_unknown_cik_returns_empty_list():
    adapter = make_adapter()
    assert adapter.fetch_recent_corporate_actions("0000000001", limit=20) == []
