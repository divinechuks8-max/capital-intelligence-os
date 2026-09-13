"""Adapter-level tests against a mock transport serving real (trimmed)
Apple Inc. XBRL company facts. No live network call."""

from datetime import date
from decimal import Decimal

from capint.adapters.sec_xbrl import SECXBRLFactsAdapter
from capint.models.event import EventType
from tests.fixtures.sec_xbrl import make_test_client


def make_adapter() -> SECXBRLFactsAdapter:
    return SECXBRLFactsAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_company_facts_returns_none_for_unknown_cik():
    adapter = make_adapter()
    assert adapter.fetch_company_facts("0000000001") is None


def test_extracts_only_annual_10k_facts():
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")
    assert facts_json is not None

    facts = adapter.extract_capital_allocation_facts("0000320193", facts_json)
    assert facts  # non-empty
    assert all(f.form == "10-K" for f in facts)
    assert {f.event_type for f in facts} == {
        EventType.SHARE_BUYBACK,
        EventType.DIVIDEND_PAYMENT,
        EventType.DEBT_ISSUANCE,
        EventType.DEBT_REPAYMENT,
    }


def test_same_economic_fact_appears_under_multiple_accessions():
    """Confirms the real-data finding that drove Phase 7's canonicalization
    step: a 10-K's comparative-year table re-reports a prior fiscal year's
    figure, so the SAME (event_type, period) shows up under more than one
    accession. The adapter itself does NOT deduplicate this — that's
    capint.ingestion.sec_xbrl's job (tested in test_sec_xbrl_ingestion.py)."""
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")
    facts = adapter.extract_capital_allocation_facts("0000320193", facts_json)

    debt_issuance = [f for f in facts if f.event_type == EventType.DEBT_ISSUANCE]
    same_period = [f for f in debt_issuance if f.period_start == date(2022, 9, 25) and f.period_end == date(2023, 9, 30)]
    assert len(same_period) == 3
    assert {f.accession for f in same_period} == {
        "0000320193-23-000106",
        "0000320193-24-000123",
        "0000320193-25-000079",
    }
    assert all(f.amount_usd == Decimal("5228000000") for f in same_period)


def test_quarterly_footnote_facts_tagged_fp_fy_are_excluded():
    """Regression test for a real bug: Apple's 10-K also tags its
    "selected quarterly financial data" footnote, and each ~90-day quarter
    in it is STILL marked fp="FY" (fp describes the filing's overall
    period, not this fact's own duration). Filtering on fp alone let
    quarterly dividend figures through mislabeled as annual totals — fixed
    by checking the fact's actual start/end duration instead."""
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")
    facts = adapter.extract_capital_allocation_facts("0000320193", facts_json)

    dividends = [f for f in facts if f.event_type == EventType.DIVIDEND_PAYMENT]
    assert dividends  # the fixture does include some
    for fact in dividends:
        duration_days = (fact.period_end - fact.period_start).days
        assert duration_days >= 330, (
            f"{fact.period_start}..{fact.period_end} is only {duration_days} days — "
            "a quarterly footnote fact leaked through as if it were annual"
        )


def test_extracts_quarterly_and_annual_fundamental_facts():
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")

    facts = adapter.extract_fundamental_facts("0000320193", facts_json)
    assert facts

    metrics = {f.metric for f in facts}
    assert metrics == {"revenue", "net_income", "eps_diluted", "gross_profit", "operating_income"}

    period_types = {f.period_type for f in facts}
    assert period_types == {"QUARTER", "FISCAL_YEAR"}

    for f in facts:
        duration = (f.period_end - f.period_start).days
        if f.period_type == "QUARTER":
            assert 80 <= duration <= 100
        else:
            assert 330 <= duration <= 380


def test_revenue_concept_migration_uses_whichever_tag_is_present():
    """Revenues (pre-ASC606) and RevenueFromContractWithCustomerExcludingAssessedTax
    (post-ASC606) are a genuine tag migration, not independent metrics —
    confirmed live (both report identically for every period Apple tags
    under both). Facts from either concept feed the same "revenue" metric."""
    adapter = make_adapter()
    facts_json = adapter.fetch_company_facts("0000320193")
    facts = adapter.extract_fundamental_facts("0000320193", facts_json)

    revenue_concepts = {f.xbrl_concept for f in facts if f.metric == "revenue"}
    assert revenue_concepts <= {"Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"}
    assert revenue_concepts  # at least one of the two actually contributed facts


def test_disagreeing_concept_values_for_same_period_are_dropped():
    """Synthetic test of the defensive merge logic (the real fixture's
    trimmed history happens not to retain an overlap period): if two
    candidate concepts for the same metric ever disagree for the same
    period — an alias conflict, or a genuine restatement — that period is
    dropped rather than guessing which number is right."""
    facts = {
        "entityName": "Test Co (SYNTHETIC)",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "start": "2020-01-01",
                                "end": "2020-12-31",
                                "val": 1000,
                                "accn": "0000000001-21-000001",
                                "filed": "2021-02-01",
                                "form": "10-K",
                                "fy": 2020,
                                "fp": "FY",
                            }
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2020-01-01",
                                "end": "2020-12-31",
                                "val": 999,  # deliberately disagrees with Revenues above
                                "accn": "0000000001-21-000001",
                                "filed": "2021-02-01",
                                "form": "10-K",
                                "fy": 2020,
                                "fp": "FY",
                            }
                        ]
                    }
                },
            }
        },
    }
    adapter = make_adapter()
    results = adapter.extract_fundamental_facts("0000000001", facts)
    assert [r for r in results if r.metric == "revenue"] == []


def test_missing_user_agent_is_rejected():
    import pytest

    with pytest.raises(ValueError):
        SECXBRLFactsAdapter(user_agent="")
