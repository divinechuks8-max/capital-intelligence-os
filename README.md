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

## Ingest real SEC 13F-HR filings

Also requires `SEC_EDGAR_USER_AGENT`:

```bash
python -m capint.cli ingest-13f --count 100
```

Pulls the most recent N entries from EDGAR's current-filings feed (exact
form type `13F-HR`, amendments excluded), parses each filing's cover page
(filer identity, period of report) and information table (one row per
security, aggregated by CUSIP), resolves/creates the `InstitutionalManager`
by CIK and each `Company` by CUSIP, and persists one `InstitutionalHolding`
per (institution, company, quarter) — classified NEW / INCREASED /
DECREASED / UNCHANGED / EXITED by diffing against that institution's prior
quarter. Whole-filing idempotent: safe to re-run.

## Insider Radar

```
GET /api/v1/radar/insider?window_days=90&baseline_lookback_days=730&top_n=25&as_of=2026-06-01T00:00:00Z
```

Ranks companies by discretionary open-market insider-buying conviction over
the trailing `window_days`, as of `as_of` (defaults to now). Every entry
carries its four score components (size-vs-own-history, breadth,
persistence, discretion) each with a plain-English explanation, plus the
underlying transaction evidence — there is no bare number without a "why"
(spec §69). See `src/capint/scoring/insider_conviction.py`'s module
docstring for exactly what "conviction" means and doesn't mean here.

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
- `src/capint/scoring/insider_conviction.py` — insider-conviction scoring:
  discretionary-purchase filtering, historical-anomaly comparison
  (median/MAD over trailing windows, honest about insufficient history),
  and named, explained score components. Computed on demand — no
  `signals` table yet (see Known limitations).
- `src/capint/radar/insider_radar.py` — finds candidate companies and ranks
  them by conviction score.
- `src/capint/adapters/sec_13f.py` / `src/capint/ingestion/sec_13f.py` —
  13F-HR institutional-holdings adapter and ingestion, mirroring the Form 4
  split. Resolves companies by CUSIP (not CIK — see Known limitations).
- `src/capint/api/` — FastAPI app, versioned under `/api/v1`.
- `migrations/` — Alembic migrations.
- `tests/fixtures/synthetic.py` — synthetic-only fixture builders for the
  Phase 1 model tests, clearly labeled, never real financial data.
- `tests/fixtures/sec_form4/` and `tests/fixtures/sec_13f/` — real (not
  synthetic) fixture data captured from one public filing each, used to
  test the SEC adapters/ingestion offline.

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

## Known limitations (Phase 3)

- Because Phase 2 only ingests EDGAR's "current filings" feed (no
  historical backfill), every real company's trailing-window baseline is
  currently thin-to-empty. The scoring engine is honest about this (see
  `confidence_notes` / a `None` `size_vs_history` component below the
  minimum bucket count) rather than presenting a false-precision anomaly
  score — but in practice, real radar entries today will mostly show
  "not enough history" until a backfill adapter exists.
- Conviction-score component weights (0.35/0.30/0.15/0.20) are a
  documented starting heuristic, not fit to realized outcomes — spec §48
  ("who is right?") is the later phase that would validate or revise them.
- Officer/director/10%-owner role is read from the *current*
  `PersonCompanyRole` row, not a point-in-time snapshot as of the
  transaction — a person who left the board since would still show their
  latest known role.
- Scores are computed on demand, not persisted to a `signals` table —
  there's no history of how a company's score changed over time yet
  (needed for the later Historical Analog Engine).
- Only insider *buying* is scored (per the MVP definition, spec §82).
  Insider selling deliberately has no symmetric "bearish" score yet — spec
  §84 warns against treating selling as automatically bearish, and building
  that fairly needs its own reasoning (tax-driven sales, diversification,
  10b5-1 plans, etc.), not a mirrored version of this module.

## Known limitations (Phase 4)

- **13F resolves companies by CUSIP; Form 4 resolves them by CIK — nothing
  cross-links the two.** The same real company can end up as two different
  `Entity` rows depending on which adapter saw it first. Fixing this needs
  a maintained CUSIP<->CIK/ticker mapping, not built yet (documented in
  `capint/ingestion/sec_13f.py`'s module docstring, spec §5).
- 13F's `value` field is trusted as reported (whole USD) rather than
  cross-checked per filer — see `sec_13f.py`'s docstring for how that
  convention was verified against a real filing; a filer whose software
  still uses the legacy in-thousands convention would have its holdings
  understated ~1000x, undetected.
- Only exact form type `13F-HR` is ingested — `13F-HR/A` amendments and
  `13F-NT` (notice, no holdings) are skipped, same amendment-tracking gap
  as Form 4.
- Joint filings attribute every row to the cover page's filing manager,
  not the specific `otherManager` sub-filer named on each line — a coarser
  attribution than the source data supports.
- "Only EDGAR's current-filings feed" limitation applies here too: no
  historical backfill yet, so most institutions will show a single
  quarter with `position_status=NEW` until a second quarter is ingested.
- Institutional-holdings scoring (an "institutional accumulation" signal
  analogous to Phase 3's insider conviction) is not built yet — Phase 4 is
  ingestion only, matching the roadmap's Phase 4/5 split.
