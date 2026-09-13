from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.sec_13f import Filing13FData, RawHolding, SEC13FAdapter
from capint.ingestion.sec_13f import get_or_create_source, ingest_filing, run_ingestion
from capint.models.entity import Entity, EntityIdentifier, EntityType, IdentifierType
from capint.models.institution import InstitutionalHolding, InstitutionalPositionStatus
from tests.fixtures.sec_13f import make_test_client


def make_adapter() -> SEC13FAdapter:
    return SEC13FAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_ingestion_creates_institution_companies_and_holdings(session):
    summary = run_ingestion(session, make_adapter(), filing_count=10)

    assert summary.filings_seen == 1
    assert summary.filings_ingested == 1
    assert summary.holdings_created == 3  # AMD, AGNC, Alphabet A (aggregated by CUSIP)
    assert summary.exits_created == 0
    assert summary.filing_errors == []

    institution_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CIK,
            EntityIdentifier.identifier_value == "0001990467",
        )
    ).scalar_one()
    institution_entity = session.get(Entity, institution_ident.entity_id)
    assert institution_entity.entity_type == EntityType.INSTITUTION
    assert institution_entity.canonical_name == "Talon Private Wealth, LLC"

    amd_ident = session.execute(
        select(EntityIdentifier).where(
            EntityIdentifier.identifier_type == IdentifierType.CUSIP,
            EntityIdentifier.identifier_value == "007903107",
        )
    ).scalar_one()
    amd_entity = session.get(Entity, amd_ident.entity_id)
    assert amd_entity.canonical_name == "ADVANCED MICRO DEVICES INC"

    holding = session.execute(
        select(InstitutionalHolding).where(InstitutionalHolding.company_entity_id == amd_entity.id)
    ).scalar_one()
    assert holding.shares_held == Decimal("2328")
    assert holding.position_status == InstitutionalPositionStatus.NEW
    assert holding.period_of_report == date(2026, 6, 30)


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), filing_count=10)
    assert first.filings_ingested == 1

    second = run_ingestion(session, make_adapter(), filing_count=10)
    assert second.filings_ingested == 0
    assert second.filings_skipped_duplicate == 1

    all_holdings = session.execute(select(InstitutionalHolding)).scalars().all()
    assert len(all_holdings) == 3  # unchanged from the first run


def test_position_status_transitions_across_quarters(session):
    """Hand-built two-quarter scenario (bypassing the adapter, which only
    has one real fixture quarter available) to exercise NEW -> INCREASED /
    DECREASED / EXITED against a controlled prior period."""
    source = get_or_create_source(session)

    q1 = Filing13FData(
        accession_number="0000000001-26-000001",
        filing_url="https://example.test/q1",
        published_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
        form_type="13F-HR",
        filer_cik="0001111111",
        filer_name="Example Capital Management",
        period_of_report=date(2026, 3, 31),
        holdings=[
            RawHolding(cusip="AAA111111", issuer_name="Company A", shares=Decimal("1000"), market_value_usd=Decimal("50000")),
            RawHolding(cusip="BBB222222", issuer_name="Company B", shares=Decimal("500"), market_value_usd=Decimal("20000")),
        ],
    )
    ingest_filing(session, source, q1)
    session.commit()

    q2 = Filing13FData(
        accession_number="0000000001-26-000002",
        filing_url="https://example.test/q2",
        published_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        form_type="13F-HR",
        filer_cik="0001111111",
        filer_name="Example Capital Management",
        period_of_report=date(2026, 6, 30),
        holdings=[
            RawHolding(cusip="AAA111111", issuer_name="Company A", shares=Decimal("1500"), market_value_usd=Decimal("80000")),
            RawHolding(cusip="CCC333333", issuer_name="Company C", shares=Decimal("200"), market_value_usd=Decimal("9000")),
            # Company B is absent from Q2 -> must be recorded as EXITED.
        ],
    )
    result = ingest_filing(session, source, q2)
    session.commit()

    assert result.holdings_created == 2
    assert result.exits_created == 1

    holdings_q2 = session.execute(
        select(InstitutionalHolding).where(InstitutionalHolding.period_of_report == date(2026, 6, 30))
    ).scalars().all()

    assert InstitutionalPositionStatus.INCREASED in [h.position_status for h in holdings_q2]
    assert InstitutionalPositionStatus.NEW in [h.position_status for h in holdings_q2]
    assert InstitutionalPositionStatus.EXITED in [h.position_status for h in holdings_q2]

    increased = next(h for h in holdings_q2 if h.position_status == InstitutionalPositionStatus.INCREASED)
    assert increased.shares_held == Decimal("1500")
    assert increased.shares_change == Decimal("500")

    exited = next(h for h in holdings_q2 if h.position_status == InstitutionalPositionStatus.EXITED)
    assert exited.shares_held == 0
    assert exited.shares_change == Decimal("-500")

    new = next(h for h in holdings_q2 if h.position_status == InstitutionalPositionStatus.NEW)
    assert new.shares_held == Decimal("200")
