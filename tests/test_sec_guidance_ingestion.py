from datetime import date

from sqlalchemy import select

from capint.adapters.sec_guidance import SECGuidanceDisclosureAdapter
from capint.ingestion.sec_guidance import run_ingestion
from capint.models.entity import Entity, EntityIdentifier, IdentifierType
from capint.models.event import Event, EventType
from capint.models.guidance import GuidanceDisclosure
from tests.fixtures.sec_guidance import KNOWN_CIK, make_test_client


def make_adapter() -> SECGuidanceDisclosureAdapter:
    return SECGuidanceDisclosureAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_company_and_two_disclosures(session):
    summary = run_ingestion(session, make_adapter(), [KNOWN_CIK], filing_count=20)

    assert summary.companies_seen == 1
    assert summary.companies_with_no_disclosures == 0
    assert summary.disclosures_created == 2
    assert summary.disclosures_skipped_duplicate == 0
    assert summary.company_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == KNOWN_CIK,
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.canonical_name == "Apple Inc."

    disclosures = session.execute(
        select(GuidanceDisclosure)
        .where(GuidanceDisclosure.company_entity_id == entity.id)
        .order_by(GuidanceDisclosure.filing_accession)
    ).scalars().all()
    assert len(disclosures) == 2
    assert all(d.item_codes == "2.02,9.01" for d in disclosures)
    assert all(d.filing_form_type == "8-K" for d in disclosures)

    latest = next(d for d in disclosures if d.filing_accession == "0000320193-26-000018")
    event = session.get(Event, latest.event_id)
    assert event.event_type == EventType.GUIDANCE_CHANGE
    assert event.event_time.date() == date(2026, 7, 30)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), [KNOWN_CIK], filing_count=20)
    assert first.disclosures_created == 2

    second = run_ingestion(session, make_adapter(), [KNOWN_CIK], filing_count=20)
    assert second.disclosures_created == 0
    assert second.disclosures_skipped_duplicate == 2

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 2


def test_unknown_cik_counts_as_no_disclosures_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["0000000001"], filing_count=20)
    assert summary.companies_with_no_disclosures == 1
    assert summary.company_errors == []
    assert summary.disclosures_created == 0
