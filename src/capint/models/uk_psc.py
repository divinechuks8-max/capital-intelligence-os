import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class UKPersonWithSignificantControl(Base):
    """One-to-one extension of an Event with event_type=MAJOR_HOLDER_CHANGE:
    one (company, PSC) beneficial-ownership disclosure from Companies
    House's Persons with Significant Control (PSC) register (spec's UK
    jurisdiction extension) — the closest UK equivalent of Schedule
    13D/13G, but a structurally different regime, hence a dedicated model
    rather than reusing capint.models.ownership.BeneficialOwnershipDisclosure:

    - `natures_of_control` is Companies House's own fixed vocabulary of
      categorical percentage/voting-rights bands (e.g.
      "ownership-of-shares-25-to-50-percent"), stored verbatim as a JSON
      list of their raw strings rather than mapped into our own enum —
      the vocabulary is large (~30 values, including trust/firm variants)
      and Companies House's, not ours, to define; forcing it into a
      closed enum risks silently rejecting a legitimate value we didn't
      anticipate. Contrast with Schedule 13D/13G's exact numeric
      `percent_of_class` — PSC never discloses an exact percentage.
    - `psc_kind` is similarly stored verbatim (e.g.
      "individual-person-with-significant-control",
      "corporate-entity-person-with-significant-control",
      "legal-person-with-significant-control") rather than a closed enum,
      same reasoning.
    - No `stated_purpose` equivalent — the PSC regime has no Item 4-style
      activist-intent narrative; it is a pure ownership/control disclosure.

    **Real, load-bearing limitation confirmed live before building this**:
    companies whose voting shares trade on a "regulated market" (LSE Main
    Market — e.g. Diageo, Barclays, GSK) are exempt from the PSC regime
    entirely (Companies Act 2006, Sch 1A) — their major-holder disclosures
    happen instead via FCA DTR5/RNS, a source this system does not ingest
    (see capint.adapters.companies_house's module docstring for why).
    PSC data is therefore only populated here for UK companies NOT on a
    regulated market — in practice, AIM-listed and smaller companies.
    Confirmed live: Diageo plc (LSE Main Market) returns zero PSC records;
    Frontier Developments plc and Angling Direct plc (both AIM) return
    real ones.
    """

    __tablename__ = "uk_persons_with_significant_control"

    event_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    psc_entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("entities.id"), nullable=True, index=True)
    psc_name: Mapped[str] = mapped_column(String(512), nullable=False)
    psc_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    natures_of_control: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    notified_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ceased_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    country_of_residence: Mapped[str | None] = mapped_column(String(128), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(128), nullable=True)
    companies_house_psc_link: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    event: Mapped["Event"] = relationship()  # noqa: F821
