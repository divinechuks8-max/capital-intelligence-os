import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class CapitalAllocationFact(Base):
    """One-to-one extension of an Event with event_type in {SHARE_BUYBACK,
    DIVIDEND_PAYMENT, DEBT_ISSUANCE, DEBT_REPAYMENT} (spec §16).

    Sourced from SEC XBRL structured facts, annual (10-K) duration only —
    see capint.adapters.sec_xbrl's module docstring for why quarterly
    cash-flow figures are deliberately not derived. `xbrl_concept` and
    `filing_accession` are kept for provenance: which exact disclosed line
    item, in which filing, this number came from.

    period_start/period_end identify the underlying economic fact (one
    fiscal year's total); a later 10-K's comparative-year table can
    re-report the SAME period — capint.ingestion.sec_xbrl keeps only the
    earliest-filed disclosure of each distinct period, so this table never
    holds duplicate rows for the same (company, event_type, period).
    """

    __tablename__ = "capital_allocation_facts"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    xbrl_concept: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filing_form_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filing_accession: Mapped[str | None] = mapped_column(String(32), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
