"""Mock HTTP transport for SEC XBRL adapter tests, backed by real company
facts data (Apple Inc., CIK 0000320193), trimmed to the four
capital-allocation concepts this adapter cares about and to the last ~14
facts per concept — still real values, not synthetic. SEC's XBRL frames
API output is derived from filers' own structured filings, themselves
U.S. federal government records, public domain under 17 U.S.C. §105."""

from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_CIK = "0000320193"


def _read(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"CIK{KNOWN_CIK}.json"):
            return httpx.Response(200, content=_read("aapl_company_facts.json"))
        return httpx.Response(404, content=b'{"error": "no facts for this CIK"}')

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
