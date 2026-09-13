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

## Ingest real SEC Schedule 13D/13G filings

Also requires `SEC_EDGAR_USER_AGENT`:

```bash
python -m capint.cli ingest-13dg --days-back 7 --limit 100
```

Unlike Form 4/13F, SEC's legacy "current filings" feed doesn't index
13D/13G at all — this adapter discovers recent filings via EDGAR's
full-text-search API instead (see `sec_13dg.py`'s module docstring for how
that was confirmed, and for the two-different-schema handling: 13D and
13G are genuinely different XML shapes, not variants of one). Every
disclosure gets `stated_purpose` populated verbatim from a 13D's Item 4
narrative (`null` for 13G, which has no such item) — a fact the filer
wrote, never this system's interpretation. `SCHEDULE 13D` filings become
`ACTIVIST_STAKE` events, `SCHEDULE 13G` filings become
`MAJOR_HOLDER_CHANGE` events. Per-disclosure idempotent: safe to re-run.

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

## Institutional Radar

```
GET /api/v1/radar/institutional?window_days=365&baseline_lookback_days=1460&top_n=25&as_of=2026-06-01T00:00:00Z
```

Ranks companies by 13F institutional-accumulation score over the trailing
`window_days` (defaults to a year, since 13F is quarterly). Same shape as
the Insider Radar: four named, explained components
(magnitude-vs-own-history, breadth, consensus, new-money-share) plus
holding-level evidence. See
`src/capint/scoring/institutional_accumulation.py`'s module docstring for
what "accumulation" means here, including how a position increase's dollar
value is estimated (13F doesn't disclose a cost basis).

## Convergence (two-family)

```
GET /api/v1/radar/convergence?insider_window_days=90&institutional_window_days=365&top_n=25
```

For each company with an insider and/or institutional signal, returns
**both scores side by side** (never blended into one number — spec §33)
plus a label: `INSIDER_AND_INSTITUTIONAL_ACCUMULATING`,
`MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING`, `INSIDER_ONLY`, or
`INSTITUTIONAL_ONLY`. This is deliberately a two-family stand-in for the
spec's full multi-family Convergence Engine (§29) — see
`src/capint/convergence/engine.py`'s module docstring for exactly what
that does and doesn't mean yet.

## Test

```bash
pytest
```

Runs entirely offline against an in-memory SQLite engine and a mocked HTTP
transport (`tests/fixtures/sec_form4/`, `sec_13f/`, `sec_13dg/`, each built
from real, captured public filings — SEC filings are U.S. government
records, public domain under 17 U.S.C. §105). No test depends on SEC's
servers being reachable.

## Layout

- `src/capint/models/` — SQLAlchemy models: Entity/EntityIdentifier,
  Source/Document (provenance), Event, Company, Person/PersonCompanyRole,
  InsiderTransaction, InstitutionalManager/InstitutionalHolding,
  BeneficialOwnershipDisclosure.
- `src/capint/temporal.py` — point-in-time query helpers. Read this before
  writing any historical/backtest query — see its module docstring.
- `src/capint/adapters/` — source adapters. `sec_common.py` holds shared
  plumbing (rate-limited HTTP client, atom-feed/CIK helpers, namespace
  stripping) that `sec_edgar.py` (Form 4), `sec_13f.py` (13F-HR), and
  `sec_13dg.py` (Schedule 13D/13G) all build on; adapters never touch the
  database (see `base.py` for why that boundary sits there).
- `src/capint/ingestion/` — turns adapter records into DB rows: entity
  resolution (get-or-create by external identifier) and idempotent
  persistence. `sec_form4.py` is the reference implementation, and its
  `get_or_create_company`/`get_or_create_person` (both CIK-based) are
  reused directly by `sec_13dg.py` — issuer and individual-reporting-person
  resolution genuinely unifies across Form 4 and 13D/13G this way.
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
- `src/capint/scoring/anomaly.py` — shared "how unusual vs. own trailing
  history" math (median/MAD percentile+z-score) used by both insider and
  institutional scoring, so a third signal family reuses it rather than
  re-deriving it.
- `src/capint/scoring/institutional_accumulation.py` /
  `src/capint/radar/institutional_radar.py` — institutional-accumulation
  scoring and radar, mirroring Phase 3's insider modules.
- `src/capint/convergence/engine.py` — the two-family (insider +
  institutional) convergence check described above.
- `src/capint/adapters/sec_13dg.py` / `src/capint/ingestion/sec_13dg.py` —
  Schedule 13D/13G adapter and ingestion. Discovers filings via EDGAR
  full-text-search rather than the atom "current filings" feed (which
  doesn't index these forms at all — see the adapter's module docstring).
- `src/capint/api/` — FastAPI app, versioned under `/api/v1`.
- `migrations/` — Alembic migrations.
- `tests/fixtures/synthetic.py` — synthetic-only fixture builders for the
  Phase 1 model tests, clearly labeled, never real financial data.
- `tests/fixtures/sec_form4/`, `tests/fixtures/sec_13f/`,
  `tests/fixtures/sec_13dg/` — real (not synthetic) fixture data captured
  from real public filings, used to test each SEC adapter/ingestion
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

## Known limitations (Phase 5)

- **Same thin-baseline effect as Phase 3, worse.** Because Phase 4 has no
  historical backfill, most companies' "trailing windows" baseline is
  literally all-zero (no prior quarters ingested at all) rather than
  merely thin. A single accumulating quarter against an all-zero baseline
  computes as the 100th percentile by construction — technically correct
  ("unusual relative to what we've observed") but easy to over-read as
  "extreme" when it may just mean "the second quarter we've ever ingested."
  Fix is the same as Phase 3's: a real historical-backfill adapter.
- **An increase's dollar value is estimated, not disclosed.** 13F reports
  a position's total shares and total value each quarter, not a cost basis
  for the incremental shares — `institutional_accumulation.py` estimates it
  as `shares_change * (market_value_usd / shares_held)`, i.e. this
  quarter's implied per-share price applied to the added shares. Exactly
  right for a brand-new position; an approximation for an addition.
- **The convergence check is two families, not the spec's full engine.**
  Insider + institutional only. Adding a third family (e.g. ETF flows)
  will need real signal-independence handling (spec §30-31, "don't
  double-count correlated signals") that doesn't exist yet — safe to skip
  today only because Form 4 and 13F are genuinely independent filings.
- **Live-validated the mechanism, not a live convergent example.** Ingesting
  ~50 real Form 4 filings and 15 real 13F filings produced zero real
  companies with both an insider and institutional signal in the same
  window (5 insider candidates vs. 1,073 institutional candidates, no
  overlap) — expected at this ingestion scale, not a bug. The
  `INSIDER_AND_INSTITUTIONAL_ACCUMULATING` and
  `MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING` code paths are fully
  exercised by `tests/test_convergence_engine.py`'s synthetic scenarios.
- Component weights are still an unfit heuristic (same caveat as Phase 3),
  and institution-quality weighting (spec §14) still doesn't exist.

## Known limitations (Phase 6)

- **Amendments aren't reconciled** — `SCHEDULE 13D/A` and `SCHEDULE 13G/A`
  are excluded from ingestion entirely, same as every other adapter here.
  A material change disclosed only via an amendment (a stake increase, a
  changed purpose) won't be captured until amendment-reconciliation exists
  for any of these adapters.
- **A CIK-less reporting person (a trust, common in joint 13D filings) is
  deduplicated by exact name match only** — no identifier, so a trust
  named slightly differently across two filings becomes two Entity rows.
  Documented in `sec_13dg.py` and `sec_13dg` ingestion module docstrings
  as a deliberately coarse, best-effort resolution.
- **No activist-vs-passive scoring or campaign tracking yet.** This phase
  is ingestion + a bare event vocabulary (ACTIVIST_STAKE /
  MAJOR_HOLDER_CHANGE) — spec §15's "detect campaign, board pressure,
  strategic review pressure" is future work once enough disclosure history
  exists to reason about change over time, not a single filing.
- **A real bug found via live validation, fixed and covered by a
  regression test:** `get_or_create_person` (Phase 2 code, reused here)
  had no fallback for "a CIK already resolves to an Entity, but that
  Entity has no Person profile" — exactly the situation this phase
  introduced (a reporting person first seen as a non-individual, later
  confirmed to be a person). Without the fix, a live batch of 45 real
  filings hit `MultipleResultsFound` on the second sighting of an affected
  CIK. Also found and fixed: EDGAR's full-text-search API returns one hit
  per *document*, not per filing, so an unrelated exhibit could make one
  accession look like two — `fetch_recent_filings` now deduplicates by
  accession number, the same pattern the atom-feed adapters already used.
- Validated against 45 real filings (124 disclosures: individuals,
  corporations, and joint filings all correctly typed and attributed);
  confirmed idempotent on re-run; served back through
  `/api/v1/ownership-disclosures` over HTTP.
