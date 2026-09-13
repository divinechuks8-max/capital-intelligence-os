import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class ShortInterestSnapshot(Base):
    """One-to-one extension of an Event with event_type=SHORT_INTEREST_CHANGE:
    one company's FINRA-reported short position as of one settlement date
    (spec §24).

    Unlike Phase 9's net_assets_change_usd (which WE compute from two
    snapshots), `change_percent`/`change_quantity` here are reported
    DIRECTLY by FINRA alongside the position itself — a more trustworthy,
    source-of-truth figure than anything derived after the fact.

    Resolved by TICKER, not CIK/CUSIP — FINRA's short-interest data has no
    issuer CIK at all, only a ticker symbol. This is the first adapter in
    this system that must resolve entities by ticker alone, which spec §5
    explicitly warns against relying on solely: tickers get reassigned
    over time, and this phase does not check point-in-time ticker
    validity (EntityIdentifier.valid_from/valid_to exist in the schema but
    aren't consulted here) — a real, documented limitation, not an
    oversight. See capint.ingestion.finra_short_interest.
    """

    __tablename__ = "short_interest_snapshots"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    settlement_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    current_short_position: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    previous_short_position: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    change_percent: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    change_quantity: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    average_daily_volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    days_to_cover: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    exchange_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    market_class_code: Mapped[str | None] = mapped_column(String(16), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
