from datetime import date
from decimal import Decimal

from sqlalchemy import select

from capint.adapters.cboe_volatility import CBOEVolatilityIndexAdapter
from capint.ingestion.cboe_volatility import run_ingestion
from capint.models.volatility import VolatilityIndexLevel
from tests.fixtures.cboe_volatility import KNOWN_INDEX, make_test_client


def make_adapter() -> CBOEVolatilityIndexAdapter:
    return CBOEVolatilityIndexAdapter(client=make_test_client(), min_request_interval=0)


def test_ingestion_creates_ten_levels(session):
    summary = run_ingestion(session, make_adapter(), [KNOWN_INDEX])

    assert summary.index_codes_seen == 1
    assert summary.index_codes_with_no_data == 0
    assert summary.levels_created == 10
    assert summary.levels_skipped_duplicate == 0
    assert summary.index_code_errors == []

    levels = session.execute(
        select(VolatilityIndexLevel).where(VolatilityIndexLevel.index_code == "VIX").order_by(VolatilityIndexLevel.trade_date)
    ).scalars().all()
    assert len(levels) == 10
    assert levels[-1].trade_date == date(2026, 9, 11)
    assert levels[-1].close == Decimal("15.840000")


def test_rerunning_ingestion_is_idempotent(session):
    first = run_ingestion(session, make_adapter(), [KNOWN_INDEX])
    assert first.levels_created == 10

    second = run_ingestion(session, make_adapter(), [KNOWN_INDEX])
    assert second.levels_created == 0
    assert second.levels_skipped_duplicate == 10


def test_unknown_index_code_counts_as_no_data_not_an_error(session):
    summary = run_ingestion(session, make_adapter(), ["ZZZZNOTAREALINDEX"])
    assert summary.index_codes_with_no_data == 1
    assert summary.index_code_errors == []
    assert summary.levels_created == 0
