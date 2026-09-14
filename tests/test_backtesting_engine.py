from datetime import date

from capint.backtesting.engine import backtest_rising_short_interest_cycles, compute_forward_return
from tests.fixtures.synthetic import (
    dt,
    make_company,
    make_price_bar,
    make_price_source,
    make_sec_source,
    make_short_interest_snapshot_event,
)


def test_no_price_data_returns_none_with_note(session):
    company = make_company(session)
    session.commit()

    result = compute_forward_return(session, company.entity_id, signal_date=date(2026, 5, 1))
    assert result.forward_return_pct is None
    assert "No ingested price data" in result.note


def test_insufficient_forward_history_returns_none_with_note(session):
    price_source = make_price_source(session)
    company = make_company(session)

    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 1), close="100.00")
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 4), close="102.00")
    session.commit()

    result = compute_forward_return(session, company.entity_id, signal_date=date(2026, 5, 1), holding_trading_days=10)
    assert result.forward_return_pct is None
    assert result.entry_date == date(2026, 5, 1)
    assert result.entry_price == 100
    assert "Only 1 trading day" in result.note


def test_forward_return_computed_correctly_with_enough_history(session):
    price_source = make_price_source(session)
    company = make_company(session)

    # Entry on 2026-05-01 at 100.00; 3 forward bars; exit at the 3rd -> 110.00
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 1), close="100.00")
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 4), close="105.00")
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 5), close="108.00")
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 6), close="110.00")
    session.commit()

    result = compute_forward_return(session, company.entity_id, signal_date=date(2026, 5, 1), holding_trading_days=3)
    assert result.entry_date == date(2026, 5, 1)
    assert result.entry_price == 100
    assert result.exit_date == date(2026, 5, 6)
    assert result.exit_price == 110
    assert result.forward_return_pct == 10  # (110-100)/100 * 100
    assert result.note is None


def test_signal_date_on_a_non_trading_day_uses_next_available_bar(session):
    """FINRA settlement dates are calendar dates, not necessarily trading
    days — the entry price must be the first REAL trading day on or after
    the signal date, never one before it (point-in-time correctness in
    the forward direction)."""
    price_source = make_price_source(session)
    company = make_company(session)

    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 1), close="90.00")  # before signal
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 4), close="100.00")  # first bar on/after signal (5/2, 5/3 weekend)
    make_price_bar(session, company=company, source=price_source, ticker="TEST", trade_date=date(2026, 5, 5), close="105.00")
    session.commit()

    result = compute_forward_return(session, company.entity_id, signal_date=date(2026, 5, 2), holding_trading_days=1)
    assert result.entry_date == date(2026, 5, 4)
    assert result.entry_price == 100
    assert result.exit_date == date(2026, 5, 5)
    assert result.forward_return_pct == 5


def test_backtest_rising_short_interest_cycles_aggregates_real_signals(session):
    sec_source = make_sec_source(session)
    price_source = make_price_source(session)
    rising_co = make_company(session, name="Rising Co (SYNTHETIC)")
    falling_co = make_company(session, name="Falling Co (SYNTHETIC)")

    make_short_interest_snapshot_event(
        session, company=rising_co, source=sec_source, ticker="RISE",
        settlement_date=date(2026, 5, 1), publication_time=dt(2026, 5, 2),
        current_short_position="1100000", previous_short_position="1000000",
        change_percent="10.00", days_to_cover="2.00",
    )
    make_short_interest_snapshot_event(
        session, company=falling_co, source=sec_source, ticker="FALL",
        settlement_date=date(2026, 5, 1), publication_time=dt(2026, 5, 2),
        current_short_position="900000", previous_short_position="1000000",
        change_percent="-10.00", days_to_cover="1.00",
    )
    make_price_bar(session, company=rising_co, source=price_source, ticker="RISE", trade_date=date(2026, 5, 1), close="100.00")
    make_price_bar(session, company=rising_co, source=price_source, ticker="RISE", trade_date=date(2026, 5, 4), close="120.00")
    session.commit()

    summary = backtest_rising_short_interest_cycles(session, as_of=dt(2026, 6, 1), holding_trading_days=1)
    # Only the rising-short-interest company is a signal at all (falling_co
    # never enters this backtest — it's not a "rising" cycle).
    assert summary.signal_count == 1
    assert summary.computable_count == 1
    assert summary.mean_forward_return_pct == 20
    assert summary.positive_count == 1
    assert summary.negative_count == 0


def test_backtest_point_in_time_excludes_not_yet_published_signal(session):
    sec_source = make_sec_source(session)
    company = make_company(session)

    make_short_interest_snapshot_event(
        session, company=company, source=sec_source, ticker="TEST",
        settlement_date=date(2026, 5, 1), publication_time=dt(2026, 6, 15),  # disclosed after as_of
        current_short_position="1100000", previous_short_position="1000000",
        change_percent="10.00", days_to_cover="2.00",
    )
    session.commit()

    summary = backtest_rising_short_interest_cycles(session, as_of=dt(2026, 6, 1))
    assert summary.signal_count == 0
