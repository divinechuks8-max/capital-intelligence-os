"""Adapter-level tests against a mock transport serving a real (trimmed)
Apple Inc. submissions.json. No live network call."""

from datetime import date, datetime, timezone

from capint.adapters.sec_guidance import SECGuidanceDisclosureAdapter
from tests.fixtures.sec_guidance import KNOWN_CIK, make_test_client


def make_adapter() -> SECGuidanceDisclosureAdapter:
    return SECGuidanceDisclosureAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_recent_guidance_disclosures_finds_only_item_2_02_and_7_01():
    adapter = make_adapter()
    disclosures = adapter.fetch_recent_guidance_disclosures(KNOWN_CIK, limit=20)

    # The fixture's first 40 filings include three 8-Ks: two tagged
    # "2.02,9.01" (guidance-relevant) and one tagged "5.02" (a director/
    # officer change — not guidance-relevant, must be excluded).
    assert len(disclosures) == 2
    assert all(d.item_codes == "2.02,9.01" for d in disclosures)
    assert {d.accession_number for d in disclosures} == {"0000320193-26-000018", "0000320193-26-000011"}


def test_disclosure_fields_parsed_correctly():
    adapter = make_adapter()
    disclosures = adapter.fetch_recent_guidance_disclosures(KNOWN_CIK, limit=20)
    latest = next(d for d in disclosures if d.accession_number == "0000320193-26-000018")

    assert latest.company_name == "Apple Inc."
    assert latest.cik == "0000320193"
    assert latest.filing_date == date(2026, 7, 30)
    assert latest.filed_at == datetime(2026, 7, 30, 20, 30, 28, tzinfo=timezone.utc)
    assert latest.primary_document_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm"
    )


def test_respects_limit():
    adapter = make_adapter()
    disclosures = adapter.fetch_recent_guidance_disclosures(KNOWN_CIK, limit=1)
    assert len(disclosures) == 1


def test_unknown_cik_returns_empty_list():
    adapter = make_adapter()
    assert adapter.fetch_recent_guidance_disclosures("0000000001", limit=20) == []
