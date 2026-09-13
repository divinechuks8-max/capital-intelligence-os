"""SEC EDGAR Form 13F-HR adapter (Phase 4).

Same data source, licensing, and fair-access constraints as
capint.adapters.sec_edgar (SEC EDGAR, public domain, identifying
User-Agent required, self-rate-limited) — see that module's docstring.

13F structure, confirmed against a live filing before writing this parser:
each filing has exactly two structured XML documents —
- `primary_doc.xml` (fixed name): the cover page — filer CIK/name and the
  `periodOfReport` (the quarter-end the holdings are as of).
- one other, arbitrarily-named XML: the "information table" — one
  <infoTable> row per (security, voting-authority split, or joint-filer
  attribution). The SAME cusip can legitimately appear more than once
  (e.g. split across otherManager sub-filers); this adapter aggregates by
  CUSIP into one row per security per filing, since capint.models tracks
  one position per institution/company/period, not per line item.

Scope of this increment:
- Only exact form type "13F-HR", not "13F-HR/A" amendments (same
  don't-reconcile-amendments limitation as the Form 4 adapter) or 13F-NT
  (notice — no holdings).
- `value` is stored here exactly as the info table reports it: whole US
  dollars. This deliberately contradicts the widely-repeated "13F value is
  in thousands" rule (true of the legacy pre-2023 plain-text 13F format) —
  cross-checked against a real, contemporaneous Berkshire Hathaway 13F-HR
  (692,000 AAPL shares, value=200237120 => $289.36/share, a plausible real
  price; treating that as thousands would imply ~$289,000/share, which
  isn't) before writing this. If a future filer's software reports the
  legacy convention instead, holdings from it will be ~1000x understated —
  no code here tries to detect or correct that per filer.
- Joint filings (multiple managers reporting through one filer) attribute
  every row to the filing manager on the cover page, not the specific
  otherManager sub-filer named in each row — a coarser attribution than
  the source data actually supports, documented as a known limitation
  rather than silently assumed away.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree

import httpx

from capint.adapters.base import SourceAdapter
from capint.adapters.sec_common import (
    ACCESSION_RE,
    ATOM_NS,
    CIK_IN_PATH_RE,
    FilingRef,
    RateLimitedSecClient,
    parse_cik,
    strip_namespaces,
    xml_text,
)

CURRENT_FILINGS_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type={form_type}&company=&dateb=&owner=include&count={count}&output=atom"
)


@dataclass(frozen=True)
class RawHolding:
    cusip: str
    issuer_name: str
    shares: Decimal
    market_value_usd: Decimal


@dataclass(frozen=True)
class Filing13FData:
    accession_number: str
    filing_url: str
    published_at: datetime
    form_type: str

    filer_cik: str
    filer_name: str
    period_of_report: date

    holdings: list[RawHolding] = field(default_factory=list)


class SEC13FAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
    ) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_current_13f_filings(self, count: int = 100) -> list[FilingRef]:
        """The most recent `count` feed entries with form type exactly
        "13F-HR" (amendments excluded — see module docstring), deduplicated
        to unique accessions."""
        url = CURRENT_FILINGS_URL.format(form_type="13F-HR", count=count)
        resp = self._http.get(url)
        root = ElementTree.fromstring(resp.content)

        seen: dict[str, FilingRef] = {}
        for entry in root.findall("a:entry", ATOM_NS):
            category = entry.find("a:category", ATOM_NS)
            form_type = category.get("term") if category is not None else None
            if form_type != "13F-HR":
                continue

            entry_id = xml_text(entry, "a:id", ATOM_NS) or ""
            accession_match = ACCESSION_RE.search(entry_id)
            if not accession_match:
                continue
            accession = accession_match.group(1)
            if accession in seen:
                continue

            href = entry.find("a:link", ATOM_NS)
            href_url = href.get("href") if href is not None else None
            cik_match = CIK_IN_PATH_RE.search(href_url or "")
            if not cik_match:
                continue

            updated_text = xml_text(entry, "a:updated", ATOM_NS)
            filed_at = datetime.fromisoformat(updated_text) if updated_text else datetime.now().astimezone()

            seen[accession] = FilingRef(
                accession_number=accession,
                cik_for_path=cik_match.group(1),
                form_type=form_type,
                filed_at=filed_at,
            )

        return list(seen.values())

    def fetch_holdings_for_filing(self, filing: FilingRef) -> Filing13FData | None:
        """Returns None if the filing's two expected XML documents can't be
        found (e.g. an unusual submission shape) — the caller should count
        that as a skipped filing, not a fabricated empty one."""
        accession_nodash = filing.accession_number.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{filing.cik_for_path}/{accession_nodash}"

        index = self._http.get(f"{base}/index.json").json()
        items = index.get("directory", {}).get("item", [])
        xml_names = [i["name"] for i in items if i["name"].lower().endswith(".xml")]
        if "primary_doc.xml" not in [n.lower() for n in xml_names]:
            return None
        info_table_names = [n for n in xml_names if n.lower() != "primary_doc.xml"]
        if not info_table_names:
            return None
        cover_name = next(n for n in xml_names if n.lower() == "primary_doc.xml")
        info_table_name = info_table_names[0]

        cover_root = strip_namespaces(ElementTree.fromstring(self._http.get(f"{base}/{cover_name}").content))
        filer_cik = parse_cik(xml_text(cover_root, "headerData/filerInfo/filer/credentials/cik") or filing.cik_for_path)
        filer_name = xml_text(cover_root, "formData/coverPage/filingManager/name") or "UNKNOWN"
        period_text = xml_text(cover_root, "headerData/filerInfo/periodOfReport")
        if period_text is None:
            return None
        period_of_report = datetime.strptime(period_text, "%m-%d-%Y").date()

        info_root = strip_namespaces(ElementTree.fromstring(self._http.get(f"{base}/{info_table_name}").content))
        by_cusip: dict[str, dict[str, Any]] = {}
        for row in info_root.findall("infoTable"):
            cusip = xml_text(row, "cusip")
            issuer_name = xml_text(row, "nameOfIssuer")
            shares_text = xml_text(row, "shrsOrPrnAmt/sshPrnamt")
            value_text = xml_text(row, "value")
            if cusip is None or shares_text is None or value_text is None:
                continue
            try:
                shares = Decimal(shares_text)
                value_usd = Decimal(value_text)  # already whole USD — see module docstring
            except InvalidOperation:
                continue

            bucket = by_cusip.setdefault(cusip, {"issuer_name": issuer_name or "UNKNOWN", "shares": Decimal("0"), "value": Decimal("0")})
            bucket["shares"] += shares
            bucket["value"] += value_usd

        holdings = [
            RawHolding(cusip=cusip, issuer_name=b["issuer_name"], shares=b["shares"], market_value_usd=b["value"])
            for cusip, b in by_cusip.items()
        ]

        return Filing13FData(
            accession_number=filing.accession_number,
            filing_url=f"{base}/{info_table_name}",
            published_at=filing.filed_at,
            form_type=filing.form_type,
            filer_cik=filer_cik,
            filer_name=filer_name,
            period_of_report=period_of_report,
            holdings=holdings,
        )

    def fetch_records(self, since: datetime, until: datetime) -> Iterable[dict[str, Any]]:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.sec_edgar.SECEdgarForm4Adapter.fetch_records for
        why the typed methods above are preferred for real ingestion."""
        for filing in self.fetch_current_13f_filings():
            if not (since <= filing.filed_at <= until):
                continue
            data = self.fetch_holdings_for_filing(filing)
            if data is None:
                continue
            for holding in data.holdings:
                yield {
                    "filer_cik": data.filer_cik,
                    "filer_name": data.filer_name,
                    "period_of_report": data.period_of_report,
                    **holding.__dict__,
                }
