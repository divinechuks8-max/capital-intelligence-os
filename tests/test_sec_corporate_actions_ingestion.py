from datetime import date

from sqlalchemy import select

from capint.adapters.sec_corporate_actions import SECCorporateActionAdapter
from capint.ingestion.sec_corporate_actions import run_ingestion
from capint.models.corporate_action import CorporateActionDisclosure
from capint.models.entity import Entity, EntityIdentifier, IdentifierType
from capint.models.event import Event, EventType
from tests.fixtures.sec_corporate_actions import KNOWN_CIK, make_test_client


def make_adapter() -> SECCorporateActionAdapter:
    return SECCorporateActionAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_company_and_one_disclosure(session):
    summary = run_ingestion(session, make_adapter(), [KNOWN_CIK], filing_count=20)

    assert summary.companies_seen == 1
    assert summary.companies_with_no_disclosures == 0
    assert summary.disclosures_created == 1
    assert summary.disclosures_skipped_duplicate == 0
    assert summary.company_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == KNOWN_CIK,
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.canonical_name == "MICROSOFT CORP"

    disclosure = session.execute(
        select(CorporateActionDisclosure).where(CorporateActionDisclosure.company_entity_id == entity.id)
    ).scalar_one()
    assert disclosure.item_codes == "2.01"
    assert disclosure.filing_accession == "0001193125-23-255762"

    event = session.get(Event, disclosure.event_id)
    assert event.event_type == EventType.M_AND_A
    assert event.event_time.date() == date(2023, 10, 13)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), [KNOWN_CIK], filing_count=20)
    assert first.disclosures_created == 1

    second = run_ingestion(session, make_adapter(), [KNOWN_CIK], filing_count=20)
    assert second.disclosures_created == 0
    assert second.disclosures_skipped_duplicate == 1

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 1


def test_unknown_cik_counts_as_no_disclosures_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["0000000001"], filing_count=20)
    assert summary.companies_with_no_disclosures == 1
    assert summary.company_errors == []
    assert summary.disclosures_created == 0


def test_corporate_action_and_guidance_refs_do_not_collide(session):
    """A guidance-relevant 8-K and this system's M&A 8-K use similarly-
    shaped raw_data_reference strings for different accessions here, but
    even if the SAME accession carried both categories' item codes, they
    must not collide on Event.raw_data_reference's unique constraint —
    confirmed by checking the two ingesters produce distinct ref strings
    for the identical accession number."""
    from capint.ingestion.sec_corporate_actions import _raw_reference as corp_action_ref
    from capint.ingestion.sec_guidance import _raw_reference as guidance_ref

    accession = "0001193125-23-255762"
    assert corp_action_ref(accession) != guidance_ref(accession)
