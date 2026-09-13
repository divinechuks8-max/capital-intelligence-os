import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class Person(Base):
    """Profile attributes for an Entity of type PERSON. One-to-one extension
    of Entity, mirroring Company."""

    __tablename__ = "people"

    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), primary_key=True)

    entity: Mapped["Entity"] = relationship()  # noqa: F821
    roles: Mapped[list["PersonCompanyRole"]] = relationship(back_populates="person")


class PersonCompanyRole(UUIDPKMixin, CreatedAtMixin, Base):
    """A person's role at a company over a time window — needed to classify
    insider transactions (officer/director/10% owner, per Form 3/4/5) and to
    build the insider network graph later (spec §11, §45)."""

    __tablename__ = "person_company_roles"

    person_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("people.entity_id"), nullable=False, index=True)
    company_entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("companies.entity_id"), nullable=False, index=True
    )
    role_title: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_officer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_director: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_ten_percent_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    person: Mapped["Person"] = relationship(back_populates="roles")
