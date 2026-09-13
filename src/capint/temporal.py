"""Point-in-time query helpers.

The single most important correctness property in this system: a historical
"as of" query must only ever see what was actually knowable at that moment.

Two distinct "as of" semantics are supported, and callers must pick one
deliberately:

- as_of_public(session, as_of, ...): what was publicly disclosed by as_of
  (filters on Event.publication_time). Use this to simulate "what the market
  knew".
- as_of_ingested(session, as_of, ...): what *our system* had ingested by
  as_of (filters on Document.retrieved_at, falling back to Event.created_at
  for events with no backing document). Use this to simulate "what our
  platform could have alerted on" — it can lag publication_time due to our
  own pipeline latency, and it can never be earlier than publication_time
  for a well-behaved ingestion pipeline.

Never filter on event_time alone for a historical simulation — event_time is
when the thing happened, not when it became knowable, and using it directly
is exactly the look-ahead-bias bug this module exists to prevent.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from capint.models.event import Event, EventType
from capint.models.source import Document


def _base_query(entity_id: UUID | None, event_types: Sequence[EventType] | None):
    stmt = select(Event)
    if entity_id is not None:
        stmt = stmt.where(Event.primary_entity_id == entity_id)
    if event_types:
        stmt = stmt.where(Event.event_type.in_(event_types))
    return stmt


def as_of_public(
    session: Session,
    as_of: datetime,
    entity_id: UUID | None = None,
    event_types: Sequence[EventType] | None = None,
) -> list[Event]:
    """Events whose publication_time was on or before `as_of`."""
    stmt = _base_query(entity_id, event_types).where(Event.publication_time <= as_of)
    return list(session.execute(stmt.order_by(Event.publication_time)).scalars())


def as_of_ingested(
    session: Session,
    as_of: datetime,
    entity_id: UUID | None = None,
    event_types: Sequence[EventType] | None = None,
) -> list[Event]:
    """Events our system had actually ingested by `as_of`.

    An event backed by a Document is gated on that document's retrieved_at;
    an event with no Document (rare — should mean it was entered directly,
    e.g. a manually curated observation) is gated on its own created_at.
    """
    gate = or_(
        Document.retrieved_at.is_not(None) & (Document.retrieved_at <= as_of),
        Document.retrieved_at.is_(None) & (Event.created_at <= as_of),
    )
    stmt = _base_query(entity_id, event_types).outerjoin(Document, Event.document_id == Document.id).where(gate)
    order_col = case((Document.retrieved_at.is_not(None), Document.retrieved_at), else_=Event.created_at)
    return list(session.execute(stmt.order_by(order_col)).scalars())
