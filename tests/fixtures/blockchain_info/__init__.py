"""Mock HTTP transport for blockchain.info adapter tests, backed by real
data captured live for the Bitcoin genesis block address (limited to its
3 most recent transactions) — chosen because it's the single most
well-documented, unambiguously real Bitcoin address in existence, not
because of any claimed corporate/treasury attribution (this adapter makes
none — see capint.models.crypto's module docstring). The fixture happens
to include one confirmed and two unconfirmed (mempool, block_height=null)
transactions — a real edge case this adapter must handle."""

import json
from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_ADDRESS = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


def _read_json(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        address = parts[1] if len(parts) > 1 and parts[0] == "rawaddr" else None
        if address == KNOWN_ADDRESS:
            return httpx.Response(200, json=_read_json("genesis_address.json"))
        return httpx.Response(404, text="Address not found")

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
