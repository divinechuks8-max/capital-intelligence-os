"""SEC Form N-PORT adapter (Phase 9): fund/ETF assets under management.

Confirmed live, against SPY (SPDR S&P 500 ETF Trust, one of the largest
and most liquid ETFs in the world) before designing this: its real N-PORT
filings expose total/net assets, but NO shares-outstanding-by-class figure
anywhere in the document. That number is what a true "flow" calculation
(net creation/redemption, isolated from market price movement) requires —
without it, this adapter tracks assets under management (AUM) over time,
not flow. See capint.models.fund.FundAumSnapshot's docstring for the full
reasoning; this is a deliberate, honest scope reduction from the spec's
"ETF/fund flows" framing (§17), not a partial/buggy attempt at it.

Also structurally different from every other adapter here in its
discovery mechanism: like capint.adapters.sec_xbrl, there's no universe-
wide "recent filings" feed to drive this from, so it's per-fund (an
explicit CIK list). Unlike sec_xbrl, discovery here uses
data.sec.gov/submissions (capint.adapters.sec_common.fetch_filings_for_cik)
rather than a company-facts endpoint, since N-PORT data isn't XBRL/
us-gaap — it has its own schema (xmlns=".../edgar/nport").

Granularity ceiling: N-PORT is filed quarterly with a ~60-day
confidentiality/disclosure lag (confirmed live: SPY's 2026-06-30 quarter
wasn't accepted until 2026-08-28). Spec §17's 1-day/5-day/20-day/60-day
flow cadence is not achievable from this source at any implementation
quality — it's a hard ceiling of the data itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree import ElementTree

import httpx

from capint.adapters.base import SourceAdapter
from capint.adapters.sec_common import RateLimitedSecClient, SUBMISSIONS_URL, parse_cik, strip_namespaces, xml_text

NPORT_FORM_TYPE = "NPORT-P"


@dataclass(frozen=True)
class NPortFilingLead:
    accession_number: str
    cik: str  # 10-digit, zero-padded
    fund_name: str
    ticker: str | None
    filed_at: datetime  # precise SEC acceptance timestamp


@dataclass(frozen=True)
class RawFundAumFact:
    cik: str
    fund_name: str
    ticker: str | None
    series_name: str | None
    period_end: date  # the "as of" date this snapshot reports (repPdDate)
    total_assets_usd: Decimal
    total_liabilities_usd: Decimal | None
    net_assets_usd: Decimal
    form: str
    accession: str
    filed_at: datetime


class SECNPortAdapter(SourceAdapter):
    source_name = "SEC EDGAR"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        min_request_interval: float = 0.2,
    ) -> None:
        self._http = RateLimitedSecClient(user_agent, client=client, min_request_interval=min_request_interval)

    def fetch_recent_filings(self, cik: str, limit: int = 20) -> list[NPortFilingLead]:
        """Exact form type "NPORT-P" only — amendments ("NPORT-P/A")
        excluded, same as every other adapter here."""
        url = SUBMISSIONS_URL.format(cik=parse_cik(cik))
        try:
            data = self._http.get(url).json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise

        fund_name = data.get("name", "UNKNOWN")
        tickers = data.get("tickers") or []
        ticker = tickers[0] if tickers else None

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])

        leads: list[NPortFilingLead] = []
        for i, form in enumerate(forms):
            if form != NPORT_FORM_TYPE:
                continue
            leads.append(
                NPortFilingLead(
                    accession_number=recent["accessionNumber"][i],
                    cik=parse_cik(cik),
                    fund_name=fund_name,
                    ticker=ticker,
                    filed_at=datetime.fromisoformat(recent["acceptanceDateTime"][i].replace("Z", "+00:00")),
                )
            )
            if len(leads) >= limit:
                break
        return leads

    def fetch_fund_snapshot(self, lead: NPortFilingLead) -> RawFundAumFact | None:
        """Returns None if the filing's primary_doc.xml can't be parsed
        (an unusual submission shape) — the caller should count that as a
        skipped filing, not a fabricated one. Only genInfo/fundInfo are
        read; the (often huge) portfolio-holdings section is not parsed
        in this phase."""
        cik_for_path = lead.cik.lstrip("0") or "0"
        accession_nodash = lead.accession_number.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_for_path}/{accession_nodash}/primary_doc.xml"

        try:
            resp = self._http.get(url)
        except httpx.HTTPStatusError:
            return None
        try:
            root = strip_namespaces(ElementTree.fromstring(resp.content))
        except ElementTree.ParseError:
            return None

        gen_info = root.find("formData/genInfo")
        fund_info = root.find("formData/fundInfo")
        if gen_info is None or fund_info is None:
            return None

        period_text = xml_text(gen_info, "repPdDate")
        series_name = xml_text(gen_info, "seriesName")
        total_assets_text = xml_text(fund_info, "totAssets")
        net_assets_text = xml_text(fund_info, "netAssets")
        total_liabs_text = xml_text(fund_info, "totLiabs")
        if period_text is None or total_assets_text is None or net_assets_text is None:
            return None

        try:
            period_end = date.fromisoformat(period_text)
            total_assets = Decimal(total_assets_text)
            net_assets = Decimal(net_assets_text)
            total_liabs = Decimal(total_liabs_text) if total_liabs_text is not None else None
        except (InvalidOperation, ValueError):
            return None

        return RawFundAumFact(
            cik=lead.cik,
            fund_name=lead.fund_name,
            ticker=lead.ticker,
            series_name=series_name if series_name and series_name != "N/A" else None,
            period_end=period_end,
            total_assets_usd=total_assets,
            total_liabilities_usd=total_liabs,
            net_assets_usd=net_assets,
            form=NPORT_FORM_TYPE,
            accession=lead.accession_number,
            filed_at=lead.filed_at,
        )

    def fetch_records(self, since: datetime, until: datetime) -> Any:
        """Satisfies the generic SourceAdapter interface — see
        capint.adapters.sec_xbrl.SECXBRLFactsAdapter.fetch_records for why
        the typed methods above are preferred for real ingestion."""
        raise NotImplementedError(
            "SECNPortAdapter is per-fund; use fetch_recent_filings + fetch_fund_snapshot "
            "via capint.ingestion.sec_nport instead."
        )
