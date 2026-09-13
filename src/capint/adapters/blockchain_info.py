"""blockchain.info adapter (Phase 12, crypto extension): Bitcoin wallet
on-chain activity for explicitly-tracked addresses.

blockchain.info's `rawaddr` endpoint is free, requires no API key or
registration, and returns real transaction-level Bitcoin data — confirmed
live before building this. This is the first on-chain data source in this
system.

**Deliberately Bitcoin-only and address-scoped.** See
capint.models.crypto.CryptoTreasuryMovement's docstring for why this does
not attempt "whale accumulation" or "exchange flow" detection: those need
a labeled address database this project found no free, legal source for.
Ethereum/ERC-20 support (via Etherscan) would be a natural next increment
but needs its own free API key (confirmed live: Etherscan's v2 API
requires one, unlike blockchain.info) — not registered in this phase.

**Real edge case confirmed live**: `rawaddr` can include an unconfirmed
(mempool) transaction with `block_height: null` — still a real,
broadcast, publicly-visible transaction, just not yet in a block. Treated
here as a valid observation (its own `time` field is still present and
used as the event time), with `block_height` simply left `None`. Such a
transaction could theoretically be dropped if never confirmed — a real,
minor limitation of on-chain data recency, not specific to this adapter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

API_BASE = "https://blockchain.info"
SATOSHIS_PER_BTC = Decimal(10) ** 8


@dataclass(frozen=True)
class RawWalletTransaction:
    chain: str
    address: str
    tx_hash: str
    tx_time: datetime
    net_amount: Decimal  # whole BTC, positive=inflow, negative=outflow
    block_height: int | None


@dataclass(frozen=True)
class RawWalletActivity:
    chain: str
    address: str
    final_balance: Decimal  # whole BTC, as reported right now by the source
    transaction_count: int  # total transactions this address has ever had (n_tx)
    transactions: list[RawWalletTransaction]  # only the fetched window, not full history


def _net_satoshis_for_address(tx: dict[str, Any], address: str) -> int:
    received = sum(out.get("value", 0) for out in tx.get("out", []) if out.get("addr") == address)
    sent = sum(
        inp.get("prev_out", {}).get("value", 0)
        for inp in tx.get("inputs", [])
        if inp.get("prev_out", {}).get("addr") == address
    )
    return received - sent


class _RateLimitedBlockchainInfoClient:
    def __init__(self, client: httpx.Client | None = None, min_request_interval: float = 0.5) -> None:
        self._client = client or httpx.Client(timeout=20.0)
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.get(url, params=params)
        self._last_request_at = time.monotonic()
        return resp


class BlockchainInfoAdapter(SourceAdapter):
    source_name = "blockchain.info"
    chain = "bitcoin"

    def __init__(self, client: httpx.Client | None = None, min_request_interval: float = 0.5) -> None:
        self._http = _RateLimitedBlockchainInfoClient(client=client, min_request_interval=min_request_interval)

    def fetch_wallet_activity(self, address: str, limit: int = 50) -> RawWalletActivity | None:
        """Returns None for an address blockchain.info has never seen
        (zero transactions) — not necessarily invalid, just unused."""
        resp = self._http.get(f"{API_BASE}/rawaddr/{address}", params={"limit": limit})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()

        transactions = []
        for tx in data.get("txs", []):
            net_satoshis = _net_satoshis_for_address(tx, address)
            transactions.append(
                RawWalletTransaction(
                    chain=self.chain,
                    address=address,
                    tx_hash=tx["hash"],
                    tx_time=datetime.fromtimestamp(tx["time"], tz=timezone.utc),
                    net_amount=Decimal(net_satoshis) / SATOSHIS_PER_BTC,
                    block_height=tx.get("block_height"),
                )
            )

        return RawWalletActivity(
            chain=self.chain,
            address=address,
            final_balance=Decimal(data.get("final_balance", 0)) / SATOSHIS_PER_BTC,
            transaction_count=data.get("n_tx", len(transactions)),
            transactions=transactions,
        )

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.finra_short_interest.FINRAShortInterestAdapter's
        identical note. This adapter is per-wallet, not time-windowed."""
        raise NotImplementedError(
            "BlockchainInfoAdapter is per-wallet; use fetch_wallet_activity "
            "via capint.ingestion.blockchain_info instead."
        )
