import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class CryptoTreasuryMovement(Base):
    """One-to-one extension of an Event with event_type=TREASURY_MOVEMENT:
    one on-chain transaction's net effect on one tracked wallet's balance
    (Phase 12, spec's crypto extension).

    **Deliberately narrow scope**: this only tracks the balance delta for
    an explicitly-provided wallet address the caller already knows about —
    mirroring every other phase's "explicit identifier list" pattern
    (--cik, --ticker, --company-number). It does NOT attempt to detect
    "whale accumulation" or "exchange flow" (EventType also has
    WHALE_ACCUMULATION/EXCHANGE_FLOW/TOKEN_UNLOCK) — those require a
    labeled address database (which addresses belong to which exchange,
    fund, or whale) that has no free, legal source found during research;
    the realistic providers (Nansen, Arkham, Chainalysis) are paid/
    licensed. Guessing a label from heuristics alone would be exactly the
    kind of unverified attribution this project's spec prohibits.

    No wallet-owner attribution is stored or claimed here either — the
    wallet Entity's `canonical_name` is simply the raw address string
    (capint.ingestion.blockchain_info never asserts whose wallet this is).
    "Treasury" in this model's name describes the *event type this system
    already declared* (TREASURY_MOVEMENT), not a verified claim about
    ownership.

    `net_amount` is this transaction's effect on the tracked address's
    balance in whole native-chain units (e.g. BTC, not satoshis) — positive
    for an inflow, negative for an outflow. There is no `balance_after`
    field: computing a genuine running balance requires either the
    address's complete transaction history or chaining from a previously
    ingested transaction, and this adapter only fetches a bounded recent
    window (like every other phase's `--limit`/`--count`) — a fabricated
    or sometimes-null running balance would be worse than none at all.
    """

    __tablename__ = "crypto_treasury_movements"
    __table_args__ = (UniqueConstraint("chain", "tx_hash", "address", name="uq_crypto_treasury_movement"),)

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    wallet_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    chain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(30, 8), nullable=False)
    block_height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
