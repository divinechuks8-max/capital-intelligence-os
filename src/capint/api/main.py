from datetime import datetime
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from capint import temporal
from capint.api.schemas import CompanyOut, EventOut
from capint.db import get_session
from capint.models.company import Company
from capint.models.entity import Entity
from capint.models.event import EventType

app = FastAPI(title="Capital Intelligence OS", version="0.1.0")


@app.get("/api/v1/companies", response_model=list[CompanyOut])
def list_companies(session: Session = Depends(get_session)) -> list[CompanyOut]:
    rows = session.execute(select(Company, Entity).join(Entity, Company.entity_id == Entity.id)).all()
    return [
        CompanyOut(
            entity_id=company.entity_id,
            canonical_name=entity.canonical_name,
            entity_type=entity.entity_type,
            sector=company.sector,
            industry=company.industry,
            country=company.country,
        )
        for company, entity in rows
    ]


@app.get("/api/v1/companies/{entity_id}", response_model=CompanyOut)
def get_company(entity_id: UUID, session: Session = Depends(get_session)) -> CompanyOut:
    row = session.execute(
        select(Company, Entity).join(Entity, Company.entity_id == Entity.id).where(Company.entity_id == entity_id)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")
    company, entity = row
    return CompanyOut(
        entity_id=company.entity_id,
        canonical_name=entity.canonical_name,
        entity_type=entity.entity_type,
        sector=company.sector,
        industry=company.industry,
        country=company.country,
    )


@app.get("/api/v1/events", response_model=list[EventOut])
def list_events(
    entity_id: UUID | None = None,
    event_type: EventType | None = None,
    as_of: datetime | None = Query(default=None, description="Point-in-time cutoff. Defaults to now."),
    mode: str = Query(default="public", pattern="^(public|ingested)$"),
    session: Session = Depends(get_session),
) -> list[EventOut]:
    """Point-in-time event listing.

    mode=public  -> only events publicly disclosed by `as_of` (Event.publication_time).
    mode=ingested -> only events our own pipeline had recorded by `as_of` (Document.retrieved_at).

    This is the enforcement point for spec §8/§72: there is no way to ask
    this endpoint for "all events" without an as_of boundary — as_of simply
    defaults to now, which is the least surprising default and still safe.
    """
    cutoff = as_of or datetime.now(tz=None).astimezone()
    event_types = [event_type] if event_type else None
    query_fn = temporal.as_of_public if mode == "public" else temporal.as_of_ingested
    events = query_fn(session, cutoff, entity_id=entity_id, event_types=event_types)
    return [EventOut.model_validate(e) for e in events]


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
