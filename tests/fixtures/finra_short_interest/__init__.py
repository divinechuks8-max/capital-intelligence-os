"""Mock HTTP transport for FINRA short interest adapter tests, backed by
real data captured live from FINRA's consolidatedShortInterest API for
three real settlement cycles (2026-07-31, 2026-08-14, 2026-08-31) and one
confirmed no-data settlement date (2026-08-29, a mid-month candidate that
turned out not to be a real cycle — used to test the date-probing logic
against a genuine negative). FINRA short interest data is a public
regulatory disclosure feed."""

import json
from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent

KNOWN_SETTLEMENT_DATES = {"2026-07-31", "2026-08-14", "2026-08-31"}
NO_DATA_SETTLEMENT_DATES = {"2026-08-29"}

_AAPL_FIXTURES = {
    "2026-08-31": "aapl_20260831.json",
    "2026-08-14": "aapl_20260814.json",
    "2026-07-31": "aapl_20260731.json",
}


def _read_json(name: str) -> list:
    return json.loads((FIXTURE_DIR / name).read_text())


def _find_filter(body: dict, field_name: str) -> str | None:
    for f in body.get("compareFilters", []):
        if f.get("fieldName") == field_name:
            return f.get("fieldValue")
    return None


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        settlement_date = _find_filter(body, "settlementDate")
        symbol = _find_filter(body, "symbolCode")

        if settlement_date in NO_DATA_SETTLEMENT_DATES:
            return httpx.Response(204)
        if settlement_date not in KNOWN_SETTLEMENT_DATES:
            return httpx.Response(204)

        if symbol is None:
            # A candidate-date probe: any real data on this date confirms the cycle.
            return httpx.Response(200, json=_read_json("probe_any_20260831.json"))

        if symbol.upper() == "AAPL":
            return httpx.Response(200, json=_read_json(_AAPL_FIXTURES[settlement_date]))

        # A ticker FINRA has no record for on this (real) settlement date.
        return httpx.Response(204)

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
