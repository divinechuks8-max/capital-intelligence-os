"""Mock HTTP transport for SEC 13F adapter tests, backed by real fixture
files captured from a live, public 13F-HR filing (see atom_feed_sample.xml
for provenance/attribution)."""

from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent


def _read(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "action=getcurrent" in url:
            return httpx.Response(200, content=_read("atom_feed_sample.xml"))
        if url.endswith("/index.json"):
            return httpx.Response(200, content=_read("index.json"))
        if url.endswith("/primary_doc.xml"):
            return httpx.Response(200, content=_read("primary_doc.xml"))
        if url.endswith(".xml"):  # the arbitrarily-named information table
            return httpx.Response(200, content=_read("info_table.xml"))
        return httpx.Response(404, content=b"not found in fixture")

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
