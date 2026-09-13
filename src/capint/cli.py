"""Operational entry point for running ingestion by hand.

    python -m capint.cli ingest-form4 --count 50
"""

import argparse
import sys

from capint.adapters.sec_edgar import SECEdgarForm4Adapter
from capint.config import settings
from capint.db import SessionLocal
from capint.ingestion.sec_form4 import run_ingestion


def ingest_form4(count: int) -> int:
    if not settings.sec_edgar_user_agent:
        print(
            "SEC_EDGAR_USER_AGENT is not set (see .env.example) — refusing to send "
            "anonymous requests to SEC EDGAR.",
            file=sys.stderr,
        )
        return 1

    adapter = SECEdgarForm4Adapter(user_agent=settings.sec_edgar_user_agent)
    with SessionLocal() as session:
        summary = run_ingestion(session, adapter, filing_count=count)

    print(f"filings seen:              {summary.filings_seen}")
    print(f"transactions created:      {summary.transactions_created}")
    print(f"duplicates skipped:        {summary.transactions_skipped_duplicate}")
    print(f"derivative txns skipped:   {summary.derivative_transactions_skipped}")
    print(f"filing errors:             {len(summary.filing_errors)}")
    for err in summary.filing_errors:
        print(f"  - {err}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="capint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    form4_parser = subparsers.add_parser("ingest-form4", help="Ingest recent SEC Form 4 filings")
    form4_parser.add_argument("--count", type=int, default=100, help="Number of recent feed entries to scan")

    args = parser.parse_args()
    if args.command == "ingest-form4":
        return ingest_form4(args.count)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
