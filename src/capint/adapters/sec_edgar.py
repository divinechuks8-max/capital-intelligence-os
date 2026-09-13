"""SEC EDGAR Form 4 adapter (Phase 2).

Data source: SEC EDGAR. Form 3/4/5 filings are U.S. federal government
records — public domain under 17 U.S.C. §105, no redistribution
restriction. Access is still governed by SEC's fair-access policy — see
capint.adapters.sec_common.RateLimitedSecClient, which every SEC adapter
uses for that.

Scope of this increment:
- Only Form 4 (post-transaction ownership changes), not Form 3 (initial
  ownership, no transactions) or Form 5 (annual/deferred). Same XML schema,
  straightforward follow-on.
- Only Table I (non-derivative transactions: common stock bought/sold/
  granted/etc.). Table II (derivative: options, RSUs, warrants) has
  different economics (strike price, underlying security, expiration) that
  capint.models.insider.InsiderTransaction does not yet model — mapping it
  into the same columns would misrepresent the data, so it's skipped and
  counted, not silently dropped or force-fit.
- "Current filings" only (the most recent N across all filers), not a full
  historical backfill — that needs the quarterly full-index files
  (https://www.sec.gov/Archives/edgar/full-index/), a separate adapter
  method left for a later increment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
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
    xml_text,
)

CURRENT_FILINGS_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type={form_type}&company=&dateb=&owner=include&count={count}&output=atom"
)


@dataclass(frozen=True)
class RawForm4Transaction:
    """One non-derivative transaction line from one Form 4, attributed to
    one reporting owner. (A joint filing with N owners and M transactions
    yields N*M of these — see module docstring.)"""

    accession_number: str
    filing_url: str
    published_at: datetime
    document_type: str

    issuer_cik: str  # 10-digit, zero-padded
    issuer_name: str
    issuer_ticker: str | None

    owner_cik: str  # 10-digit, zero-padded
    owner_name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: str | None

    security_title: str
    transaction_date: date
    transaction_code: str  # SEC Section 16(a) code: P, S, A, F, M, G, ...
    acquired_disposed_code: str  # "A" or "D"
    shares_transacted: Decimal
    price_per_share: Decimal | None
    shares_owned_after: Decimal | None
    is_10b5_1_plan: bool

    transaction_index: int  # position within this filing, for idempotency keys


def _decimal(el: ElementTree.Element | None, path: str) -> Decimal | None:
    text = xml_text(el, path)
    if text is None:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


class SECEdgarForm4Adapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
    ) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_current_form4_filings(self, count: int = 100) -> list[FilingRef]:
        """The most recent `count` feed entries, deduplicated to unique
        filings (each filing appears once per named party — issuer and
        every reporting owner — in SEC's current-filings feed)."""
        url = CURRENT_FILINGS_URL.format(form_type="4", count=count)
        resp = self._http.get(url)
        root = ElementTree.fromstring(resp.content)

        seen: dict[str, FilingRef] = {}
        for entry in root.findall("a:entry", ATOM_NS):
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

            category = entry.find("a:category", ATOM_NS)
            form_type = category.get("term") if category is not None else "4"

            updated_text = xml_text(entry, "a:updated", ATOM_NS)
            filed_at = datetime.fromisoformat(updated_text) if updated_text else datetime.now().astimezone()

            seen[accession] = FilingRef(
                accession_number=accession,
                cik_for_path=cik_match.group(1),
                form_type=form_type or "4",
                filed_at=filed_at,
            )

        return list(seen.values())

    def fetch_transactions_for_filing(self, filing: FilingRef) -> tuple[list[RawForm4Transaction], int]:
        """Returns (non-derivative transactions, count of derivative
        transactions skipped)."""
        accession_nodash = filing.accession_number.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{filing.cik_for_path}/{accession_nodash}"

        index = self._http.get(f"{base}/index.json").json()
        items = index.get("directory", {}).get("item", [])
        xml_name = next(
            (i["name"] for i in items if i["name"].lower().endswith(".xml") and "cal" not in i["name"].lower()),
            None,
        )
        if xml_name is None:
            return [], 0

        xml_resp = self._http.get(f"{base}/{xml_name}")
        root = ElementTree.fromstring(xml_resp.content)

        document_type = xml_text(root, "documentType") or filing.form_type
        aff_10b5_1 = (xml_text(root, "aff10b5One") or "false").lower() == "true"

        issuer_el = root.find("issuer")
        issuer_cik = parse_cik(xml_text(issuer_el, "issuerCik") or filing.cik_for_path)
        issuer_name = xml_text(issuer_el, "issuerName") or "UNKNOWN"
        issuer_ticker = xml_text(issuer_el, "issuerTradingSymbol")

        transactions: list[RawForm4Transaction] = []
        derivative_skipped = 0
        txn_index = 0

        for owner_el in root.findall("reportingOwner"):
            owner_id_el = owner_el.find("reportingOwnerId")
            owner_cik = parse_cik(xml_text(owner_id_el, "rptOwnerCik") or "")
            owner_name = xml_text(owner_id_el, "rptOwnerName") or "UNKNOWN"

            rel_el = owner_el.find("reportingOwnerRelationship")
            is_director = (xml_text(rel_el, "isDirector") or "false").lower() == "true"
            is_officer = (xml_text(rel_el, "isOfficer") or "false").lower() == "true"
            is_ten_pct = (xml_text(rel_el, "isTenPercentOwner") or "false").lower() == "true"
            officer_title = xml_text(rel_el, "officerTitle")

            non_deriv_table = root.find("nonDerivativeTable")
            if non_deriv_table is not None:
                for txn_el in non_deriv_table.findall("nonDerivativeTransaction"):
                    coding = txn_el.find("transactionCoding")
                    amounts = txn_el.find("transactionAmounts")
                    post_amounts = txn_el.find("postTransactionAmounts")

                    txn_date_text = xml_text(txn_el, "transactionDate/value")
                    if txn_date_text is None:
                        continue
                    shares = _decimal(amounts, "transactionShares/value")
                    if shares is None:
                        continue

                    transactions.append(
                        RawForm4Transaction(
                            accession_number=filing.accession_number,
                            filing_url=f"{base}/{xml_name}",
                            published_at=filing.filed_at,
                            document_type=document_type,
                            issuer_cik=issuer_cik,
                            issuer_name=issuer_name,
                            issuer_ticker=issuer_ticker,
                            owner_cik=owner_cik,
                            owner_name=owner_name,
                            is_director=is_director,
                            is_officer=is_officer,
                            is_ten_percent_owner=is_ten_pct,
                            officer_title=officer_title,
                            security_title=xml_text(txn_el, "securityTitle/value") or "Common Stock",
                            transaction_date=date.fromisoformat(txn_date_text),
                            transaction_code=xml_text(coding, "transactionCode") or "OTHER",
                            acquired_disposed_code=xml_text(amounts, "transactionAcquiredDisposedCode/value") or "A",
                            shares_transacted=shares,
                            price_per_share=_decimal(amounts, "transactionPricePerShare/value"),
                            shares_owned_after=_decimal(post_amounts, "sharesOwnedFollowingTransaction/value"),
                            is_10b5_1_plan=aff_10b5_1,
                            transaction_index=txn_index,
                        )
                    )
                    txn_index += 1

            derivative_table = root.find("derivativeTable")
            if derivative_table is not None:
                derivative_skipped += len(derivative_table.findall("derivativeTransaction"))

        return transactions, derivative_skipped

    def fetch_records(self, since: datetime, until: datetime) -> Iterable[dict[str, Any]]:
        """Satisfies the generic SourceAdapter interface. Prefer the typed
        `fetch_current_form4_filings` + `fetch_transactions_for_filing`
        pair (used by capint.ingestion.sec_form4) for real ingestion — this
        method exists for callers that only want the generic shape."""
        for filing in self.fetch_current_form4_filings():
            if not (since <= filing.filed_at <= until):
                continue
            transactions, _ = self.fetch_transactions_for_filing(filing)
            for txn in transactions:
                yield txn.__dict__
