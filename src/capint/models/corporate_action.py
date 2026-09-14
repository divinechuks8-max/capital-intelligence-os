import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class CorporateActionDisclosure(Base):
    """One-to-one extension of an Event with event_type=M_AND_A: one 8-K
    filing tagged with Item 2.01 (Phase 14).

    Same shape and reasoning as capint.models.guidance.GuidanceDisclosure
    (Phase 12) — a dedicated model rather than reusing that one, since
    "a completed acquisition/disposition was disclosed" is a genuinely
    different disclosure category from "a guidance-relevant filing was
    made," even though the underlying mechanism (SEC's own per-filing
    Item codes) is identical.

    **A real, deliberate scope limitation, not a partial attempt at deal
    extraction**: this stores WHICH 8-K was filed under Item 2.01 and a
    link to it — it does NOT fetch or parse the actual filing exhibit to
    extract deal terms, consideration, or counterparty identity. Item
    2.01 ("Completion of Acquisition or Disposition of Assets") was
    chosen deliberately over the broader Item 1.01 ("Entry into a
    Material Definitive Agreement", which covers ordinary commercial
    contracts far more often than signed merger agreements) — trading
    recall for real specificity. Confirmed live before building this:
    Microsoft's real 2023-10-13 8-K tagged with Item 2.01 alone is its
    Activision Blizzard acquisition completion.

    No distinct spinoff detection either (EventType also declares
    SPINOFF) — SEC's 8-K item taxonomy has no item number specific to
    spinoffs (they're typically also filed under Item 2.01, or disclosed
    via a separate Form 10 registration for the spun-off entity, a
    completely different filing this system doesn't ingest), so every
    Item 2.01 disclosure here is tagged M_AND_A regardless of whether the
    underlying transaction was an acquisition or a divestiture/spinoff —
    a human must read the filing to tell which.
    """

    __tablename__ = "corporate_action_disclosures"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    item_codes: Mapped[str] = mapped_column(String(64), nullable=False)
    filing_form_type: Mapped[str] = mapped_column(String(16), nullable=False, default="8-K")
    filing_accession: Mapped[str] = mapped_column(String(32), nullable=False)
    primary_document_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
