import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class VolatilityIndexLevel(UUIDPKMixin, CreatedAtMixin, Base):
    """One daily OHLC level for one Cboe volatility index (Phase 14,
    options/derivatives extension) — e.g. VIX (the "fear index",
    calculated from S&P 500 index option prices), VVIX (volatility of
    VIX itself), SKEW (tail-risk priced into S&P 500 options).

    Not tied to any Company/Entity — deliberately. A volatility index is
    a market-wide signal, not specific to any single tracked issuer (the
    handful of Cboe single-stock exceptions, like VXAPL for Apple, are
    not ingested here), so there is no natural Entity in this system's
    graph for it to attach to, and inventing one would add an entity-graph
    concept (identifiers, relationships) this data doesn't actually need.

    **Why this was chosen over "unusual options activity" or a real
    put/call ratio**: per-security options activity and put/call ratio
    data remain genuinely commercial/gated (confirmed live during
    research — Cboe's own market-statistics pages show sign-in/
    subscription indicators, and granular options data is explicitly
    sold via Cboe DataShop). Full daily history of Cboe's own published
    volatility INDICES, by contrast, is confirmed live to be genuinely
    free and publicly intended for reuse — Cboe's own site
    (cboe.com/tradable-products/vix/vix-historical-data) describes this
    exact data as "Updated Daily" for public download, distinct from the
    paid DataShop product. This is real options-derived market data, not
    a substitute pretending to be something else.

    No scoring/signal layer here yet (e.g. a percentile-based "volatility
    spike" score) — this phase ingests only; see other phases'
    ingest-now/score-later precedent (Phases 2->3, 4->5, 10->13).
    """

    __tablename__ = "volatility_index_levels"
    __table_args__ = (UniqueConstraint("index_code", "trade_date", name="uq_volatility_index_level"),)

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    index_code: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    source: Mapped["Source"] = relationship()  # noqa: F821
