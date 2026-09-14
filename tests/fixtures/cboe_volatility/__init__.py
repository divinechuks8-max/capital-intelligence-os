"""Mock HTTP transport for the Cboe volatility index adapter, backed by
real data captured live for VIX (the header plus its 10 most recent real
trading days as of capture). Cboe publishes this data freely for public
reuse (cboe.com/tradable-products/vix/vix-historical-data), distinct
from the paid Cboe DataShop product for granular options-level data."""

from pathlib import Path

import httpx

FIXTURE_DIR = Path(__file__).parent
KNOWN_INDEX = "VIX"


def build_mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"{KNOWN_INDEX}_History.csv"):
            return httpx.Response(200, content=(FIXTURE_DIR / "vix_history.csv").read_bytes())
        return httpx.Response(404, text="Not Found")

    return httpx.MockTransport(handler)


def make_test_client() -> httpx.Client:
    return httpx.Client(transport=build_mock_transport())
