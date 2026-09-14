"""Mock HTTP transport for the Finnhub adapter, backed by real data
captured live for AAPL's aggregate analyst recommendation trends (4 real
monthly periods). Finnhub's free tier genuinely includes this endpoint —
confirmed live before building the adapter."""

import json
from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_TICKER = "AAPL"


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("symbol") == KNOWN_TICKER:
            data = json.loads((FIXTURE_DIR / "aapl_recommendation.json").read_text(encoding="utf-8"))
            return httpx.Response(200, json=data)
        return httpx.Response(200, json=[])

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
