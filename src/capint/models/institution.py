import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class InstitutionalManagerType(str, enum.Enum):
    """Roster from spec §14. Deliberately does NOT default to a "smart
    money" label of any kind — see OTHER below. 13F data alone (a holdings
    list) has no reliable signal for which of these a filer is; that needs
    additional curation this phase doesn't attempt (see
    capint.ingestion.sec_13f module docstring)."""

    LONG_TERM_ASSET_MANAGER = "LONG_TERM_ASSET_MANAGER"
    INDEX_MANAGER = "INDEX_MANAGER"
    HEDGE_FUND = "HEDGE_FUND"
    ACTIVIST = "ACTIVIST"
    PENSION = "PENSION"
    SOVEREIGN_WEALTH_FUND = "SOVEREIGN_WEALTH_FUND"
    FAMILY_OFFICE = "FAMILY_OFFICE"
    QUANTITATIVE_FUND = "QUANTITATIVE_FUND"
    VENTURE_FUND = "VENTURE_FUND"
    PRIVATE_EQUITY = "PRIVATE_EQUITY"
    STRATEGIC_CORPORATE_INVESTOR = "STRATEGIC_CORPORATE_INVESTOR"
    OTHER = "OTHER"


class InstitutionalManager(Base):
    """Profile attributes for an Entity of type INSTITUTION — a 13F filer.
    One-to-one extension of Entity, mirroring Company/Person."""

    __tablename__ = "institutional_managers"

    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    manager_type: Mapped[InstitutionalManagerType] = mapped_column(
        Enum(InstitutionalManagerType, name="institutional_manager_type"),
        nullable=False,
        default=InstitutionalManagerType.OTHER,
    )
    form13f_file_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    entity: Mapped["Entity"] = relationship()  # noqa: F821


class InstitutionalPositionStatus(str, enum.Enum):
    """Spec §13: "new position, increase, decrease, exit". Computed by
    comparing this filing's per-CUSIP holding to the same institution's
    immediately preceding period_of_report — not asserted by the filing
    itself, but deterministically derivable from it (an OBSERVATION, not an
    INTERPRETATION of intent — spec §2)."""

    NEW = "NEW"
    INCREASED = "INCREASED"
    DECREASED = "DECREASED"
    UNCHANGED = "UNCHANGED"
    EXITED = "EXITED"


class InstitutionalHolding(Base):
    """One-to-one extension of an Event with event_type =
    INSTITUTIONAL_POSITION_CHANGE: one institution's disclosed position in
    one company, as of one 13F period_of_report.

    event_time on the parent Event is period_of_report (what the position
    IS as of); publication_time is when the filing was actually accepted by
    EDGAR — routinely 30-45 days later. Never conflate the two: spec §13
    is explicit that reporting lag must always be shown, not silently
    treated as "current".
    """

    __tablename__ = "institutional_holdings"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    institution_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("institutional_managers.entity_id"), nullable=False, index=True
    )
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    period_of_report: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    shares_held: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    market_value_usd: Mapped[Decimal] = mapped_column(Numeric(20, 2), nullable=False)
    shares_change: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    position_status: Mapped[InstitutionalPositionStatus] = mapped_column(
        Enum(InstitutionalPositionStatus, name="institutional_position_status"), nullable=False
    )
    filing_form_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
