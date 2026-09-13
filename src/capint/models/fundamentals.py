import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class FundamentalPeriodType(str, enum.Enum):
    """Whether a report row is a discrete ~3-month quarter or a full
    fiscal year — never a year-to-date cumulative figure. See
    capint.adapters.sec_xbrl's module docstring: income-statement XBRL
    facts (unlike cash-flow ones) ARE tagged with a genuine discrete-
    quarter duration alongside the YTD one, so both quarterly and annual
    granularity are available here, but YTD is still deliberately
    excluded — it would double-count against the discrete quarters."""

    QUARTER = "QUARTER"
    FISCAL_YEAR = "FISCAL_YEAR"


class FundamentalReport(Base):
    """One-to-one extension of an Event with event_type=EARNINGS: one
    company's core fundamentals (revenue, net income, EPS, gross/operating
    margin) for one discrete quarter or fiscal year, sourced from SEC XBRL
    structured facts (spec §21).

    Deliberately a single row per period covering every metric, unlike
    Phase 7's CapitalAllocationFact (one row per event TYPE per period) —
    a buyback either happened or didn't in a given year (a discrete
    corporate action), but revenue/earnings are always reported every
    period as one coherent disclosure, so one row per period is the
    natural shape. All metric columns are nullable because a company's
    specific XBRL tagging can omit any one of them for a given period.

    `Event.publication_time` here is the 10-Q/10-K FILING date, not the
    (typically earlier) earnings-release/8-K announcement date — a real
    precision gap versus the actual first public disclosure of these
    numbers, documented rather than hidden (see sec_xbrl.py).
    """

    __tablename__ = "fundamental_reports"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    period_type: Mapped[FundamentalPeriodType] = mapped_column(
        Enum(FundamentalPeriodType, name="fundamental_period_type"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(8), nullable=True)  # raw XBRL "fp": Q1-Q4, FY

    revenue_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    net_income_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    eps_diluted: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    gross_profit_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    operating_income_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)

    # Deterministic derivations from the fields above (not a "score" —
    # plain arithmetic on the same disclosed numbers), null if their inputs are.
    gross_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    operating_margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)

    filing_form_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filing_accession: Mapped[str | None] = mapped_column(String(32), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
