import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class PriceBar(UUIDPKMixin, CreatedAtMixin, Base):
    """One daily OHLCV bar for one company. Not an Event/Document-based
    disclosure model like every other observation in this system — a
    price bar isn't a filing or announcement, it's continuously
    observable market data with no single "publication" moment.
    Provenance is instead a plain `source_id` FK.

    `company_entity_id` resolves through the exact same ticker-based
    lookup capint.ingestion.finra_short_interest.get_or_create_company_by_ticker
    uses, so a short-interest signal and its price data land on the same
    Entity — required for capint.backtesting.engine to join them at all.

    **No ingestion path currently populates this table.** Phase 13
    originally built one against Alpha Vantage; Phase 15 removed it after
    discovering, on closer reading of Alpha Vantage's actual Terms of
    Service (not just its API docs), that the free tier is licensed for
    "personal, non-commercial use" only and excludes use "as or on behalf
    of a corporation... or any other association" and use "as part of any
    type of commercial activity that allows individuals or entities other
    than User to access information" — squarely what an ingest-and-serve
    platform like this one does, regardless of whether it charges anyone.
    A same-pattern check of three more vendors (Twelve Data, Finnhub,
    Polygon.io) found the identical individual-use-only / business-tier
    split on every one — a structural consequence of how US exchange
    market data is licensed for redistribution (UTP/CTA plans), not
    vendor-specific stinginess. No free, compliant daily-price source was
    found despite this search. This model/schema and
    capint.backtesting.engine remain valid and reusable the moment a
    compliant source is identified — only the ingestion path was removed.
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
