from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.sec_xbrl import RawFundamentalFact, SECXBRLFactsAdapter
from capint.ingestion.sec_xbrl import canonicalize_facts, canonicalize_fundamental_facts, run_ingestion
from capint.models.capital_allocation import CapitalAllocationFact
from capint.models.entity import Entity, EntityIdentifier, IdentifierType
from capint.models.event import Event, EventType
from capint.models.fundamentals import FundamentalPeriodType, FundamentalReport
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
    assert first.fundamental_reports_created > 0

    second = run_ingestion(session, make_adapter(), ["0000320193"])
    assert second.facts_created == 0
    assert second.facts_skipped_duplicate == first.facts_created
    assert second.fundamental_reports_created == 0
    assert second.fundamental_reports_skipped_duplicate == first.fundamental_reports_created


def test_unknown_cik_counts_as_no_facts_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["0000000001"])
    assert summary.companies_with_no_facts == 1
    assert summary.company_errors == []
    assert summary.facts_created == 0
    assert summary.fundamental_reports_created == 0


def test_canonicalize_fundamental_facts_picks_earliest_per_metric():
    """Canonicalization is per (metric, period) independently, not one
    accession forced across all five metrics — an earlier version tried
    the latter and silently dropped metrics not tagged in whichever
    accession happened to be earliest overall for a period (see
    canonicalize_fundamental_facts's docstring). In practice, for a
    well-tagged recent fiscal year, every metric's earliest disclosure
    does come from the same accession — this test confirms that's still
    true, without relying on it being forced."""
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")
    facts = adapter.extract_fundamental_facts("0000320193", facts_json)

    canonical = canonicalize_fundamental_facts(facts)
    fy2025_key = (date(2024, 9, 29), date(2025, 9, 27), "FISCAL_YEAR")
    assert fy2025_key in canonical
    by_metric = canonical[fy2025_key]
    assert set(by_metric) == {"revenue", "net_income", "eps_diluted", "gross_profit", "operating_income"}
    assert all(f.accession == "0000320193-25-000079" for f in by_metric.values())


def test_canonicalize_fundamental_facts_does_not_drop_metrics_from_different_accessions():
    """Regression test for the real bug: an earlier version forced one
    "canonical accession" per period, so if metric A's earliest disclosure
    came from an older accession than metric B's, metric B was silently
    dropped entirely. Constructed synthetically since the real fixture's
    trimmed history happens to have every metric agree on one accession
    per period."""

    def make(metric, filed, accession):
        return RawFundamentalFact(
            cik="0000000001",
            entity_name="Test Co (SYNTHETIC)",
            metric=metric,
            xbrl_concept="TestConcept",
            value=Decimal("100"),
            period_start=date(2020, 1, 1),
            period_end=date(2020, 12, 31),
            period_type="FISCAL_YEAR",
            fiscal_year=2020,
            fiscal_period="FY",
            form="10-K",
            accession=accession,
            filed=filed,
        )

    facts = [
        make("revenue", date(2021, 2, 1), "0000000001-21-000001"),
        # net_income's earliest disclosure is a LATER filing than revenue's —
        # e.g. an older filing that only tagged revenue for this period.
        make("net_income", date(2022, 2, 1), "0000000001-22-000001"),
    ]

    canonical = canonicalize_fundamental_facts(facts)
    key = (date(2020, 1, 1), date(2020, 12, 31), "FISCAL_YEAR")
    assert set(canonical[key]) == {"revenue", "net_income"}  # neither dropped


def test_ingestion_creates_fundamental_report_with_computed_margins(session):
    summary = run_ingestion(session, make_adapter(), ["0000320193"])
    assert summary.fundamental_reports_created > 0

    company_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0000320193",
        )
    ).scalar_one()
    company_entity = session.get(Entity, company_ident.entity_id)

    report = session.execute(
        select(FundamentalReport).where(
            FundamentalReport.company_entity_id == company_entity.id,
            FundamentalReport.period_type == FundamentalPeriodType.FISCAL_YEAR,
            FundamentalReport.period_end == date(2025, 9, 27),
        )
    ).scalar_one()

    assert report.revenue_usd == Decimal("416161000000")
    assert report.gross_profit_usd == Decimal("195201000000")
    assert report.operating_income_usd == Decimal("133050000000")
    assert report.net_income_usd == Decimal("112010000000")
    assert report.filing_accession == "0000320193-25-000079"

    expected_gross_margin = Decimal("195201000000") / Decimal("416161000000") * 100
    expected_operating_margin = Decimal("133050000000") / Decimal("416161000000") * 100
    assert abs(report.gross_margin_pct - expected_gross_margin) < Decimal("0.01")
    assert abs(report.operating_margin_pct - expected_operating_margin) < Decimal("0.01")

    event = session.get(Event, report.event_id)
    assert event.event_type == EventType.EARNINGS
    assert event.event_time.date() == date(2025, 9, 27)
    assert event.publication_time.date() == date(2025, 10, 31)
