"""Source adapter abstraction (spec §61).

Analytics/scoring code must depend only on this interface, never on a
specific provider's API shape — that keeps a vendor swap or a new
jurisdiction's disclosure regime from rippling into the rest of the system.

Phase 1 originally specified `fetch_events(...) -> list[Event]`. Building
the first real adapter (Phase 2, SEC EDGAR Form 4) showed that constraint
was wrong: constructing an `Event` row requires a resolved `primary_entity_id`
and `source_id`, both of which only exist after DB-backed entity resolution
(get-or-create by CIK, etc.) — work an adapter has no business doing, since
it has no DB session and shouldn't need one to stay swappable/testable.

So the adapter boundary now sits one layer lower: adapters fetch and parse,
returning plain records; a separate ingestion module (capint.ingestion)
does entity resolution and persistence against those records. See
capint/ingestion/sec_form4.py for the reference split.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import Any


class SourceAdapter(ABC):
    """One instance per external data provider."""

    source_name: str

    @abstractmethod
    def fetch_records(self, since: datetime, until: datetime) -> Iterable[dict[str, Any]]:
        """Yield normalized raw records published in [since, until].

        Records are plain dicts (not ORM rows) so adapters stay decoupled
        from the database — turning a record into Entity/Event/... rows is
        the ingestion pipeline's job. Implementations own their own
        pagination, rate limiting, and retry policy.
        """
        raise NotImplementedError
