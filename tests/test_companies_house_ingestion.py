from datetime import date

from sqlalchemy import select

from capint.adapters.companies_house import CompaniesHouseAdapter
from capint.ingestion.companies_house import run_ingestion
from capint.models.company import Company
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.event import Event, EventType
from capint.models.uk_psc import UKPersonWithSignificantControl
from tests.fixtures.companies_house import make_test_client


def make_adapter() -> CompaniesHouseAdapter:
    return CompaniesHouseAdapter(
        api_key="test-key-not-real",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_regulated_market_issuer_ingests_company_with_zero_psc(session):
    summary = run_ingestion(session, make_adapter(), ["00023307"])

    assert summary.companies_seen == 1
    assert summary.companies_with_no_psc == 1
    assert summary.psc_records_created == 0
    assert summary.company_errors == []

    ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.UK_COMPANY_NUMBER,
            EntityIdentifier.identifier_value == "00023307",
        )
    ).scalar_one()
    entity = session.get(Entity, ident.entity_id)
    assert entity.canonical_name == "DIAGEO PLC"
    assert entity.entity_type == EntityType.COMPANY


def test_aim_issuer_ingests_four_psc_records_with_correct_entity_resolution(session):
    summary = run_ingestion(session, make_adapter(), ["05151321"])

    assert summary.companies_seen == 1
    assert summary.companies_with_no_psc == 0
    assert summary.psc_records_created == 4
    assert summary.psc_records_skipped_duplicate == 0
    assert summary.company_errors == []

    company_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.UK_COMPANY_NUMBER,
            EntityIdentifier.identifier_value == "05151321",
        )
    ).scalar_one()
    company = session.get(Company, company_ident.entity_id)

    records = session.execute(
        select(UKPersonWithSignificantControl)
        .where(UKPersonWithSignificantControl.company_entity_id == company.entity_id)
        .order_by(UKPersonWithSignificantControl.notified_on)
    ).scalars().all()
    assert len(records) == 4

    gresham = next(r for r in records if r.psc_name == "Gresham House Asset Management Ltd")
    assert gresham.psc_kind == "corporate-entity-person-with-significant-control"
    assert gresham.ceased_on is None
    assert gresham.natures_of_control == ["voting-rights-25-to-50-percent"]
    # A corporate PSC with a real UK registration number resolves through
    # the same Company path any tracked issuer would use.
    gresham_company_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.UK_COMPANY_NUMBER,
            EntityIdentifier.identifier_value == "09447087",
        )
    ).scalar_one()
    assert gresham.psc_entity_id == gresham_company_ident.entity_id
    gresham_entity = session.get(Entity, gresham.psc_entity_id)
    assert gresham_entity.entity_type == EntityType.COMPANY

    page = next(r for r in records if r.psc_name == "Mr Martyn Graham Page")
    assert page.psc_kind == "individual-person-with-significant-control"
    assert page.ceased_on == date(2018, 11, 12)
    page_entity = session.get(Entity, page.psc_entity_id)
    assert page_entity.entity_type == EntityType.PERSON

    event = session.get(Event, gresham.event_id)
    assert event.event_type == EventType.MAJOR_HOLDER_CHANGE
    assert event.event_time.date() == date(2025, 3, 28)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), ["05151321"])
    assert first.psc_records_created == 4

    second = run_ingestion(session, make_adapter(), ["05151321"])
    assert second.psc_records_created == 0
    assert second.psc_records_skipped_duplicate == 4

    all_events = session.execute(select(Event)).scalars().all()
    assert len(all_events) == 4


def test_unknown_company_number_is_a_company_error_not_a_crash(session):
    summary = run_ingestion(session, make_adapter(), ["99999999"])
    assert summary.company_errors == ["99999999: no such company"]
    assert summary.psc_records_created == 0


def test_corporate_psc_entity_is_reused_not_duplicated_on_rerun(session):
    """Gresham House Asset Management Ltd is a real UK-registered
    corporate PSC of Angling Direct plc. Resolving it a second time (e.g.
    if it later turns up as a PSC of some other tracked company, or is
    ingested directly as an issuer in its own right) must reuse the same
    Company entity, not create a second one for the same real company."""
    from capint.ingestion.companies_house import get_or_create_uk_company

    run_ingestion(session, make_adapter(), ["05151321"])

    gresham_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.UK_COMPANY_NUMBER,
            EntityIdentifier.identifier_value == "09447087",
        )
    ).scalar_one()

    reused = get_or_create_uk_company(session, "09447087", "GRESHAM HOUSE ASSET MANAGEMENT LTD")
    assert reused.entity_id == gresham_ident.entity_id

    all_gresham_entities = session.execute(
        select(Entity).where(Entity.canonical_name.ilike("%Gresham House Asset Management%"))
    ).scalars().all()
    assert len(all_gresham_entities) == 1
