"""Adapter-level tests against a mock transport serving real data captured
live from Cboe for VIX. No live network call."""

from datetime import date
from decimal import Decimal

from capint.adapters.cboe_volatility import CBOEVolatilityIndexAdapter
from tests.fixtures.cboe_volatility import KNOWN_INDEX, make_test_client


def make_adapter() -> CBOEVolatilityIndexAdapter:
    return CBOEVolatilityIndexAdapter(client=make_test_client(), min_request_interval=0)


def test_fetch_index_history_parses_real_data():
    adapter = make_adapter()
    levels = adapter.fetch_index_history(KNOWN_INDEX)

    assert len(levels) == 10
    assert levels[0].trade_date == date(2026, 8, 31)
    assert levels[-1].trade_date == date(2026, 9, 11)
    assert all(lvl.index_code == "VIX" for lvl in levels)


def test_level_fields_parsed_correctly():
    adapter = make_adapter()
    levels = adapter.fetch_index_history(KNOWN_INDEX)
    latest = next(lvl for lvl in levels if lvl.trade_date == date(2026, 9, 11))

    assert latest.open == Decimal("17.510000")
    assert latest.high == Decimal("17.710000")
    assert latest.low == Decimal("15.590000")
    assert latest.close == Decimal("15.840000")


def test_lowercase_index_code_accepted():
    adapter = make_adapter()
    levels = adapter.fetch_index_history("vix")
    assert len(levels) == 10
    assert levels[0].index_code == "VIX"


def test_unknown_index_code_returns_empty_list():
    adapter = make_adapter()
    assert adapter.fetch_index_history("ZZZZNOTAREALINDEX") == []
