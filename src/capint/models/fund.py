import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class Fund(Base):
    """Profile attributes for an Entity of type ETF or FUND — a registered
    investment company that files Form N-PORT. One-to-one extension of
    Entity, mirroring Company/Person/InstitutionalManager.

    Deliberately doesn't try to distinguish an exchange-traded fund from a
    traditional open-end mutual fund beyond whatever `ticker` is present —
    N-PORT itself doesn't cleanly flag that distinction, and guessing from
    other signals wasn't worth the complexity for this phase.
    """

    __tablename__ = "funds"

    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(16), nullable=True)
    series_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    entity: Mapped["Entity"] = relationship()  # noqa: F821


class FundAumSnapshot(Base):
    """One-to-one extension of an Event with event_type=ETF_FLOW: one
    fund's total/net assets as of one N-PORT reporting period (spec §17).

    Named deliberately around "AUM", not "flow": a fund's net assets
    change for two reasons — net creation/redemption (true flow) AND
    market price movement of its holdings — and N-PORT does not expose
    the pieces needed to cleanly separate them (no shares-outstanding-by-
    class figure was present even for SPY, one of the largest and most
    liquid ETFs in the world, when this was built — confirmed by fetching
    its real filing before designing this model). `net_assets_change_usd`
    is the plain quarter-over-quarter dollar delta, useful context but NOT
    an isolated flow figure — spec §17's "1-day/5-day/20-day/60-day flow"
    granularity is also not attempted; N-PORT is quarterly with a ~60-day
    disclosure lag, a hard ceiling on granularity regardless.
    """

    __tablename__ = "fund_aum_snapshots"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    fund_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("funds.entity_id"), nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_assets_usd: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    total_liabilities_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    net_assets_usd: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    net_assets_change_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    filing_form_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    filing_accession: Mapped[str | None] = mapped_column(String(32), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
