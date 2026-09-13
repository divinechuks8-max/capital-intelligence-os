"""SEC Schedule 13D / 13G adapter (Phase 6).

Same licensing/fair-access basis as every other SEC adapter (see
capint.adapters.sec_common). Two things about 13D/13G specifically had to
be verified against real, live filings before writing this parser:

1. Discovery: unlike Form 4 and 13F, SEC's legacy "getcurrent" atom feed
   returns "No recent filings" for `type=SC+13D` / `type=SC+13G` — that CGI
   endpoint apparently doesn't index these forms. This adapter instead uses
   EDGAR's full-text-search API (efts.sec.gov/LATEST/search-index), which
   does. That API's hits also don't carry a precise timestamp, only a
   `file_date` (date, no time) — so precise `publication_time` is fetched
   separately, from the `<ACCEPTANCE-DATETIME>` line in the accession's
   full-submission .txt header (only the first ~1KB is fetched via an HTTP
   Range request, not the whole submission). That field has no UTC offset;
   it's SEC's own local time (America/New_York), confirmed by comparing it
   against the same filing's file_date/day-of-week.
2. Schema: SEC modernized both schedules to structured XML circa 2024-2025
   (a `primary_doc.xml`, like Form 4/13F), but 13D and 13G are genuinely
   different schemas, not variants of one — reporting persons are a
   <reportingPersons><reportingPersonInfo> list in 13D vs. a (usually
   singular) <coverPageHeaderReportingPersonDetails> in 13G, with
   different field names for the same concept (e.g. `aggregateAmountOwned`
   vs. `reportingPersonBeneficiallyOwnedAggregateNumberOfShares`), and even
   the issuer CIK tag's capitalization differs (`issuerCIK` vs `issuerCik`).
   This adapter has one parse function per schedule type and normalizes
   both into the same RawScheduleFiling shape.

Scope of this increment:
- Only exact form types "SCHEDULE 13D" / "SCHEDULE 13G" — amendments
  ("SCHEDULE 13D/A", "/G/A") are excluded, same amendment-tracking gap as
  every other adapter here.
- 13D's Item 4 narrative ("transactionPurpose" — the stated reason for the
  stake, activist intentions if any) is captured verbatim as
  `stated_purpose`. It is a FACT (what the filer wrote), not this system's
  interpretation of intent — spec §2/§15 require keeping those separate,
  and nothing here classifies a filing as "activist" beyond its being a
  13D rather than a 13G.
- A reporting person without its own CIK (common for trusts, e.g. "Woodman
  Family Trust" in a joint 13D filing) is carried through with `cik=None`
  — the ingestion layer resolves such entities by exact name match only
  (a coarser, best-effort dedup — see capint.ingestion.sec_13dg).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlencode
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from capint.adapters.base import SourceAdapter
from capint.adapters.sec_common import RateLimitedSecClient, parse_cik, strip_namespaces, xml_text

FULL_TEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_SEC_EASTERN = ZoneInfo("America/New_York")
_ACCEPTANCE_RE = re.compile(rb"<ACCEPTANCE-DATETIME>(\d{14})")

SCHEDULE_13D = "SCHEDULE 13D"
SCHEDULE_13G = "SCHEDULE 13G"


@dataclass(frozen=True)
class ScheduleFilingLead:
    accession_number: str
    cik_for_path: str
    form_type: str
    file_date: date


@dataclass(frozen=True)
class RawReportingPerson:
    cik: str | None  # None if the filing doesn't resolve this person to a CIK
    name: str
    type_code: str | None  # SEC's reporting-person type code: IN, CO, PN, HC, IA, BD, TR, OO, ...
    shares_owned: Decimal | None
    percent_of_class: Decimal | None
    sole_voting_power: Decimal | None
    shared_voting_power: Decimal | None
    sole_dispositive_power: Decimal | None
    shared_dispositive_power: Decimal | None


@dataclass(frozen=True)
class RawScheduleFiling:
    accession_number: str
    filing_url: str
    published_at: datetime
    form_type: str  # SCHEDULE_13D or SCHEDULE_13G

    issuer_cik: str
    issuer_name: str
    issuer_cusip: str | None
    event_date: date | None  # the triggering event's date ("dateOfEvent" / "eventDateRequiresFilingThisStatement")
    stated_purpose: str | None  # 13D Item 4 narrative, verbatim; always None for 13G

    reporting_persons: list[RawReportingPerson] = field(default_factory=list)


def _decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None


def _parse_13d_cover(root: ElementTree.Element) -> tuple[str, str, str | None, date | None, str | None, list[RawReportingPerson]]:
    header = root.find("formData/coverPageHeader")
    issuer_el = header.find("issuerInfo") if header is not None else None
    issuer_cik = parse_cik(xml_text(issuer_el, "issuerCIK") or "")
    issuer_name = xml_text(issuer_el, "issuerName") or "UNKNOWN"
    issuer_cusip = xml_text(issuer_el, "issuerCusips/issuerCusipNumber")

    event_date_text = xml_text(header, "dateOfEvent")
    event_date = datetime.strptime(event_date_text, "%m/%d/%Y").date() if event_date_text else None

    stated_purpose = xml_text(root, "formData/items1To7/item4/transactionPurpose")

    persons: list[RawReportingPerson] = []
    for person_el in root.findall("formData/reportingPersons/reportingPersonInfo"):
        no_cik = (xml_text(person_el, "reportingPersonNoCIK") or "N").upper() == "Y"
        cik = None if no_cik else parse_cik(xml_text(person_el, "reportingPersonCIK") or "")
        persons.append(
            RawReportingPerson(
                cik=cik or None,
                name=xml_text(person_el, "reportingPersonName") or "UNKNOWN",
                type_code=xml_text(person_el, "typeOfReportingPerson"),
                shares_owned=_decimal(xml_text(person_el, "aggregateAmountOwned")),
                percent_of_class=_decimal(xml_text(person_el, "percentOfClass")),
                sole_voting_power=_decimal(xml_text(person_el, "soleVotingPower")),
                shared_voting_power=_decimal(xml_text(person_el, "sharedVotingPower")),
                sole_dispositive_power=_decimal(xml_text(person_el, "soleDispositivePower")),
                shared_dispositive_power=_decimal(xml_text(person_el, "sharedDispositivePower")),
            )
        )

    return issuer_cik, issuer_name, issuer_cusip, event_date, stated_purpose, persons


def _parse_13g_cover(root: ElementTree.Element) -> tuple[str, str, str | None, date | None, str | None, list[RawReportingPerson]]:
    header = root.find("formData/coverPageHeader")
    issuer_el = header.find("issuerInfo") if header is not None else None
    # 13G spells this issuerCik (lowercase 'ik'), unlike 13D's issuerCIK.
    issuer_cik = parse_cik(xml_text(issuer_el, "issuerCik") or xml_text(issuer_el, "issuerCIK") or "")
    issuer_name = xml_text(issuer_el, "issuerName") or "UNKNOWN"
    issuer_cusip = xml_text(issuer_el, "issuerCusips/issuerCusipNumber")

    event_date_text = xml_text(header, "eventDateRequiresFilingThisStatement")
    event_date = datetime.strptime(event_date_text, "%m/%d/%Y").date() if event_date_text else None

    # 13G is a passive-holder disclosure: no "stated purpose" item exists in its schema.
    stated_purpose = None

    # Top-level filer CIK doubles as the (usually singular) reporting person's CIK for 13G —
    # the schema has no per-reporting-person CIK field the way 13D does.
    filer_cik = parse_cik(xml_text(root, "headerData/filerInfo/filer/filerCredentials/cik") or "")

    persons: list[RawReportingPerson] = []
    for person_el in root.findall("formData/coverPageHeaderReportingPersonDetails"):
        shares_el = person_el.find("reportingPersonBeneficiallyOwnedNumberOfShares")
        persons.append(
            RawReportingPerson(
                cik=filer_cik or None,
                name=xml_text(person_el, "reportingPersonName") or "UNKNOWN",
                type_code=xml_text(person_el, "typeOfReportingPerson"),
                shares_owned=_decimal(xml_text(person_el, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares")),
                percent_of_class=_decimal(xml_text(person_el, "classPercent")),
                sole_voting_power=_decimal(xml_text(shares_el, "soleVotingPower")),
                shared_voting_power=_decimal(xml_text(shares_el, "sharedVotingPower")),
                sole_dispositive_power=_decimal(xml_text(shares_el, "soleDispositivePower")),
                shared_dispositive_power=_decimal(xml_text(shares_el, "sharedDispositivePower")),
            )
        )

    return issuer_cik, issuer_name, issuer_cusip, event_date, stated_purpose, persons


class SEC13DGAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
    ) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_recent_filings(
        self, form_type: str, start_date: date, end_date: date, limit: int = 100
    ) -> list[ScheduleFilingLead]:
        """form_type must be exactly SCHEDULE_13D or SCHEDULE_13G — amendments
        (form field ending "/A") are filtered out, same as every other adapter.

        The search API returns one hit per *document*, not per filing — a
        single accession with an exhibit or two shows up as multiple hits.
        Deduplicated by accession number, same pattern as the atom-feed
        adapters (Form 4, 13F) use for the same underlying reason.
        """
        params = {
            "q": '""',
            "forms": form_type,
            "dateRange": "custom",
            "startdt": start_date.isoformat(),
            "enddt": end_date.isoformat(),
        }
        url = f"{FULL_TEXT_SEARCH_URL}?{urlencode(params)}"
        data = self._http.get(url).json()

        seen: dict[str, ScheduleFilingLead] = {}
        for hit in data.get("hits", {}).get("hits", []):
            if len(seen) >= limit:
                break
            source = hit["_source"]
            accession = source["adsh"]
            if accession in seen:
                continue
            if source.get("form") != form_type:  # excludes "SCHEDULE 13D/A" etc.
                continue
            ciks = source.get("ciks") or []
            if not ciks:
                continue
            seen[accession] = ScheduleFilingLead(
                accession_number=accession,
                cik_for_path=ciks[0].lstrip("0") or "0",
                form_type=form_type,
                file_date=date.fromisoformat(source["file_date"]),
            )
        return list(seen.values())

    def _fetch_acceptance_datetime(self, base: str, accession_number: str) -> datetime:
        prefix = self._http.get_prefix(f"{base}/{accession_number}.txt", num_bytes=2048)
        match = _ACCEPTANCE_RE.search(prefix)
        if not match:
            raise ValueError(f"Could not find ACCEPTANCE-DATETIME for {accession_number}")
        naive = datetime.strptime(match.group(1).decode("ascii"), "%Y%m%d%H%M%S")
        return naive.replace(tzinfo=_SEC_EASTERN).astimezone(timezone.utc)

    def fetch_filing(self, lead: ScheduleFilingLead) -> RawScheduleFiling | None:
        """Returns None if the filing's primary_doc.xml can't be located or
        parsed — the caller should count that as a skipped filing."""
        accession_nodash = lead.accession_number.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{lead.cik_for_path}/{accession_nodash}"

        try:
            index = self._http.get(f"{base}/index.json").json()
        except httpx.HTTPStatusError:
            return None
        items = index.get("directory", {}).get("item", [])
        if not any(i["name"].lower() == "primary_doc.xml" for i in items):
            return None

        published_at = self._fetch_acceptance_datetime(base, lead.accession_number)
        root = strip_namespaces(ElementTree.fromstring(self._http.get(f"{base}/primary_doc.xml").content))

        if lead.form_type == SCHEDULE_13D:
            issuer_cik, issuer_name, issuer_cusip, event_date, stated_purpose, persons = _parse_13d_cover(root)
        else:
            issuer_cik, issuer_name, issuer_cusip, event_date, stated_purpose, persons = _parse_13g_cover(root)

        if not issuer_cik or not persons:
            return None

        return RawScheduleFiling(
            accession_number=lead.accession_number,
            filing_url=f"{base}/primary_doc.xml",
            published_at=published_at,
            form_type=lead.form_type,
            issuer_cik=issuer_cik,
            issuer_name=issuer_name,
            issuer_cusip=issuer_cusip,
            event_date=event_date,
            stated_purpose=stated_purpose,
            reporting_persons=persons,
        )

    def fetch_records(self, since: datetime, until: datetime) -> Iterable[dict[str, Any]]:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.sec_edgar.SECEdgarForm4Adapter.fetch_records for
        why the typed methods above are preferred for real ingestion."""
        for form_type in (SCHEDULE_13D, SCHEDULE_13G):
            for lead in self.fetch_recent_filings(form_type, since.date(), until.date()):
                filing = self.fetch_filing(lead)
                if filing is None or not (since <= filing.published_at <= until):
                    continue
                yield {**filing.__dict__}
