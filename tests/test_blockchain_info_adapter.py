"""Adapter-level tests against a mock transport serving real data captured
live from blockchain.info for the Bitcoin genesis block address. No live
network call."""

from datetime import datetime, timezone
from decimal import Decimal

from capint.adapters.blockchain_info import BlockchainInfoAdapter
from tests.fixtures.blockchain_info import KNOWN_ADDRESS, make_test_client


def make_adapter() -> BlockchainInfoAdapter:
    return BlockchainInfoAdapter(client=make_test_client(), min_request_interval=0)


def test_fetch_wallet_activity_parses_real_data():
    adapter = make_adapter()
    activity = adapter.fetch_wallet_activity(KNOWN_ADDRESS, limit=3)

    assert activity is not None
    assert activity.chain == "bitcoin"
    assert activity.address == KNOWN_ADDRESS
    assert activity.final_balance == Decimal("10747985141") / Decimal(10) ** 8
    assert activity.transaction_count == 65801
    assert len(activity.transactions) == 3


def test_net_amount_computed_correctly_for_each_transaction():
    adapter = make_adapter()
    activity = adapter.fetch_wallet_activity(KNOWN_ADDRESS, limit=3)
    by_hash = {tx.tx_hash: tx for tx in activity.transactions}

    tx1 = by_hash["4fe8b2b6fec09c158e1bd622e236a629510c87d4f77a9778f416a850aab041db"]
    assert tx1.net_amount == Decimal("546") / Decimal(10) ** 8
    assert tx1.block_height is None  # unconfirmed at capture time — a real edge case
    assert tx1.tx_time == datetime.fromtimestamp(1789313244, tz=timezone.utc)

    tx3 = by_hash["06fb51ac445c2be7eb6f6c1837341be7ff01f932e80c5a1cec19b06fecd86d11"]
    assert tx3.net_amount == Decimal("910") / Decimal(10) ** 8
    assert tx3.block_height == 966879  # confirmed


def test_unknown_address_returns_none():
    adapter = make_adapter()
    assert adapter.fetch_wallet_activity("1BitcoinAddressThatDoesNotExistXXXXXX") is None
