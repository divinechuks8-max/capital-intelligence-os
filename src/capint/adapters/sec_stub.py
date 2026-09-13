"""Placeholder for the Phase 2 SEC EDGAR adapter (Form 3/4/5 ingestion).

Deliberately unimplemented: see capint.adapters.base module docstring for why.
This stub exists only to fix the shape of what Phase 2 will build, and to
give the rest of the codebase (e.g. tests) a concrete class to reference.
"""

from datetime import datetime

from capint.adapters.base import SourceAdapter
from capint.models.event import Event


class SECEdgarAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def fetch_events(self, since: datetime, until: datetime) -> list[Event]:
        raise NotImplementedError(
            "Phase 2: parse SEC Form 3/4/5 filings from EDGAR full-text/XBRL feeds. "
            "Requires: confirming EDGAR access terms, a compliant User-Agent per "
            "SEC's fair-access policy, and a Form 4 XML parser mapping to "
            "InsiderTransaction."
        )
