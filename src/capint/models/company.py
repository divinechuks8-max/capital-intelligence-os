import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base


class Company(Base):
    """Profile attributes for an Entity of type COMPANY.

    One-to-one extension of Entity via a shared primary key, rather than a
    subclass table with its own identity — the Entity row is what everything
    else (events, identifiers, relationships) references."""

    __tablename__ = "companies"

    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), primary_key=True)
    sector: Mapped[str | None] = mapped_column(String(128), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)

    entity: Mapped["Entity"] = relationship()  # noqa: F821
