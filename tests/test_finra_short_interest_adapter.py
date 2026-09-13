"""Adapter-level tests against a mock transport serving real FINRA
consolidatedShortInterest data captured live for AAPL across three real
settlement cycles. No live network call."""

from datetime import date
from decimal import Decimal

import pytest

from capint.adapters.finra_short_interest import FINRAShortInterestAdapter
from tests.fixtures.finra_short_interest import make_test_client


def make_adapter() -> FINRAShortInterestAdapter:
    return FINRAShortInterestAdapter(
        user_agent="Capital Intelligence OS tests test@example.com",
        client=make_test_client(),
        min_request_interval=0,
    )


def test_fetch_short_interest_parses_real_aapl_figures():
    adapter = make_adapter()
    fact = adapter.fetch_short_interest("AAPL", date(2026, 8, 31))

    assert fact is not None
    assert fact.ticker == "AAPL"
    assert fact.issue_name == "Apple Inc. Common Stock"
    assert fact.settlement_date == date(2026, 8, 31)
    assert fact.current_short_position == Decimal("139749097")
    assert fact.previous_short_position == Decimal("116327753")
    assert fact.change_percent == Decimal("20.13")
    assert fact.change_quantity == Decimal("23421344")
    assert fact.average_daily_volume == Decimal("39537335")
    assert fact.days_to_cover == Decimal("3.53")
    assert fact.exchange_code == "R"
    assert fact.market_class_code == "NNM"


def test_fetch_short_interest_prior_cycle():
    adapter = make_adapter()
    fact = adapter.fetch_short_interest("AAPL", date(2026, 8, 14))

    assert fact is not None
    assert fact.current_short_position == Decimal("116327753")
    assert fact.change_percent == Decimal("-17.85")


def test_fetch_short_interest_lowercases_ticker_accepted():
    adapter = make_adapter()
    fact = adapter.fetch_short_interest("aapl", date(2026, 8, 31))
    assert fact is not None
    assert fact.ticker == "AAPL"


def test_fetch_short_interest_returns_none_for_unknown_ticker():
    adapter = make_adapter()
    fact = adapter.fetch_short_interest("ZZZZNOTAREALTICKERZZ", date(2026, 8, 31))
    assert fact is None


def test_fetch_short_interest_returns_none_for_no_data_settlement_date():
    """2026-08-29 is a plausible mid-month candidate that FINRA simply has
    no cycle for (confirmed live via HTTP 204) — the settlement-date
    schedule shifts around holidays/weekends, so a wrong guess must not
    look like an error."""
    adapter = make_adapter()
    fact = adapter.fetch_short_interest("AAPL", date(2026, 8, 29))
    assert fact is None


def test_fetch_recent_settlement_dates_skips_non_cycle_candidates_and_finds_real_ones():
    adapter = make_adapter()
    dates = adapter.fetch_recent_settlement_dates(as_of=date(2026, 9, 13), num_cycles=3)

    assert dates == [date(2026, 8, 31), date(2026, 8, 14), date(2026, 7, 31)]


def test_fetch_recent_settlement_dates_respects_num_cycles():
    adapter = make_adapter()
    dates = adapter.fetch_recent_settlement_dates(as_of=date(2026, 9, 13), num_cycles=1)
    assert dates == [date(2026, 8, 31)]


def test_missing_user_agent_is_rejected():
    with pytest.raises(ValueError):
        FINRAShortInterestAdapter(user_agent="")
