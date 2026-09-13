import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class EventType(str, enum.Enum):
    """Roster from spec §7. Only INSIDER_PURCHASE/INSIDER_SALE have a subtype
    table (InsiderTransaction) so far; the rest are declared now so the event
    stream has a stable vocabulary as later phases add their own subtypes."""

    INSIDER_PURCHASE = "INSIDER_PURCHASE"
    INSIDER_SALE = "INSIDER_SALE"
    INSTITUTIONAL_POSITION_CHANGE = "INSTITUTIONAL_POSITION_CHANGE"
    ACTIVIST_STAKE = "ACTIVIST_STAKE"
    MAJOR_HOLDER_CHANGE = "MAJOR_HOLDER_CHANGE"
    ETF_FLOW = "ETF_FLOW"
    SHARE_BUYBACK = "SHARE_BUYBACK"
    SECONDARY_OFFERING = "SECONDARY_OFFERING"
    INSIDER_COMPENSATION = "INSIDER_COMPENSATION"
    EARNINGS = "EARNINGS"
    GUIDANCE_CHANGE = "GUIDANCE_CHANGE"
    ESTIMATE_REVISION = "ESTIMATE_REVISION"
    ANALYST_RATING_CHANGE = "ANALYST_RATING_CHANGE"
    M_AND_A = "M_AND_A"
    SPINOFF = "SPINOFF"
    DEBT_ISSUANCE = "DEBT_ISSUANCE"
    DEBT_REPAYMENT = "DEBT_REPAYMENT"
    DIVIDEND_PAYMENT = "DIVIDEND_PAYMENT"
    CAPITAL_RAISE = "CAPITAL_RAISE"
    CONTRACT_AWARD = "CONTRACT_AWARD"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    REGULATORY_EVENT = "REGULATORY_EVENT"
    CLINICAL_TRIAL_EVENT = "CLINICAL_TRIAL_EVENT"
    PATENT_EVENT = "PATENT_EVENT"
    CRYPTO_TRANSFER = "CRYPTO_TRANSFER"
    WHALE_ACCUMULATION = "WHALE_ACCUMULATION"
    EXCHANGE_FLOW = "EXCHANGE_FLOW"
    TOKEN_UNLOCK = "TOKEN_UNLOCK"
    TREASURY_MOVEMENT = "TREASURY_MOVEMENT"
    SHORT_INTEREST_CHANGE = "SHORT_INTEREST_CHANGE"


class Event(UUIDPKMixin, CreatedAtMixin, Base):
    """Base table for every observation in the system (spec §7/§8).

    Four distinct timestamps are tracked deliberately:
    - event_time: when the underlying thing actually happened.
    - announcement_time: when it was first announced/disclosed, if distinct.
    - publication_time: when the source document became publicly available.
    - effective_time: when it takes legal/economic effect, if applicable.
    - retrieval_time (inherited as created_at semantics below) is *our*
      ingestion time, tracked on Document, since the same event can be
      re-ingested later without changing when the public first saw it.

    A point-in-time query MUST filter on publication_time (or retrieval_time
    when simulating our own ingestion latency), never on event_time —
    otherwise a fact only disclosed later leaks into an earlier simulated
    "as of" view. See capint.temporal and tests/test_temporal_correctness.py.
    """

    __tablename__ = "events"
    __table_args__ = (CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_confidence_range"),)

    event_type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"), nullable=False, index=True)
    primary_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)

    event_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    announcement_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    effective_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # Doubles as an adapter-defined idempotency key (e.g. "sec-form4:<accession>#<index>")
    # so re-running ingestion never double-counts the same source transaction.
    raw_data_reference: Mapped[str | None] = mapped_column(String(512), unique=True, nullable=True)

    source: Mapped["Source"] = relationship()  # noqa: F821
    document: Mapped["Document | None"] = relationship()  # noqa: F821

    @property
    def retrieval_time(self) -> datetime | None:
        """When *our* system first ingested this event — read off the backing
        Document if one is attached, else falls back to our own created_at."""
        if self.document is not None:
            return self.document.retrieved_at
        return self.created_at
