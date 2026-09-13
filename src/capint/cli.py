"""Operational entry points for running ingestion by hand.

    python -m capint.cli ingest-form4 --count 50
    python -m capint.cli ingest-13f --count 20
    python -m capint.cli ingest-13dg --days-back 7
"""

import argparse
import sys
from datetime import date, timedelta

from capint.adapters.sec_13dg import SEC13DGAdapter
from capint.adapters.sec_13f import SEC13FAdapter
from capint.adapters.sec_edgar import SECEdgarForm4Adapter
from capint.config import settings
from capint.db import SessionLocal
from capint.ingestion.sec_13dg import run_ingestion as run_13dg_ingestion
from capint.ingestion.sec_13f import run_ingestion as run_13f_ingestion
from capint.ingestion.sec_form4 import run_ingestion as run_form4_ingestion


def _require_user_agent() -> bool:
    if not settings.sec_edgar_user_agent:
        print(
            "SEC_EDGAR_USER_AGENT is not set (see .env.example) — refusing to send "
            "anonymous requests to SEC EDGAR.",
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

    args = parser.parse_args()
    if args.command == "ingest-form4":
        return ingest_form4(args.count)
    if args.command == "ingest-13f":
        return ingest_13f(args.count)
    if args.command == "ingest-13dg":
        return ingest_13dg(args.days_back, args.limit)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
