from decimal import Decimal

from sqlalchemy import select

from capint.adapters.blockchain_info import BlockchainInfoAdapter
from capint.ingestion.blockchain_info import run_ingestion
from capint.models.crypto import CryptoTreasuryMovement
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from tests.fixtures.blockchain_info import KNOWN_ADDRESS, make_test_client


def make_adapter() -> BlockchainInfoAdapter:
    return BlockchainInfoAdapter(client=make_test_client(), min_request_interval=0)


def test_ingestion_creates_wallet_entity_and_three_movements(session):
    summary = run_ingestion(session, make_adapter(), [KNOWN_ADDRESS], limit=3)

    assert summary.wallets_seen == 1
    assert summary.wallets_with_no_activity == 0
    assert summary.movements_created == 3
    assert summary.movements_skipped_duplicate == 0
    assert summary.wallet_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.WALLET_ADDRESS,
            EntityIdentifier.identifier_value == f"bitcoin:{KNOWN_ADDRESS}",
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.entity_type == EntityType.CRYPTO_WALLET
    # No owner attribution is claimed — the wallet's name is its raw address.
    assert entity.canonical_name == KNOWN_ADDRESS

    movements = session.execute(
        select(CryptoTreasuryMovement).where(CryptoTreasuryMovement.wallet_entity_id == entity.id)
    ).scalars().all()
    assert len(movements) == 3
    assert all(m.chain == "bitcoin" for m in movements)
    assert all(m.net_amount > 0 for m in movements)  # this address only ever receives

    event_ids = {m.event_id for m in movements}
    events = session.execute(select(Event).where(Event.id.in_(event_ids))).scalars().all()
    assert all(e.event_type == EventType.TREASURY_MOVEMENT for e in events)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), [KNOWN_ADDRESS], limit=3)
    assert first.movements_created == 3

    second = run_ingestion(session, make_adapter(), [KNOWN_ADDRESS], limit=3)
    assert second.movements_created == 0
    assert second.movements_skipped_duplicate == 3

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 3


def test_unknown_address_counts_as_no_activity_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["1BitcoinAddressThatDoesNotExistXXXXXX"], limit=3)
    assert summary.wallets_with_no_activity == 1
    assert summary.wallet_errors == []
    assert summary.movements_created == 0
