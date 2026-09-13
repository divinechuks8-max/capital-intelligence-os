# Capital Intelligence OS

Public-market intelligence and capital-flow research platform. See project
notes for the full multi-phase spec; this repo currently implements the
Phase 1 foundation only: entity resolution, event/provenance modeling, and
point-in-time (temporal) correctness.

Uses public data only. Nothing here obtains, infers from, or facilitates
trading on material non-public information. Outputs are research/decision
support, not investment advice.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
cp .env.example .env  # then point DATABASE_URL at a real Postgres instance
```

## Database

```bash
alembic upgrade head
```

Tests never touch `DATABASE_URL` — they run against an isolated in-memory
SQLite engine (see `tests/conftest.py`).

## Run the API

```bash
uvicorn capint.api.main:app --reload
```

## Ingest real SEC Form 4 filings

Requires `SEC_EDGAR_USER_AGENT` in `.env` (an identifying "org/name contact@email",
per SEC's fair-access policy — anonymous requests are refused):

```bash
python -m capint.cli ingest-form4 --count 100
```

Pulls the most recent N entries from EDGAR's current-filings feed, parses
non-derivative (Table I) transactions out of each Form 4, resolves/creates
Entity rows by CIK, and persists Event/InsiderTransaction rows. Safe to
re-run — already-ingested transactions are skipped by their
`(accession number, transaction index)` idempotency key.

## Test

```bash
pytest
```

Runs entirely offline against an in-memory SQLite engine and a mocked HTTP
transport (`tests/fixtures/sec_form4/`, built from one real, captured,
public Form 4 filing — SEC filings are U.S. government records, public
domain under 17 U.S.C. §105). No test depends on SEC's servers being
reachable.

## Layout

- `src/capint/models/` — SQLAlchemy models: Entity/EntityIdentifier,
  Source/Document (provenance), Event, Company, Person/PersonCompanyRole,
  InsiderTransaction.
- `src/capint/temporal.py` — point-in-time query helpers. Read this before
  writing any historical/backtest query — see its module docstring.
- `src/capint/adapters/` — source adapters. `sec_edgar.py` fetches and
  parses SEC Form 4 filings into plain records; adapters never touch the
  database (see `base.py` for why that boundary sits there).
- `src/capint/ingestion/` — turns adapter records into DB rows: entity
  resolution (get-or-create by external identifier) and idempotent
  persistence. `sec_form4.py` is the reference implementation.
- `src/capint/cli.py` — `python -m capint.cli ingest-form4` operational entry point.
- `src/capint/api/` — FastAPI app, versioned under `/api/v1`.
- `migrations/` — Alembic migrations.
- `tests/fixtures/synthetic.py` — synthetic-only fixture builders for the
  Phase 1 model tests, clearly labeled, never real financial data.
- `tests/fixtures/sec_form4/` — real (not synthetic) fixture data captured
  from one public Form 4 filing, used to test the SEC adapter/ingestion
  offline.

## Known limitations (Phase 2)

- Form 4 only — Form 3 (initial ownership) and Form 5 (annual/deferred) use
  the same XML schema and are a straightforward follow-on.
- Non-derivative (Table I) transactions only. Table II (options, RSUs,
  warrants) needs its own columns (strike price, underlying security,
  expiration) not yet modeled on `InsiderTransaction` — skipped and counted
  (`derivative_transactions_skipped`), not force-fit or dropped silently.
- Only EDGAR's "current filings" feed (most recent N across all filers) is
  wired up — a full historical backfill needs the quarterly full-index
  files and is a separate adapter method.
- No amendment (`4/A`) vs. original tracking — an amendment is ingested as
  its own transaction rather than reconciled against the filing it restates.
