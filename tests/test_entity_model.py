from datetime import date

from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from tests.fixtures.synthetic import make_company


def test_entity_identifier_roundtrip(session):
    company = make_company(session)
    ident = EntityIdentifier(
        entity_id=company.entity_id,
        identifier_type=IdentifierType.TICKER,
        identifier_value="ACME",
        exchange="NASDAQ",
        is_primary=True,
        valid_from=date(2020, 1, 1),
    )
    session.add(ident)
    session.commit()

    fetched = session.get(Entity, company.entity_id)
    assert fetched.canonical_name.startswith("Acme Robotics")
    assert len(fetched.identifiers) == 1
    assert fetched.identifiers[0].identifier_value == "ACME"


def test_ticker_reassignment_is_time_scoped(session):
    """Two different entities can legitimately hold the same ticker in
    different eras — resolution must never be a naive ticker lookup."""
    old_co = make_company(session, name="Old Co (SYNTHETIC, delisted)")
    new_co = make_company(session, name="New Co (SYNTHETIC, current)")

    old_ident = EntityIdentifier(
        entity_id=old_co.entity_id,
        identifier_type=IdentifierType.TICKER,
        identifier_value="ZZZ",
        valid_from=date(2010, 1, 1),
        valid_to=date(2015, 12, 31),
    )
    new_ident = EntityIdentifier(
        entity_id=new_co.entity_id,
        identifier_type=IdentifierType.TICKER,
        identifier_value="ZZZ",
        valid_from=date(2016, 1, 1),
    )
    session.add_all([old_ident, new_ident])
    session.commit()

    assert old_ident.is_valid_on(date(2012, 6, 1)) is True
    assert old_ident.is_valid_on(date(2020, 1, 1)) is False
    assert new_ident.is_valid_on(date(2020, 1, 1)) is True
    assert new_ident.is_valid_on(date(2012, 6, 1)) is False


def test_entity_type_enum_values_present():
    assert EntityType.CRYPTO_WALLET.value == "CRYPTO_WALLET"
    assert EntityType.ACTIVIST.value == "ACTIVIST"
