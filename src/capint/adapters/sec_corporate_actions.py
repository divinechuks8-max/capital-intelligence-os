"""SEC 8-K corporate-action disclosure adapter (Phase 14, M&A extension).

Same shape as capint.adapters.sec_guidance (Phase 12): `data.sec.gov/
submissions` exposes each recent filing's Item number(s) directly, so no
full-text search or document parsing is needed to find M&A-relevant
filings. Confirmed live before building this: Microsoft's real filing
history includes a real 8-K filed 2023-10-13 tagged with Item 2.01 alone
— its Activision Blizzard acquisition completion.

**Deliberately narrow, and an honest observation-only scope, not a
partial attempt at deal-term extraction**: this only flags 8-Ks tagged
with Item 2.01 ("Completion of Acquisition or Disposition of Assets") —
the item SEC's own instructions describe as covering completed M&A
transactions. It does NOT flag Item 1.01 ("Entry into a Material
Definitive Agreement"), which covers a much broader, noisier category of
ordinary commercial contracts, not just signed-but-not-yet-closed merger
agreements — including it would trade real specificity for recall this
increment doesn't need. It also does NOT fetch or parse the actual
filing exhibit to extract deal terms, consideration, or counterparty
identity — this is an OBSERVATION that a completed acquisition/
disposition was disclosed, not this system's INTERPRETATION of its
content (same discipline as capint.adapters.sec_guidance).

This adapter duplicates sec_guidance's fetch-and-filter loop rather than
sharing it — a deliberate "extract on third consumer" choice (this
project's own stated discipline, e.g. capint.adapters.sec_common), since
this is only the second such "item-tagged 8-K" need. A third one should
prompt extracting the shared parts into sec_common.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter
from capint.adapters.sec_common import RateLimitedSecClient, SUBMISSIONS_URL, parse_cik

FORM_TYPE = "8-K"
CORPORATE_ACTION_RELEVANT_ITEMS = {"2.01"}


@dataclass(frozen=True)
class RawCorporateActionDisclosure:
    cik: str
    company_name: str
    item_codes: str  # raw, e.g. "2.01" — verbatim from SEC, not interpreted
    accession_number: str
    filing_date: date
    filed_at: datetime  # SEC acceptance timestamp
    primary_document_url: str | None


class SECCorporateActionAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(self, user_agent: str, client: httpx.Client | None = None, min_request_interval: float = 0.2) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_recent_corporate_actions(self, cik: str, limit: int = 20) -> list[RawCorporateActionDisclosure]:
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
        disclosures: list[RawCorporateActionDisclosure] = []
        for i, form in enumerate(forms):
            if form != FORM_TYPE:
                continue
            item_codes = items_list[i] if i < len(items_list) else ""
            codes = {c.strip() for c in item_codes.split(",") if c.strip()}
            if not codes & CORPORATE_ACTION_RELEVANT_ITEMS:
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
                RawCorporateActionDisclosure(
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
        capint.adapters.sec_guidance.SECGuidanceDisclosureAdapter's
        identical note. This adapter is per-company, not time-windowed."""
        raise NotImplementedError(
            "SECCorporateActionAdapter is per-company; use fetch_recent_corporate_actions "
            "via capint.ingestion.sec_corporate_actions instead."
        )
