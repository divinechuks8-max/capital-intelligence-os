"""Companies House REST API adapter (Phase 11, UK jurisdiction extension).

Companies House is a UK government executive agency (Dept for Business
and Trade); its data is Crown Copyright, licensed under the Open
Government Licence v3.0, and the REST API
(api.company-information.service.gov.uk) is explicitly built for this
kind of reuse. Auth is HTTP Basic with a free, instantly-issued API key
as the username and an empty password (confirmed live).

This adapter covers two endpoints: company profile lookup, and the
Persons with Significant Control (PSC) register — the UK's beneficial-
ownership disclosure regime, and the closest real analogue to Schedule
13D/13G (Phase 6). See capint.models.uk_psc.UKPersonWithSignificantControl's
docstring for the full schema-mapping reasoning.

**A real, load-bearing limitation confirmed live before building this
(not a bug — a fact about the UK regulatory regime):** companies whose
voting shares trade on a "regulated market" — the LSE Main Market, e.g.
Diageo, Barclays, GSK, Rolls-Royce — are exempt from the PSC regime
entirely (Companies Act 2006, Sch 1A). Confirmed against real data: all
four of those returned zero PSC records. Their major-holder disclosures
instead happen via the FCA's DTR5 regime, distributed through RNS
(the London Stock Exchange's Regulatory News Service) or another
FCA-approved Primary Information Provider. This system does NOT ingest
that data: the only realistic aggregator found during research
(investegate.co.uk) has Terms of Use that explicitly prohibit exactly
what this system does — "distribute, republish or otherwise provide any
information or derived works to any third party... or use or process
information or derived works for any commercial purposes" — so it was
deliberately not built against, per this project's public-legal-sources-
only mandate. PSC data ends up populated here mainly for AIM-listed and
smaller UK companies, which are NOT on a "regulated market" and do have
to disclose PSCs normally — confirmed live against Frontier Developments
plc and Angling Direct plc, both AIM-listed, both with real PSC records.

This adapter also cannot tell "exempt" apart from "genuinely has no PSC
to report" for a company that returns zero records — Companies House
exposes a separate persons-with-significant-control-statements endpoint
for that distinction, which this adapter does not call (a real,
documented scope limitation, not an oversight).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx

from capint.adapters.base import SourceAdapter

API_BASE = "https://api.company-information.service.gov.uk"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


# A corporate PSC's `identification.registration_number` is only a genuine
# Companies House number when the PSC itself is registered in a UK
# jurisdiction — a corporate PSC incorporated abroad (Delaware, Cayman, ...)
# has a registration_number scoped to a *different* country's registry, and
# treating it as a UK_COMPANY_NUMBER would risk colliding with an unrelated
# UK company that happens to share the same number. Deliberately a
# conservative whitelist, not a blocklist, for exactly that reason.
_UK_REGISTRATION_COUNTRIES = {
    "england", "wales", "scotland", "northern ireland", "united kingdom",
    "england & wales", "england and wales", "great britain", "uk",
}


@dataclass(frozen=True)
class RawUKCompanyProfile:
    company_number: str
    name: str
    status: str
    jurisdiction: str | None
    incorporated_on: date | None
    sic_codes: list[str]


@dataclass(frozen=True)
class RawUKPersonWithSignificantControl:
    company_number: str
    psc_link: str
    name: str
    kind: str
    natures_of_control: list[str]
    notified_on: date
    ceased_on: date | None
    country_of_residence: str | None
    nationality: str | None
    # Only present for kind="corporate-entity-person-with-significant-control"
    # registered in the UK — a real stable cross-company identifier, unlike
    # individual PSCs (whose psc_link is scoped to this one company/PSC
    # relationship only). See capint.ingestion.companies_house for how this
    # is used to resolve a corporate PSC to the same Company entity that
    # would be created if that company were itself ingested as an issuer.
    identification_registration_number: str | None


class _RateLimitedCompaniesHouseClient:
    """Companies House documents a generous 600-requests-per-5-minutes
    limit (confirmed live via the X-Ratelimit-* response headers); this
    self-imposed pacing is a courteous default, same spirit as every other
    adapter's rate limiter, not an attempt to track that exact window."""

    def __init__(self, api_key: str, client: httpx.Client | None = None, min_request_interval: float = 0.1) -> None:
        if not api_key.strip():
            raise ValueError("A Companies House API key is required — refusing to send unauthenticated requests.")
        self._client = client or httpx.Client(timeout=20.0)
        self._auth = httpx.BasicAuth(api_key, "")
        self._min_interval = min_request_interval
        self._last_request_at: float | None = None

    def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
        resp = self._client.get(url, params=params, auth=self._auth)
        self._last_request_at = time.monotonic()
        return resp


class CompaniesHouseAdapter(SourceAdapter):
    source_name = "Companies House"

    def __init__(self, api_key: str, client: httpx.Client | None = None, min_request_interval: float = 0.1) -> None:
        self._http = _RateLimitedCompaniesHouseClient(api_key, client=client, min_request_interval=min_request_interval)

    def fetch_company_profile(self, company_number: str) -> RawUKCompanyProfile | None:
        resp = self._http.get(f"{API_BASE}/company/{company_number}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return RawUKCompanyProfile(
            company_number=data["company_number"],
            name=data["company_name"],
            status=data["company_status"],
            jurisdiction=data.get("jurisdiction"),
            incorporated_on=_parse_date(data.get("date_of_creation")),
            sic_codes=data.get("sic_codes", []),
        )

    def fetch_psc_register(self, company_number: str) -> list[RawUKPersonWithSignificantControl]:
        """Fetches every current and ceased PSC record for one company,
        paginating through Companies House's items_per_page/start_index
        scheme. Returns an empty list both when the company genuinely has
        none and when it's PSC-exempt (a regulated-market issuer) — see
        this module's docstring for why that ambiguity is a real, accepted
        limitation here."""
        records: list[RawUKPersonWithSignificantControl] = []
        start_index = 0
        page_size = 100
        while True:
            resp = self._http.get(
                f"{API_BASE}/company/{company_number}/persons-with-significant-control",
                params={"items_per_page": page_size, "start_index": start_index},
            )
            if resp.status_code == 404:
                return []
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            for item in items:
                identification = item.get("identification") or {}
                records.append(
                    RawUKPersonWithSignificantControl(
                        company_number=company_number,
                        psc_link=item["links"]["self"],
                        name=item.get("name", "UNKNOWN"),
                        kind=item.get("kind", "unknown"),
                        natures_of_control=item.get("natures_of_control", []),
                        notified_on=_parse_date(item["notified_on"]),
                        ceased_on=_parse_date(item.get("ceased_on")),
                        country_of_residence=item.get("country_of_residence"),
                        nationality=item.get("nationality"),
                        identification_registration_number=(
                            identification.get("registration_number")
                            if (identification.get("country_registered") or "").strip().lower()
                            in _UK_REGISTRATION_COUNTRIES
                            else None
                        ),
                    )
                )
            start_index += len(items)
            total_results = data.get("total_results", 0)
            if start_index >= total_results or not items:
                break
        return records

    def fetch_records(self, since, until) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.finra_short_interest.FINRAShortInterestAdapter's
        identical note. This adapter is per-company, not time-windowed."""
        raise NotImplementedError(
            "CompaniesHouseAdapter is per-company; use fetch_company_profile + fetch_psc_register "
            "via capint.ingestion.companies_house instead."
        )
