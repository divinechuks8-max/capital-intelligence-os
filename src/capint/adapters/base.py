"""Source adapter abstraction (spec §61).

Analytics/scoring code must depend only on this interface (and the Event
model it produces), never on a specific provider's API shape — that keeps a
vendor swap or a new jurisdiction's disclosure regime from rippling into the
rest of the system.

No adapter in this increment performs a live fetch. Wiring up a real
provider means, at minimum, resolving its licensing terms (§62) and its
rate limits — neither has been done yet for any source, so implementing a
live call now would risk exactly the "fabricated data" failure mode the
spec prohibits (§84).
"""

from abc import ABC, abstractmethod
from datetime import datetime

from capint.models.event import Event


class SourceAdapter(ABC):
    """One instance per external data provider."""

    source_name: str

    @abstractmethod
    def fetch_events(self, since: datetime, until: datetime) -> list[Event]:
        """Return newly available Event rows (not yet persisted) published
        in [since, until]. Implementations own their own pagination, rate
        limiting, and retry policy."""
        raise NotImplementedError
