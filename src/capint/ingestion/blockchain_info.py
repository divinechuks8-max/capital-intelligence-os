"""Entity resolution + persistence for on-chain wallet activity (Phase 12,
crypto extension). See capint.models.crypto.CryptoTreasuryMovement's
docstring for why no wallet-owner attribution is stored: the wallet
Entity's canonical_name is simply the raw address string."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.adapters.blockchain_info import BlockchainInfoAdapter, RawWalletTransaction
from capint.models.crypto import CryptoTreasuryMovement
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.source import Document, Source, SourceTier

SOURCE_NAME = "blockchain.info"


def _raw_reference(chain: str, address: str, tx_hash: str) -> str:
    return f"onchain:{chain}:{address}:{tx_hash}"


def get_or_create_source(session: Session) -> Source:
    source = session.execute(select(Source).where(Source.name == SOURCE_NAME)).scalar_one_or_none()
    if source is not None:
        return source
    source = Source(name=SOURCE_NAME, tier=SourceTier.A_OFFICIAL, base_url="https://blockchain.info")
    session.add(source)
    session.flush()
    return source


def get_or_create_wallet_entity(session: Session, chain: str, address: str) -> Entity:
    identifier_value = f"{chain}:{address}"
    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.WALLET_ADDRESS,
            EntityIdentifier.identifier_value == identifier_value,
        )
    ).scalars().first()
    if ident is not None:
        entity = session.get(Entity, ident.entity_id)
        if entity is not None:
            return entity

    entity = Entity(entity_type=EntityType.CRYPTO_WALLET, canonical_name=address)
    session.add(entity)
    session.flush()
    session.add(
        EntityIdentifier(
            entity_id=entity.id, identifier_type=IdentifierType.WALLET_ADDRESS, identifier_value=identifier_value, is_primary=True
        )
    )
    session.flush()
    return entity


def ingest_wallet_transaction(
    session: Session, source: Source, wallet_entity: Entity, fact: RawWalletTransaction
) -> bool:
    """Returns False if this transaction was already ingested for this
    address (idempotent no-op)."""
    ref = _raw_reference(fact.chain, fact.address, fact.tx_hash)
    if session.execute(select(Event).where(Event.raw_data_reference == ref)).scalar_one_or_none() is not None:
        return False

    document = Document(
        source_id=source.id,
        external_id=fact.tx_hash,
        url=f"https://www.blockchain.com/explorer/transactions/btc/{fact.tx_hash}",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    session.flush()

    event = Event(
        event_type=EventType.TREASURY_MOVEMENT,
        primary_entity_id=wallet_entity.id,
        event_time=fact.tx_time,
        publication_time=fact.tx_time,
        source_id=source.id,
        document_id=document.id,
        confidence=1.0,
        raw_data_reference=ref,
    )
    session.add(event)
    session.flush()

    session.add(
        CryptoTreasuryMovement(
            event_id=event.id,
            wallet_entity_id=wallet_entity.id,
            chain=fact.chain,
            address=fact.address,
            tx_hash=fact.tx_hash,
            net_amount=fact.net_amount,
            block_height=fact.block_height,
        )
    )
    session.flush()
    return True


@dataclass
class IngestionSummary:
    wallets_seen: int = 0
    wallets_with_no_activity: int = 0
    movements_created: int = 0
    movements_skipped_duplicate: int = 0
    wallet_errors: list[str] = field(default_factory=list)


def run_ingestion(session: Session, adapter: BlockchainInfoAdapter, addresses: list[str], limit: int = 50) -> IngestionSummary:
    summary = IngestionSummary()
    source = get_or_create_source(session)

    for address in addresses:
        summary.wallets_seen += 1
        try:
            activity = adapter.fetch_wallet_activity(address, limit=limit)
        except Exception as exc:  # noqa: BLE001 — one bad address must not abort the batch
            summary.wallet_errors.append(f"{address}: {exc!r}")
            continue

        if activity is None or not activity.transactions:
            summary.wallets_with_no_activity += 1
            continue

        wallet_entity = get_or_create_wallet_entity(session, activity.chain, address)
        for tx in activity.transactions:
            created = ingest_wallet_transaction(session, source, wallet_entity, tx)
            if created:
                summary.movements_created += 1
            else:
                summary.movements_skipped_duplicate += 1

    session.commit()
    return summary
