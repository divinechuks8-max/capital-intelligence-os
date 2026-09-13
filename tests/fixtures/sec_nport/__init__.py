"""Mock HTTP transport for SEC N-PORT adapter tests, backed by real
fixture data captured from SPDR S&P 500 ETF Trust (SPY, CIK 0000884394):
a trimmed real `submissions.json` (six real NPORT-P filings) and two real
`primary_doc.xml` filings (Q1/Q2 2026, trimmed to genInfo/fundInfo only —
the portfolio-holdings section this adapter doesn't parse is dropped to
keep the fixture small). SEC filings are U.S. federal government records,
public domain under 17 U.S.C. §105."""

from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_CIK = "0000884394"

_ACCESSION_TO_FIXTURE = {
    "000141036826089410": "primary_doc_2026q2.xml",
    "000141036826055357": "primary_doc_2026q1.xml",
}


def _read(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"CIK{KNOWN_CIK}.json"):
            return httpx.Response(200, content=_read("submissions.json"))
        for accession_nodash, fixture_name in _ACCESSION_TO_FIXTURE.items():
            if f"/{accession_nodash}/primary_doc.xml" in url:
                return httpx.Response(200, content=_read(fixture_name))
        return httpx.Response(404, content=b'{"error": "no data for this CIK/accession"}')

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
