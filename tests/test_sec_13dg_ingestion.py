from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.sec_13dg import SEC13DGAdapter
from capint.ingestion.sec_13dg import run_ingestion
from capint.ingestion.sec_form4 import get_or_create_person
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.ownership import BeneficialOwnershipDisclosure, ScheduleType
from tests.fixtures.sec_13dg import make_test_client


def make_adapter() -> SEC13DGAdapter:
    return SEC13DGAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_activist_stake_disclosures_for_13d(session):
    summary = run_ingestion(session, make_adapter(), date(2026, 9, 1), date(2026, 9, 13))

    assert summary.filings_seen == 2  # 1 real 13D lead + 1 real 13G lead (amendment already filtered by adapter)
    assert summary.disclosures_created == 3  # 2 persons on the 13D + 1 on the 13G
    assert summary.disclosures_skipped_duplicate == 0
    assert summary.filing_errors == []

    issuer_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0001500435",
        )
    ).scalar_one()
    issuer_entity = session.get(Entity, issuer_ident.entity_id)
    assert issuer_entity.canonical_name == "GoPro, Inc."

    disclosures = session.execute(
        select(BeneficialOwnershipDisclosure, Event)
        .join(Event, BeneficialOwnershipDisclosure.event_id == Event.id)
        .where(BeneficialOwnershipDisclosure.company_entity_id == issuer_entity.id)
    ).all()
    assert len(disclosures) == 2
    for disclosure, event in disclosures:
        assert disclosure.schedule_type == ScheduleType.SCHEDULE_13D
        assert event.event_type == EventType.ACTIVIST_STAKE
        assert disclosure.is_joint_filing is True
        assert disclosure.stated_purpose is not None
        assert disclosure.event_date == date(2026, 9, 1)

    woodman_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0001610500",
        )
    ).scalar_one()
    woodman_entity = session.get(Entity, woodman_ident.entity_id)
    assert woodman_entity.entity_type == EntityType.PERSON

    trust_entity = session.execute(
        select(Entity).where(Entity.entity_type == EntityType.OTHER, Entity.canonical_name.like("Woodman Family Trust%"))
    ).scalar_one()
    trust_ident_count = session.execute(
        select(EntityIdentifier).where(EntityIdentifier.entity_id == trust_entity.id)
    ).all()
    assert trust_ident_count == []  # no CIK given -> no identifier row, resolved by name only


def test_ingestion_creates_major_holder_change_disclosure_for_13g(session):
    run_ingestion(session, make_adapter(), date(2026, 9, 1), date(2026, 9, 13))

    issuer_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0001785279",
        )
    ).scalar_one()
    issuer_entity = session.get(Entity, issuer_ident.entity_id)
    assert issuer_entity.canonical_name == "Metagenomi Therapeutics, Inc."

    disclosure, event = session.execute(
        select(BeneficialOwnershipDisclosure, Event)
        .join(Event, BeneficialOwnershipDisclosure.event_id == Event.id)
        .where(BeneficialOwnershipDisclosure.company_entity_id == issuer_entity.id)
    ).one()
    assert disclosure.schedule_type == ScheduleType.SCHEDULE_13G
    assert event.event_type == EventType.MAJOR_HOLDER_CHANGE
    assert disclosure.stated_purpose is None
    assert disclosure.is_joint_filing is False
    assert disclosure.percent_of_class == Decimal("7.3")


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), date(2026, 9, 1), date(2026, 9, 13))
    assert first.disclosures_created == 3

    second = run_ingestion(session, make_adapter(), date(2026, 9, 1), date(2026, 9, 13))
    assert second.disclosures_created == 0
    assert second.disclosures_skipped_duplicate == 3

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 3


def test_individual_reporting_person_cross_links_with_form4_entity(session):
    """A person already known to the system via Form 4 (CIK-based) must
    resolve to the SAME Entity when later seen as a Schedule 13D reporting
    person — both use SEC's CIK, so this is one case where entity
    resolution genuinely unifies across signal families (contrast Phase
    4's 13F/Form-4 CUSIP mismatch)."""
    pre_existing = get_or_create_person(session, "0001610500", "Woodman Nicholas (pre-existing)")
    session.commit()

    run_ingestion(session, make_adapter(), date(2026, 9, 1), date(2026, 9, 13))

    woodman_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0001610500",
        )
    ).scalar_one()
    assert woodman_ident.entity_id == pre_existing.entity_id
