from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.sec_xbrl import SECXBRLFactsAdapter
from capint.ingestion.sec_xbrl import canonicalize_facts, run_ingestion
from capint.models.capital_allocation import CapitalAllocationFact
from capint.models.entity import Entity, EntityIdentifier, IdentifierType
from capint.models.event import Event, EventType
from tests.fixtures.sec_xbrl import make_test_client


def make_adapter() -> SECXBRLFactsAdapter:
    return SECXBRLFactsAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_canonicalize_facts_keeps_earliest_filed_per_period():
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")
    facts = adapter.extract_capital_allocation_facts("0000320193", facts_json)

    canonical = canonicalize_facts(facts)

    debt_issuance_2023 = [
        f
        for f in canonical
        if f.event_type == EventType.DEBT_ISSUANCE and f.period_start == date(2022, 9, 25) and f.period_end == date(2023, 9, 30)
    ]
    assert len(debt_issuance_2023) == 1  # not 3
    assert debt_issuance_2023[0].accession == "0000320193-23-000106"  # earliest filed
    assert debt_issuance_2023[0].filed == date(2023, 11, 3)


def test_ingestion_creates_one_event_per_distinct_period(session):
    summary = run_ingestion(session, make_adapter(), ["0000320193"])

    assert summary.companies_seen == 1
    assert summary.companies_with_no_facts == 0
    assert summary.company_errors == []

    company_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0000320193",
        )
    ).scalar_one()
    company_entity = session.get(Entity, company_ident.entity_id)
    assert company_entity.canonical_name == "Apple Inc."

    events = session.execute(
        select(Event).where(
            Event.primary_entity_id == company_entity.id, Event.event_type == EventType.DEBT_ISSUANCE
        )
    ).scalars().all()
    # Real fixture data has 4 distinct DEBT_ISSUANCE periods (FY2022, FY2023 x1
    # canonicalized from 3 accessions, FY2024, FY2025) — never one row per accession.
    matching_period = [
        e
        for e in events
        if session.get(CapitalAllocationFact, e.id).period_start == date(2022, 9, 25)
    ]
    assert len(matching_period) == 1

    fact = session.get(CapitalAllocationFact, matching_period[0].id)
    assert fact.amount_usd == Decimal("5228000000")
    assert fact.filing_accession == "0000320193-23-000106"


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), ["0000320193"])
    assert first.facts_created > 0

    second = run_ingestion(session, make_adapter(), ["0000320193"])
    assert second.facts_created == 0
    assert second.facts_skipped_duplicate == first.facts_created


def test_unknown_cik_counts_as_no_facts_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["0000000001"])
    assert summary.companies_with_no_facts == 1
    assert summary.company_errors == []
    assert summary.facts_created == 0
