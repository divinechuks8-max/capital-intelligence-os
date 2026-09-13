"""Operational entry points for running ingestion by hand.

    python -m capint.cli ingest-form4 --count 50
    python -m capint.cli ingest-13f --count 20
    python -m capint.cli ingest-13dg --days-back 7
    python -m capint.cli ingest-financials --from-tracked
    python -m capint.cli ingest-nport --cik 0000884394
    python -m capint.cli ingest-short-interest --ticker AAPL
    python -m capint.cli ingest-uk-psc --company-number 05151321
    python -m capint.cli ingest-crypto-treasury --address 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa
    python -m capint.cli ingest-guidance --cik 0000320193
"""

import argparse
import sys
from datetime import date, timedelta

from sqlalchemy import select

from capint.adapters.blockchain_info import BlockchainInfoAdapter
from capint.adapters.companies_house import CompaniesHouseAdapter
from capint.adapters.finra_short_interest import FINRAShortInterestAdapter
from capint.adapters.sec_13dg import SEC13DGAdapter
from capint.adapters.sec_13f import SEC13FAdapter
from capint.adapters.sec_edgar import SECEdgarForm4Adapter
from capint.adapters.sec_guidance import SECGuidanceDisclosureAdapter
from capint.adapters.sec_nport import SECNPortAdapter
from capint.adapters.sec_xbrl import SECXBRLFactsAdapter
from capint.config import settings
from capint.db import SessionLocal
from capint.ingestion.blockchain_info import run_ingestion as run_crypto_treasury_ingestion
from capint.ingestion.companies_house import run_ingestion as run_uk_psc_ingestion
from capint.ingestion.finra_short_interest import run_ingestion as run_short_interest_ingestion
from capint.ingestion.sec_13dg import run_ingestion as run_13dg_ingestion
from capint.ingestion.sec_13f import run_ingestion as run_13f_ingestion
from capint.ingestion.sec_form4 import run_ingestion as run_form4_ingestion
from capint.ingestion.sec_guidance import run_ingestion as run_guidance_ingestion
from capint.ingestion.sec_nport import run_ingestion as run_nport_ingestion
from capint.ingestion.sec_xbrl import run_ingestion as run_xbrl_ingestion
from capint.models.company import Company
from capint.models.entity import EntityIdentifier, IdentifierType


def _require_user_agent() -> bool:
    if not settings.sec_edgar_user_agent:
        print(
            "SEC_EDGAR_USER_AGENT is not set (see .env.example) — refusing to send "
            "anonymous requests to SEC EDGAR.",
            file=sys.stderr,
        )
        return False
    return True


def _require_companies_house_api_key() -> bool:
    if not settings.companies_house_api_key:
        print(
            "COMPANIES_HOUSE_API_KEY is not set (see .env.example) — refusing to send "
            "unauthenticated requests to Companies House.",
            file=sys.stderr,
        )
        return False
    return True


def ingest_form4(count: int) -> int:
    if not _require_user_agent():
        return 1

    adapter = SECEdgarForm4Adapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        summary = run_form4_ingestion(session, adapter, filing_count=count)

    print(f"filings seen:              {summary.filings_seen}")
    print(f"transactions created:      {summary.transactions_created}")
    print(f"duplicates skipped:        {summary.transactions_skipped_duplicate}")
    print(f"derivative txns skipped:   {summary.derivative_transactions_skipped}")
    print(f"filing errors:             {len(summary.filing_errors)}")
    for err in summary.filing_errors:
        print(f"  - {err}")
    return 0


def ingest_13f(count: int) -> int:
    if not _require_user_agent():
        return 1

    adapter = SEC13FAdapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        summary = run_13f_ingestion(session, adapter, filing_count=count)

    print(f"filings seen:              {summary.filings_seen}")
    print(f"filings ingested:          {summary.filings_ingested}")
    print(f"filings skipped (dup):     {summary.filings_skipped_duplicate}")
    print(f"holdings created:          {summary.holdings_created}")
    print(f"exits created:             {summary.exits_created}")
    print(f"filing errors:             {len(summary.filing_errors)}")
    for err in summary.filing_errors:
        print(f"  - {err}")
    return 0


def ingest_13dg(days_back: int, limit: int) -> int:
    if not _require_user_agent():
        return 1

    adapter = SEC13DGAdapter(user_agent=settings.sec_edgar_user_agent)
    until = date.today()
    since = until - timedelta(days=days_back)
    with SessionLocal() as session:
        summary = run_13dg_ingestion(session, adapter, since, until, limit=limit)

    print(f"filings seen:                {summary.filings_seen}")
    print(f"disclosures created:         {summary.disclosures_created}")
    print(f"disclosures skipped (dup):   {summary.disclosures_skipped_duplicate}")
    print(f"filing errors:               {len(summary.filing_errors)}")
    for err in summary.filing_errors:
        print(f"  - {err}")
    return 0


def ingest_financials(ciks: list[str], from_tracked: bool) -> int:
    """Ingests both capital-allocation facts (Phase 7: buybacks, dividends,
    debt) and fundamentals (Phase 8: revenue, earnings, margins) — one
    XBRL company-facts fetch per company feeds both."""
    if not _require_user_agent():
        return 1

    adapter = SECXBRLFactsAdapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        if from_tracked:
            # Only entities with a Company profile — a bare EntityIdentifier(CIK)
            # scan would also pull in Person (Form 4 insiders) and Institution
            # (13F filers) CIKs, which XBRL company-facts isn't meaningful for.
            # Merged with (not replacing) any explicit --cik values.
            tracked = session.execute(
                select(EntityIdentifier.identifier_value)
                .join(Company, Company.entity_id == EntityIdentifier.entity_id)
                .where(EntityIdentifier.identifier_type == IdentifierType.CIK)
                .distinct()
            ).scalars()
            ciks = list(dict.fromkeys([*ciks, *tracked]))
        if not ciks:
            print(
                "No CIKs to process — pass --cik one or more times, or --from-tracked "
                "once some companies have been ingested via ingest-form4/ingest-13f/ingest-13dg.",
                file=sys.stderr,
            )
            return 1

        summary = run_xbrl_ingestion(session, adapter, ciks)

    print(f"companies seen:                    {summary.companies_seen}")
    print(f"companies with no facts:           {summary.companies_with_no_facts}")
    print(f"capital-allocation facts created:  {summary.facts_created}")
    print(f"capital-allocation facts dup:      {summary.facts_skipped_duplicate}")
    print(f"fundamental reports created:       {summary.fundamental_reports_created}")
    print(f"fundamental reports dup:           {summary.fundamental_reports_skipped_duplicate}")
    print(f"company errors:                    {len(summary.company_errors)}")
    for err in summary.company_errors:
        print(f"  - {err}")
    return 0


def ingest_nport(ciks: list[str], filing_count: int) -> int:
    """Ingests fund/ETF assets-under-management snapshots from Form N-PORT
    (Phase 9). No --from-tracked here: funds aren't reliably discoverable
    from the companies this system already tracks (13F resolves ETF
    positions as generic CUSIP-keyed "companies", not funds) — explicit
    --cik is required."""
    if not _require_user_agent():
        return 1
    if not ciks:
        print("No CIKs to process — pass --cik one or more times (e.g. --cik 0000884394 for SPY).", file=sys.stderr)
        return 1

    adapter = SECNPortAdapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        summary = run_nport_ingestion(session, adapter, ciks, filing_count=filing_count)

    print(f"funds seen:                  {summary.funds_seen}")
    print(f"funds with no filings:       {summary.funds_with_no_filings}")
    print(f"AUM snapshots created:       {summary.snapshots_created}")
    print(f"AUM snapshots skipped (dup): {summary.snapshots_skipped_duplicate}")
    print(f"fund errors:                 {len(summary.fund_errors)}")
    for err in summary.fund_errors:
        print(f"  - {err}")
    return 0


def ingest_short_interest(tickers: list[str], num_cycles: int) -> int:
    """Ingests FINRA consolidated short interest (Phase 10). Resolves
    companies by bare ticker — see capint.ingestion.finra_short_interest's
    module docstring for why that's a documented limitation, not an
    oversight. No --from-tracked: this system has no reliable way to map
    its CIK-keyed companies back to a current ticker without point-in-time
    ticker validity checks (out of scope for this phase) — explicit
    --ticker is required."""
    if not _require_user_agent():
        return 1
    if not tickers:
        print("No tickers to process — pass --ticker one or more times (e.g. --ticker AAPL).", file=sys.stderr)
        return 1

    adapter = FINRAShortInterestAdapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        summary = run_short_interest_ingestion(session, adapter, tickers, num_cycles=num_cycles)

    print(f"tickers seen:                {summary.tickers_seen}")
    print(f"settlement cycles found:     {summary.settlement_cycles_seen}")
    print(f"snapshots created:           {summary.snapshots_created}")
    print(f"snapshots skipped (dup):     {summary.snapshots_skipped_duplicate}")
    print(f"snapshots not reported:      {summary.snapshots_not_reported}")
    print(f"ticker errors:               {len(summary.ticker_errors)}")
    for err in summary.ticker_errors:
        print(f"  - {err}")
    return 0


def ingest_uk_psc(company_numbers: list[str]) -> int:
    """Ingests UK Persons with Significant Control (beneficial ownership)
    disclosures from Companies House (Phase 11). No --from-tracked: this
    system's existing companies are resolved by CIK/CUSIP (US filers), not
    Companies House numbers — explicit --company-number is required.

    Real limitation, confirmed live before this was built: PSC data is
    empty for UK companies on a "regulated market" (LSE Main Market) —
    they're exempt by law (Companies Act 2006, Sch 1A) and disclose major
    holders via a different regime this system doesn't ingest (see
    capint.adapters.companies_house's module docstring). PSC data is
    populated mainly for AIM-listed and smaller UK companies."""
    if not _require_companies_house_api_key():
        return 1
    if not company_numbers:
        print(
            "No company numbers to process — pass --company-number one or more times "
            "(e.g. --company-number 05151321 for Angling Direct plc).",
            file=sys.stderr,
        )
        return 1

    adapter = CompaniesHouseAdapter(api_key=settings.companies_house_api_key)
    with SessionLocal() as session:
        summary = run_uk_psc_ingestion(session, adapter, company_numbers)

    print(f"companies seen:              {summary.companies_seen}")
    print(f"companies with no PSC:       {summary.companies_with_no_psc}")
    print(f"PSC records created:         {summary.psc_records_created}")
    print(f"PSC records skipped (dup):   {summary.psc_records_skipped_duplicate}")
    print(f"company errors:              {len(summary.company_errors)}")
    for err in summary.company_errors:
        print(f"  - {err}")
    return 0


def ingest_crypto_treasury(addresses: list[str], limit: int) -> int:
    """Ingests on-chain Bitcoin wallet activity for explicitly-provided
    addresses (Phase 12, crypto extension). No API key needed —
    blockchain.info is free and unauthenticated. No --from-tracked: this
    system has no mechanism linking a wallet address to any company/person
    it already tracks (see capint.models.crypto's module docstring for why
    no such attribution is attempted here) — explicit --address is
    required."""
    if not addresses:
        print(
            "No addresses to process — pass --address one or more times "
            "(e.g. --address 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa).",
            file=sys.stderr,
        )
        return 1

    adapter = BlockchainInfoAdapter()
    with SessionLocal() as session:
        summary = run_crypto_treasury_ingestion(session, adapter, addresses, limit=limit)

    print(f"wallets seen:                {summary.wallets_seen}")
    print(f"wallets with no activity:    {summary.wallets_with_no_activity}")
    print(f"movements created:           {summary.movements_created}")
    print(f"movements skipped (dup):     {summary.movements_skipped_duplicate}")
    print(f"wallet errors:               {len(summary.wallet_errors)}")
    for err in summary.wallet_errors:
        print(f"  - {err}")
    return 0


def ingest_guidance(ciks: list[str], filing_count: int) -> int:
    """Ingests guidance-relevant 8-K disclosures (Items 2.02/7.01) for
    explicitly-provided companies (Phase 12). No structured guidance
    extraction is attempted — see capint.models.guidance's module
    docstring for why this is an honest observation-only scope."""
    if not _require_user_agent():
        return 1
    if not ciks:
        print("No CIKs to process — pass --cik one or more times (e.g. --cik 0000320193 for Apple).", file=sys.stderr)
        return 1

    adapter = SECGuidanceDisclosureAdapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        summary = run_guidance_ingestion(session, adapter, ciks, filing_count=filing_count)

    print(f"companies seen:                  {summary.companies_seen}")
    print(f"companies with no disclosures:   {summary.companies_with_no_disclosures}")
    print(f"disclosures created:             {summary.disclosures_created}")
    print(f"disclosures skipped (dup):       {summary.disclosures_skipped_duplicate}")
    print(f"company errors:                  {len(summary.company_errors)}")
    for err in summary.company_errors:
        print(f"  - {err}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="capint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    form4_parser = subparsers.add_parser("ingest-form4", help="Ingest recent SEC Form 4 filings")
    form4_parser.add_argument("--count", type=int, default=100, help="Number of recent feed entries to scan")

    f13_parser = subparsers.add_parser("ingest-13f", help="Ingest recent SEC Form 13F-HR filings")
    f13_parser.add_argument("--count", type=int, default=100, help="Number of recent feed entries to scan")

    dg_parser = subparsers.add_parser("ingest-13dg", help="Ingest recent SEC Schedule 13D/13G filings")
    dg_parser.add_argument("--days-back", type=int, default=7, help="How many days back to search")
    dg_parser.add_argument("--limit", type=int, default=100, help="Max filings per schedule type")

    xbrl_parser = subparsers.add_parser(
        "ingest-financials",
        help="Ingest SEC XBRL capital-allocation (buyback/dividend/debt) and fundamentals facts",
    )
    xbrl_parser.add_argument("--cik", action="append", default=[], help="Company CIK (repeatable)")
    xbrl_parser.add_argument(
        "--from-tracked", action="store_true", help="Use every CIK-identified company already in the database"
    )

    nport_parser = subparsers.add_parser("ingest-nport", help="Ingest fund/ETF AUM snapshots from Form N-PORT")
    nport_parser.add_argument("--cik", action="append", default=[], help="Fund CIK (repeatable)")
    nport_parser.add_argument("--filing-count", type=int, default=8, help="Max recent N-PORT filings per fund")

    short_interest_parser = subparsers.add_parser(
        "ingest-short-interest", help="Ingest FINRA consolidated short interest"
    )
    short_interest_parser.add_argument("--ticker", action="append", default=[], help="Ticker symbol (repeatable)")
    short_interest_parser.add_argument(
        "--num-cycles", type=int, default=3, help="Number of recent settlement cycles to ingest per ticker"
    )

    uk_psc_parser = subparsers.add_parser(
        "ingest-uk-psc", help="Ingest UK Persons with Significant Control (beneficial ownership) disclosures"
    )
    uk_psc_parser.add_argument(
        "--company-number", action="append", default=[], help="Companies House number (repeatable)"
    )

    crypto_parser = subparsers.add_parser(
        "ingest-crypto-treasury", help="Ingest on-chain Bitcoin wallet activity for tracked addresses"
    )
    crypto_parser.add_argument("--address", action="append", default=[], help="Bitcoin address (repeatable)")
    crypto_parser.add_argument("--limit", type=int, default=50, help="Max recent transactions per address")

    guidance_parser = subparsers.add_parser(
        "ingest-guidance", help="Ingest guidance-relevant 8-K disclosures (Items 2.02/7.01)"
    )
    guidance_parser.add_argument("--cik", action="append", default=[], help="Company CIK (repeatable)")
    guidance_parser.add_argument("--filing-count", type=int, default=20, help="Max recent 8-K filings to scan per company")

    args = parser.parse_args()
    if args.command == "ingest-form4":
        return ingest_form4(args.count)
    if args.command == "ingest-13f":
        return ingest_13f(args.count)
    if args.command == "ingest-13dg":
        return ingest_13dg(args.days_back, args.limit)
    if args.command == "ingest-financials":
        return ingest_financials(args.cik, args.from_tracked)
    if args.command == "ingest-nport":
        return ingest_nport(args.cik, args.filing_count)
    if args.command == "ingest-short-interest":
        return ingest_short_interest(args.ticker, args.num_cycles)
    if args.command == "ingest-uk-psc":
        return ingest_uk_psc(args.company_number)
    if args.command == "ingest-crypto-treasury":
        return ingest_crypto_treasury(args.address, args.limit)
    if args.command == "ingest-guidance":
        return ingest_guidance(args.cik, args.filing_count)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
