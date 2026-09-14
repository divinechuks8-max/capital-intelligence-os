"""Mock HTTP transport for the Alpha Vantage adapter, backed by real data
captured live for AAPL (100 real trading days, 2026-04-21 to
2026-09-11 — the free tier's `outputsize=compact` window)."""

import json
from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_TICKER = "AAPL"


def _read_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("outputsize") == "full":
            return httpx.Response(200, json={"Information": "outputsize=full is a premium feature"})
        if params.get("symbol") == KNOWN_TICKER:
            return httpx.Response(200, json=_read_json("aapl_daily.json"))
        return httpx.Response(200, json={"Error Message": "Invalid API call."})

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
