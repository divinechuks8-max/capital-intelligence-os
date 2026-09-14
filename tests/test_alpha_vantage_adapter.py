"""Adapter-level tests against a mock transport serving real data captured
live from Alpha Vantage for AAPL. No live network call."""

from datetime import date
from decimal import Decimal

import pytest

from capint.adapters.alpha_vantage import AlphaVantageAdapter
from tests.fixtures.alpha_vantage import KNOWN_TICKER, make_test_client


def make_adapter() -> AlphaVantageAdapter:
    return AlphaVantageAdapter(api_key="test-key-not-real", client=make_test_client(), min_request_interval=0)


def test_fetch_daily_prices_parses_real_data():
    adapter = make_adapter()
    bars = adapter.fetch_daily_prices(KNOWN_TICKER)

    assert len(bars) == 100
    assert bars[0].trade_date == date(2026, 4, 21)
    assert bars[-1].trade_date == date(2026, 9, 11)
    assert bars == sorted(bars, key=lambda b: b.trade_date)  # chronological order


def test_bar_fields_parsed_correctly():
    adapter = make_adapter()
    bars = adapter.fetch_daily_prices(KNOWN_TICKER)
    latest = next(b for b in bars if b.trade_date == date(2026, 9, 11))

    assert latest.ticker == "AAPL"
    assert latest.open == Decimal("327.4500")
    assert latest.high == Decimal("336.2200")
    assert latest.low == Decimal("326.3000")
    assert latest.close == Decimal("332.2700")
    assert latest.volume == 50716865


def test_outputsize_full_returns_empty_list_not_an_error():
    """outputsize=full is a confirmed-live premium-only feature on the
    free tier — the API returns an "Information" message, not price data,
    and this adapter must not mistake that for a crash."""
    adapter = make_adapter()
    bars = adapter.fetch_daily_prices(KNOWN_TICKER, outputsize="full")
    assert bars == []


def test_unknown_ticker_returns_empty_list():
    adapter = make_adapter()
    assert adapter.fetch_daily_prices("ZZZZNOTAREALTICKERZZ") == []


def test_missing_api_key_is_rejected():
    with pytest.raises(ValueError):
        AlphaVantageAdapter(api_key="")
