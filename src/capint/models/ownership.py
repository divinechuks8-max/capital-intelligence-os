import enum
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class ScheduleType(str, enum.Enum):
    SCHEDULE_13D = "SCHEDULE_13D"
    SCHEDULE_13G = "SCHEDULE_13G"


class BeneficialOwnershipDisclosure(Base):
    """One-to-one extension of an Event with event_type in
    {ACTIVIST_STAKE, MAJOR_HOLDER_CHANGE}, one row per (filing, reporting
    person) — mirrors InsiderTransaction's joint-filer handling (spec §11).

    `filer_entity_id` references entities.id directly (not people.entity_id)
    because a Schedule 13D/13G reporting person is often not an individual
    (trusts, holding companies, partnerships all file these) — forcing a
    Person profile onto a trust would misrepresent it. See
    capint.ingestion.sec_13dg for how filer_entity_id is resolved
    differently depending on whether the reporting person has a CIK and
    whether SEC's own type code marks it as an individual ("IN").

    `stated_purpose` (Schedule 13D's Item 4 narrative) is stored verbatim —
    it is what the filer wrote, an OBSERVATION, not this system's
    interpretation of activist intent (spec §2/§15). Always NULL for
    Schedule 13G, whose schema has no such item (passive holders don't
    state a purpose).
    """

    __tablename__ = "beneficial_ownership_disclosures"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    filer_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    schedule_type: Mapped[ScheduleType] = mapped_column(Enum(ScheduleType, name="schedule_type"), nullable=False)
    filer_type_code: Mapped[str | None] = mapped_column(String(8), nullable=True)

    shares_beneficially_owned: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    percent_of_class: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)
    sole_voting_power: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    shared_voting_power: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    sole_dispositive_power: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    shared_dispositive_power: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)

    event_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    stated_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_joint_filing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    filing_form_type: Mapped[str | None] = mapped_column(String(24), nullable=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
