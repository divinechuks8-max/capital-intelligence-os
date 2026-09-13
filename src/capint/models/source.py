import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from capint.models.base import Base, CreatedAtMixin, UUIDPKMixin


class SourceTier(str, enum.Enum):
    """Source quality tiers (spec §9). Lower-tier sources must never silently
    override higher-tier facts — conflict resolution is a future engine
    concern, but the tier has to be recorded from day one to make that
    possible at all."""

    A_OFFICIAL = "A_OFFICIAL"  # regulatory filings, exchange data, on-chain data
    B_LICENSED = "B_LICENSED"  # licensed financial data providers
    C_PUBLICATION = "C_PUBLICATION"  # established financial publications
    D_UNVERIFIED = "D_UNVERIFIED"  # social media, forums, unverified


class Source(UUIDPKMixin, CreatedAtMixin, Base):
    """A provider of data (e.g. 'SEC EDGAR', 'Company IR Site', 'Bitcoin
    blockchain'). Distinct from Document, which is one retrieved artifact
    from that provider."""

    __tablename__ = "sources"

    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    tier: Mapped[SourceTier] = mapped_column(Enum(SourceTier, name="source_tier"), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    license_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    documents: Mapped[list["Document"]] = relationship(back_populates="source")


class Document(UUIDPKMixin, CreatedAtMixin, Base):
    """One retrieved artifact backing an observation — e.g. a specific SEC
    filing accession, a company press release, a block explorer page.

    If redistribution rights are unclear, do not store raw content here —
    store enough to re-fetch and to prove provenance (url, hash, retrieval
    timestamp) and fetch the source on demand instead (spec §10, §62).
    """

    __tablename__ = "documents"

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_storage_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source: Mapped["Source"] = relationship(back_populates="documents")
