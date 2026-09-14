"""Mock HTTP transport for the GDELT adapter, backed by a real tone
distribution captured live for the query "Apple Inc" (trimmed to just
bin/count pairs — this adapter never reads GDELT's "toparts" per-bin
article lists). GDELT's Terms of Use explicitly permit commercial use
and redistribution, confirmed by reading them before this was built."""

import json
from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_QUERY = "Apple Inc"


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("query") == KNOWN_QUERY and params.get("mode") == "tonechart":
            data = json.loads((FIXTURE_DIR / "apple_inc_tonechart.json").read_text(encoding="utf-8"))
            return httpx.Response(200, json=data)
        return httpx.Response(200, json={"tonechart": []})

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
