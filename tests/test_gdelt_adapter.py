"""Adapter-level tests against a mock transport serving a real tone
distribution captured live from GDELT for "Apple Inc". No live network
call."""

from decimal import Decimal

from capint.adapters.gdelt import GDELTAdapter
from tests.fixtures.gdelt import KNOWN_QUERY, make_test_client


def make_adapter() -> GDELTAdapter:
    return GDELTAdapter(client=make_test_client(), min_request_interval=0)


def test_fetch_tone_distribution_parses_real_data():
    adapter = make_adapter()
    result = adapter.fetch_tone_distribution(KNOWN_QUERY)

    assert result is not None
    assert result.query == KNOWN_QUERY
    assert result.timespan == "7d"
    assert result.article_count == 3172
    assert len(result.bins) == 22


def test_mean_tone_computed_correctly():
    adapter = make_adapter()
    result = adapter.fetch_tone_distribution(KNOWN_QUERY)
    # Hand-verified: sum(bin*count)/sum(count) over the real 22-bin distribution
    assert result.mean_tone == Decimal("0.195")


def test_bins_preserve_raw_distribution():
    adapter = make_adapter()
    result = adapter.fetch_tone_distribution(KNOWN_QUERY)
    by_bin = {b.bin: b.count for b in result.bins}
    assert by_bin[0] == 704
    assert by_bin[1] == 730
    assert by_bin[-10] == 1
    assert by_bin[11] == 1  # a real overflow bin beyond GDELT's documented -10..10 range


def test_no_coverage_returns_none():
    adapter = make_adapter()
    result = adapter.fetch_tone_distribution("ZZZZNoCoverageForThisQueryZZZZ")
    assert result is None
