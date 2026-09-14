import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class PriceBar(UUIDPKMixin, CreatedAtMixin, Base):
    """One daily OHLCV bar for one company (Phase 13's backtesting
    extension). Not an Event/Document-based disclosure model like every
    other observation in this system — a price bar isn't a filing or
    announcement, it's continuously observable market data with no single
    "publication" moment. Provenance is instead a plain `source_id` FK,
    with a real, load-bearing limitation: the free tier of this system's
    only price source (Alpha Vantage) exposes just the trailing ~100
    trading days (`outputsize=compact`) — full multi-year history is a
    paid feature, confirmed live before building this. See
    capint.adapters.alpha_vantage's module docstring.

    `company_entity_id` resolves through the exact same ticker-based
    lookup capint.ingestion.finra_short_interest.get_or_create_company_by_ticker
    uses, so a short-interest signal and its price data land on the same
    Entity — required for capint.backtesting.engine to join them at all.
    """

    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("company_entity_id", "trade_date", name="uq_price_bar_company_date"),)

    company_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)

    source: Mapped["Source"] = relationship()  # noqa: F821
