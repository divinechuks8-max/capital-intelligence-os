"""Adapter-level tests against a mock transport serving real data captured
live from Finnhub for AAPL. No live network call."""

from datetime import date

import pytest

from capint.adapters.finnhub import FinnhubAdapter
from tests.fixtures.finnhub import KNOWN_TICKER, make_test_client


def make_adapter() -> FinnhubAdapter:
    return FinnhubAdapter(api_key="test-key-not-real", client=make_test_client(), min_request_interval=0)


def test_fetch_recommendation_trends_parses_real_data():
    adapter = make_adapter()
    trends = adapter.fetch_recommendation_trends(KNOWN_TICKER)

    assert len(trends) == 4
    assert trends[0].period == date(2026, 6, 1)  # sorted chronologically
    assert trends[-1].period == date(2026, 9, 1)


def test_trend_fields_parsed_correctly():
    adapter = make_adapter()
    trends = adapter.fetch_recommendation_trends(KNOWN_TICKER)
    latest = next(t for t in trends if t.period == date(2026, 9, 1))

    assert latest.ticker == "AAPL"
    assert latest.strong_buy == 12
    assert latest.buy == 22
    assert latest.hold == 15
    assert latest.sell == 3
    assert latest.strong_sell == 1


def test_unknown_ticker_returns_empty_list():
    adapter = make_adapter()
    assert adapter.fetch_recommendation_trends("ZZZZNOTAREALTICKERZZ") == []


def test_missing_api_key_is_rejected():
    with pytest.raises(ValueError):
        FinnhubAdapter(api_key="")
