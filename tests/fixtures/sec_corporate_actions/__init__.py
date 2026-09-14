"""Mock HTTP transport for the SEC corporate-action adapter, backed by a
real (trimmed to 50 of 1000 recent filings) submissions.json captured
from Microsoft Corp (CIK 0000789019) — including its real 2023-10-13 8-K
tagged with Item 2.01 alone, its Activision Blizzard acquisition
completion. SEC filings are U.S. federal government records, public
domain under 17 U.S.C. §105."""

from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_CIK = "0000789019"


def _read(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith(f"CIK{KNOWN_CIK}.json"):
            return httpx.Response(200, content=_read("msft_submissions.json"))
        return httpx.Response(404, content=b'{"error": "no data for this CIK"}')

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
