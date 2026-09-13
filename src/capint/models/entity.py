import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class EntityType(str, enum.Enum):
    """The full roster from the platform spec. Not every type has a profile
    table yet (Company/Person do); others are represented at the Entity level
    only until their own domain model is built."""

    COMPANY = "COMPANY"
    PERSON = "PERSON"
    INSTITUTION = "INSTITUTION"
    FUND = "FUND"
    ETF = "ETF"
    TRUST = "TRUST"
    GOVERNMENT_ENTITY = "GOVERNMENT_ENTITY"
    ACTIVIST = "ACTIVIST"
    CRYPTO_WALLET = "CRYPTO_WALLET"
    CRYPTO_PROTOCOL = "CRYPTO_PROTOCOL"
    EXCHANGE = "EXCHANGE"
    MARKET_MAKER = "MARKET_MAKER"
    FOUNDATION = "FOUNDATION"
    SUPPLIER = "SUPPLIER"
    CUSTOMER = "CUSTOMER"
    COMPETITOR = "COMPETITOR"
    OTHER = "OTHER"


class IdentifierType(str, enum.Enum):
    TICKER = "TICKER"
    CUSIP = "CUSIP"
    ISIN = "ISIN"
    CIK = "CIK"
    LEI = "LEI"
    EXCHANGE_CODE = "EXCHANGE_CODE"
    WALLET_ADDRESS = "WALLET_ADDRESS"
    ALIAS = "ALIAS"
    FUND_ID = "FUND_ID"


class Entity(UUIDPKMixin, CreatedAtMixin, Base):
    """Canonical, internally-stable representation of any tracked actor.

    Never join across sources on ticker/name alone — resolve through
    EntityIdentifier instead, since tickers are reused and reassigned and
    names collide across issuers and jurisdictions.
    """

    __tablename__ = "entities"

    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, name="entity_type"), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    identifiers: Mapped[list["EntityIdentifier"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Entity {self.entity_type.value} {self.canonical_name!r}>"


class EntityIdentifier(UUIDPKMixin, CreatedAtMixin, Base):
    """A mapping from an external identifier to a canonical Entity.

    Identifiers carry a validity window because they are reassigned over
    time (tickers especially) — a lookup must be point-in-time aware, not
    just a static dictionary.
    """

    __tablename__ = "entity_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "identifier_type", "identifier_value", "exchange", "valid_from", name="uq_identifier_scope"
        ),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    identifier_type: Mapped[IdentifierType] = mapped_column(
        Enum(IdentifierType, name="identifier_type"), nullable=False, index=True
    )
    identifier_value: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_primary: Mapped[bool] = mapped_column(default=False, nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sources.id"), nullable=True)

    entity: Mapped["Entity"] = relationship(back_populates="identifiers")

    def is_valid_on(self, as_of: date) -> bool:
        if self.valid_from and as_of < self.valid_from:
            return False
        if self.valid_to and as_of > self.valid_to:
            return False
        return True
