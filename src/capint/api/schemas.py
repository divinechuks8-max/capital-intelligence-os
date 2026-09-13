"""API DTOs. Kept separate from ORM models so the wire format can evolve
independently of the storage schema."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from capint.models.entity import EntityType
from capint.models.event import EventType


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entity_id: UUID
    canonical_name: str
    entity_type: EntityType
    sector: str | None = None
    industry: str | None = None
    country: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: EventType
    primary_entity_id: UUID
    event_time: datetime | None
    publication_time: datetime
    confidence: float
    source_id: UUID
