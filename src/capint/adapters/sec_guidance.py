"""SEC 8-K guidance-relevant disclosure adapter (Phase 12).

Confirmed live before building this: `data.sec.gov/submissions` (already
used by capint.adapters.sec_nport) exposes each recent filing's Item
number(s) directly as a parallel `items` array — e.g. "2.02,9.01" for a
typical earnings 8-K. No full-text search or document parsing is needed
to find guidance-relevant filings.

**A real, deliberate scope limitation, not a partial attempt at guidance
extraction**: this adapter flags 8-Ks tagged with Item 2.02 ("Results of
Operations and Financial Condition" — the vehicle for routine quarterly
earnings releases, only sometimes containing new guidance) or Item 7.01
("Regulation FD Disclosure" — the more common vehicle for a standalone
guidance-only announcement). Both are included as "guidance-relevant
disclosure events." It does NOT fetch or parse the actual press-release
exhibit to determine whether guidance was raised, lowered, reaffirmed, or
extract any numeric range — see capint.models.guidance.GuidanceDisclosure's
docstring for why that is an honest OBSERVATION-only scope, not this
system's interpretation of the filing's content.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter
from capint.adapters.sec_common import RateLimitedSecClient, SUBMISSIONS_URL, parse_cik

FORM_TYPE = "8-K"
GUIDANCE_RELEVANT_ITEMS = {"2.02", "7.01"}


@dataclass(frozen=True)
class RawGuidanceDisclosure:
    cik: str
    company_name: str
    item_codes: str  # raw, e.g. "2.02,9.01" — verbatim from SEC, not interpreted
    accession_number: str
    filing_date: date
    filed_at: datetime  # SEC acceptance timestamp
    primary_document_url: str | None


class SECGuidanceDisclosureAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(self, user_agent: str, client: httpx.Client | None = None, min_request_interval: float = 0.2) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_recent_guidance_disclosures(self, cik: str, limit: int = 20) -> list[RawGuidanceDisclosure]:
        """Exact form type "8-K" only — amendments ("8-K/A") excluded,
        same as every other adapter here excludes its own form's
        amendments."""
        cik_padded = parse_cik(cik)
        url = SUBMISSIONS_URL.format(cik=cik_padded)
        try:
            data = self._http.get(url).json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

        company_name = data.get("name", "UNKNOWN")
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        items_list = recent.get("items", [])
        accessions = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        acceptance_times = recent.get("acceptanceDateTime", [])
        primary_documents = recent.get("primaryDocument", [])

        cik_for_path = cik_padded.lstrip("0") or "0"
        disclosures: list[RawGuidanceDisclosure] = []
        for i, form in enumerate(forms):
            if form != FORM_TYPE:
                continue
            item_codes = items_list[i] if i < len(items_list) else ""
            codes = {c.strip() for c in item_codes.split(",") if c.strip()}
            if not codes & GUIDANCE_RELEVANT_ITEMS:
                continue

            accession = accessions[i]
            primary_document = primary_documents[i] if i < len(primary_documents) else None
            accession_nodash = accession.replace("-", "")
            primary_document_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik_for_path}/{accession_nodash}/{primary_document}"
                if primary_document
                else None
            )

            disclosures.append(
                RawGuidanceDisclosure(
                    cik=cik_padded,
                    company_name=company_name,
                    item_codes=item_codes,
                    accession_number=accession,
                    filing_date=date.fromisoformat(filing_dates[i]),
                    filed_at=datetime.fromisoformat(acceptance_times[i].replace("Z", "+00:00")),
                    primary_document_url=primary_document_url,
                )
            )
            if len(disclosures) >= limit:
                break
        return disclosures

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.sec_nport.SECNPortAdapter.fetch_records for why
        the typed method above is preferred for real ingestion."""
        raise NotImplementedError(
            "SECGuidanceDisclosureAdapter is per-company; use fetch_recent_guidance_disclosures "
            "via capint.ingestion.sec_guidance instead."
        )
