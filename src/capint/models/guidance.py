import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class GuidanceDisclosure(Base):
    """One-to-one extension of an Event with event_type=GUIDANCE_CHANGE:
    one 8-K filing tagged with an item number under which companies
    typically disclose results and/or forward guidance (Phase 12).

    **A real, deliberate scope limitation, not a partial attempt at
    guidance extraction**: this stores WHICH 8-K item(s) were reported
    (`item_codes`, verbatim from SEC's own data, e.g. "2.02,9.01") and a
    link to the filing — it does NOT parse the actual press release
    exhibit text to determine whether guidance was raised, lowered, or
    reaffirmed, or extract any numeric guidance range. Item 2.02 ("Results
    of Operations and Financial Condition") is used for routine quarterly
    earnings releases far more often than for a standalone guidance
    update; Item 7.01 ("Regulation FD Disclosure") is the more common
    vehicle for a guidance-only announcement outside the earnings cycle.
    Both are included here as "guidance-relevant disclosure events" — an
    OBSERVATION that something in this category was disclosed, not an
    INTERPRETATION of its content or direction (spec's observation/
    interpretation separation). A human (or a future NLP-based phase)
    must read the actual filing to know what it says.
    """

    __tablename__ = "guidance_disclosures"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    item_codes: Mapped[str] = mapped_column(String(64), nullable=False)
    filing_form_type: Mapped[str] = mapped_column(String(16), nullable=False, default="8-K")
    filing_accession: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_document_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
