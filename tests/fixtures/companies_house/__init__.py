"""Mock HTTP transport for Companies House adapter tests, backed by real
data captured live for two real UK companies: Diageo plc (00023307, LSE
Main Market — PSC-exempt, confirmed live to return zero PSC records) and
Angling Direct plc (05151321, AIM-listed — confirmed live to have real
PSC records, including a corporate PSC with a UK registration number, a
ceased corporate PSC, and two ceased individual PSCs). Companies House
data is Crown Copyright, licensed under the Open Government Licence v3.0
for reuse."""

import json
from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent

_PROFILE_FIXTURES = {
    "00023307": "diageo_profile.json",
    "05151321": "angling_direct_profile.json",
}
_PSC_FIXTURES = {
    "00023307": "diageo_psc.json",
    "05151321": "angling_direct_psc.json",
}


def _read_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        parts = path.strip("/").split("/")
        # parts == ["company", "<number>"] or ["company", "<number>", "persons-with-significant-control"]
        company_number = parts[1] if len(parts) > 1 else None

        if len(parts) == 2:
            fixture = _PROFILE_FIXTURES.get(company_number)
            if fixture is None:
                return httpx.Response(404, json={"errors": [{"error": "company-profile-not-found"}]})
            return httpx.Response(200, json=_read_json(fixture))

        if len(parts) == 3 and parts[2] == "persons-with-significant-control":
            fixture = _PSC_FIXTURES.get(company_number)
            if fixture is None:
                return httpx.Response(404, json={"errors": [{"error": "psc-not-found"}]})
            return httpx.Response(200, json=_read_json(fixture))

        return httpx.Response(404, json={"errors": [{"error": "not-found"}]})

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
