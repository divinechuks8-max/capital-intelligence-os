"""Mock HTTP transport for SEC 13D/13G adapter tests, backed by real
fixture files captured from two live, public filings: a Schedule 13D
(GoPro / Nicholas Woodman, accession 0001104659-26-106446) and a Schedule
13G (Metagenomi Therapeutics / Brian Thomas, accession
0001193125-26-387853). SEC filings are U.S. federal government records,
public domain under 17 U.S.C. §105."""

from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent


def _read(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)

        if "efts.sec.gov" in url:
            if "forms=SCHEDULE+13G" in url:
                return httpx.Response(200, content=_read("search_13g.json"))
            return httpx.Response(200, content=_read("search_13d.json"))

        is_13d = "1500435" in url
        is_13g = "1785279" in url

        if url.endswith("/index.json"):
            return httpx.Response(200, content=_read("13d_index.json" if is_13d else "13g_index.json"))
        if url.endswith("/primary_doc.xml"):
            return httpx.Response(200, content=_read("13d_primary_doc.xml" if is_13d else "13g_primary_doc.xml"))
        if url.endswith(".txt"):
            return httpx.Response(
                200, content=_read("13d_submission_prefix.txt" if is_13d else "13g_submission_prefix.txt")
            )
        return httpx.Response(404, content=b"not found in fixture")

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
