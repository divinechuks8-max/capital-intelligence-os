"""Historical validation / backtesting harness (Phase 13, pipeline's
HISTORICAL VALIDATION stage).

**Research/exploratory descriptive statistics only — never a trading
signal, a probability, or investment advice.** This computes what
actually happened to a company's price after a real, already-disclosed
signal date, using real ingested price data (capint.models.price.PriceBar).
It reports plain counts/means/medians of observed forward returns; it
does not compute or claim statistical significance, does not correct for
multiple comparisons, and a small sample here should never be read as
predictive. A human must interpret these numbers, per this project's spec
(RESEARCH -> ALERT -> HUMAN DECISION, never an automated trading action).

**No ingestion path currently populates PriceBar** — see that model's
docstring for why the original Alpha Vantage source (Phase 13) was
removed in Phase 15 (a real Terms of Service violation for this
platform's architecture, not a technical limitation) and why a genuinely
free, compliant replacement wasn't found despite checking four vendors.
This engine's logic remains valid and ready the moment a compliant price
source is identified; every call here will simply find no rows to work
with until then, and report that honestly via `note` rather than
fabricating a result.

Point-in-time discipline: `compute_forward_return`'s entry price is the
first price bar ON OR AFTER the signal date, never before it — the same
"never let a later fact leak into an earlier view" principle this system
enforces everywhere else, applied in the other temporal direction (a
forward-looking check must not accidentally use a price bar that predates
the signal).
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capint.models.event import Event, EventType
from capint.models.price import PriceBar
from capint.models.short_interest import ShortInterestSnapshot

DEFAULT_HOLDING_TRADING_DAYS = 10


@dataclass(frozen=True)
class ForwardReturnResult:
    company_entity_id: uuid.UUID
    signal_date: date
    holding_trading_days: int
    entry_date: date | None = None
    entry_price: Decimal | None = None
    exit_date: date | None = None
    exit_price: Decimal | None = None
    forward_return_pct: Decimal | None = None
    note: str | None = None


def compute_forward_return(
    session: Session,
    company_entity_id: uuid.UUID,
    signal_date: date,
    holding_trading_days: int = DEFAULT_HOLDING_TRADING_DAYS,
) -> ForwardReturnResult:
    """Returns the observed close-to-close return from the first trading
    day on/after `signal_date` to `holding_trading_days` trading days
    later, using whatever real price data has been ingested for this
    company. `forward_return_pct` is None (with `note` explaining why)
    when there isn't enough ingested price history to compute it — never
    fabricated."""
    entry = session.execute(
        select(PriceBar)
        .where(PriceBar.company_entity_id == company_entity_id, PriceBar.trade_date >= signal_date)
        .order_by(PriceBar.trade_date.asc())
        .limit(1)
    ).scalar_one_or_none()
    if entry is None:
        return ForwardReturnResult(
            company_entity_id=company_entity_id,
            signal_date=signal_date,
            holding_trading_days=holding_trading_days,
            note="No ingested price data on or after the signal date for this company.",
        )

    forward_bars = session.execute(
        select(PriceBar)
        .where(PriceBar.company_entity_id == company_entity_id, PriceBar.trade_date > entry.trade_date)
        .order_by(PriceBar.trade_date.asc())
        .limit(holding_trading_days)
    ).scalars().all()
    if len(forward_bars) < holding_trading_days:
        return ForwardReturnResult(
            company_entity_id=company_entity_id,
            signal_date=signal_date,
            holding_trading_days=holding_trading_days,
            entry_date=entry.trade_date,
            entry_price=entry.close,
            note=(
                f"Only {len(forward_bars)} trading day(s) of forward price history ingested (need "
                f"{holding_trading_days}) — see capint.models.price.PriceBar's docstring: no "
                "ingestion path currently populates this table."
            ),
        )

    exit_bar = forward_bars[-1]
    forward_return_pct = (exit_bar.close - entry.close) / entry.close * 100
    return ForwardReturnResult(
        company_entity_id=company_entity_id,
        signal_date=signal_date,
        holding_trading_days=holding_trading_days,
        entry_date=entry.trade_date,
        entry_price=entry.close,
        exit_date=exit_bar.trade_date,
        exit_price=exit_bar.close,
        forward_return_pct=forward_return_pct,
    )


@dataclass
class BacktestSummary:
    """Plain descriptive statistics over whatever forward returns could
    actually be computed — never a claim of significance. See this
    module's docstring."""

    signal_count: int
    computable_count: int
    mean_forward_return_pct: float | None
    median_forward_return_pct: float | None
    positive_count: int
    negative_count: int
    results: list[ForwardReturnResult] = field(default_factory=list)


def _summarize(results: list[ForwardReturnResult]) -> BacktestSummary:
    computable = [r.forward_return_pct for r in results if r.forward_return_pct is not None]
    values = [float(v) for v in computable]
    return BacktestSummary(
        signal_count=len(results),
        computable_count=len(values),
        mean_forward_return_pct=statistics.mean(values) if values else None,
        median_forward_return_pct=statistics.median(values) if values else None,
        positive_count=sum(1 for v in values if v > 0),
        negative_count=sum(1 for v in values if v < 0),
        results=results,
    )


def backtest_rising_short_interest_cycles(
    session: Session,
    as_of: datetime,
    holding_trading_days: int = DEFAULT_HOLDING_TRADING_DAYS,
) -> BacktestSummary:
    """For every real FINRA settlement cycle with a reported increase in
    short interest (point-in-time gated on Event.publication_time, as
    usual), computes the observed forward return over the following
    `holding_trading_days` trading days — an exploratory check of what
    historically followed a rising-short-interest signal in this system's
    own ingested data, not a claim about what will happen next time."""
    stmt = (
        select(Event, ShortInterestSnapshot)
        .join(ShortInterestSnapshot, ShortInterestSnapshot.event_id == Event.id)
        .where(
            Event.event_type == EventType.SHORT_INTEREST_CHANGE,
            Event.publication_time <= as_of,
            ShortInterestSnapshot.change_percent > 0,
        )
        .order_by(ShortInterestSnapshot.settlement_date)
    )
    rows = session.execute(stmt).all()

    results = [
        compute_forward_return(session, snapshot.company_entity_id, snapshot.settlement_date, holding_trading_days)
        for _event, snapshot in rows
    ]
    return _summarize(results)
