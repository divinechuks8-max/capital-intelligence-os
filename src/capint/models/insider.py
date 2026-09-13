import enum
import uuid
from decimal import Decimal

from sqlalchemy import Boolean, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class InsiderTransactionType(str, enum.Enum):
    """Deliberately not just BUY/SELL — spec §11 requires distinguishing the
    economic character of the transaction, since e.g. an option exercise or
    tax-withholding sale carries very different signal than a discretionary
    open-market purchase."""

    OPEN_MARKET_PURCHASE = "OPEN_MARKET_PURCHASE"
    OPEN_MARKET_SALE = "OPEN_MARKET_SALE"
    OPTION_EXERCISE = "OPTION_EXERCISE"
    TAX_WITHHOLDING = "TAX_WITHHOLDING"
    GIFT = "GIFT"
    COMPENSATION_AWARD = "COMPENSATION_AWARD"
    AUTOMATIC_10B5_1 = "AUTOMATIC_10B5_1"
    CONVERSION = "CONVERSION"
    TRANSFER = "TRANSFER"
    OTHER = "OTHER"


class InsiderTransaction(Base):
    """One-to-one extension of an Event with event_type in
    {INSIDER_PURCHASE, INSIDER_SALE}, carrying the Form 3/4/5-specific
    detail needed for insider-conviction scoring (a later phase)."""

    __tablename__ = "insider_transactions"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    insider_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.entity_id"), nullable=False, index=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    transaction_type: Mapped[InsiderTransactionType] = mapped_column(
        Enum(InsiderTransactionType, name="insider_transaction_type"), nullable=False
    )
    shares_transacted: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    price_per_share: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    shares_owned_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    is_10b5_1_plan: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    filing_form_type: Mapped[str | None] = mapped_column(String(16), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
