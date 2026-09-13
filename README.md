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

## Ingest real SEC financial facts (capital allocation + fundamentals)

Also requires `SEC_EDGAR_USER_AGENT`:

```bash
python -m capint.cli ingest-financials --from-tracked --cik 0000320193
```

Structurally different from every other adapter: there is no "current
filings across the universe" feed for this data, so it's per-company —
`--cik` (repeatable) targets specific companies by CIK, `--from-tracked`
adds every CIK-identified company already in the database (from Form
4/13D-G ingestion; the two are merged, not either/or). One SEC XBRL
company-facts fetch per company feeds two things:

- **Capital allocation** (Phase 7): genuinely annual-duration 10-K figures
  only — `SHARE_BUYBACK`, `DIVIDEND_PAYMENT`, `DEBT_ISSUANCE`,
  `DEBT_REPAYMENT`. See `sec_xbrl.py`'s module docstring for why: cash-flow
  figures are reported year-to-date, not per discrete quarter (so
  quarterly figures are deliberately not derived), and a fact's `fp: "FY"`
  tag does NOT reliably mean "this fact spans the full fiscal year" (a
  10-K's own quarterly-data footnote uses it too) — an explicit duration
  check replaces trusting that flag.
- **Fundamentals** (Phase 8, spec §21): revenue, net income, diluted EPS,
  gross/operating margin, at BOTH quarterly and annual granularity —
  income-statement facts, unlike cash-flow ones, genuinely do carry a
  discrete-quarter duration in 10-Qs. `gross_margin_pct`/
  `operating_margin_pct` are plain arithmetic on the disclosed figures, not
  a score.

Both are per-fact/per-report idempotent, keyed on the earliest filing
that discloses each period's number (a later 10-K's comparative-year
table re-reports the same fact; only the first disclosure counts).

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
- `src/capint/adapters/sec_xbrl.py` / `src/capint/ingestion/sec_xbrl.py` —
  SEC XBRL company-facts adapter and ingestion, per-company (no
  universe-wide feed exists for this data) and the only adapter driven by
  an explicit CIK list rather than "recent filings". Covers both
  corporate capital-allocation (buybacks/dividends/debt, Phase 7) and
  fundamentals (revenue/earnings/margins, Phase 8) from one fetch per
  company. `canonicalize_facts`/`canonicalize_fundamental_facts` keep only
  the earliest disclosure of each distinct period's fact — see the
  module's docstrings for the real live-data bugs (comparative-year
  restatement; a later version wrongly forcing all of a period's metrics
  to come from one accession) that made this necessary.
- `src/capint/api/` — FastAPI app, versioned under `/api/v1`.
- `migrations/` — Alembic migrations.
- `tests/fixtures/synthetic.py` — synthetic-only fixture builders for the
  Phase 1 model tests, clearly labeled, never real financial data.
- `tests/fixtures/sec_form4/`, `tests/fixtures/sec_13f/`,
  `tests/fixtures/sec_13dg/`, `tests/fixtures/sec_xbrl/` — real (not
  synthetic) fixture data captured from real public filings, used to test
  each SEC adapter/ingestion offline.

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

## Known limitations (Phase 7)

- **Annual granularity only, by design.** Buyback/dividend/debt cash-flow
  figures are reported year-to-date within a fiscal year (a Q3 10-Q's
  total covers 9 months, not one quarter) — deriving a discrete quarterly
  number means subtracting consecutive YTD figures, which is fragile
  across fiscal-year boundaries and restatements. This adapter only
  ingests each fiscal year's final (10-K) total instead, at annual, not
  quarterly, resolution. See `sec_xbrl.py`'s module docstring.
- **No M&A, spinoffs, or secondary-offering detection yet.** Phase 7's
  roadmap line reads "capital allocation + buybacks + M&A", but M&A/
  spinoff *events* (as opposed to the buyback/dividend/debt *totals* this
  phase covers) are disclosed as 8-K narrative text and merger-agreement
  exhibits, not structured XBRL facts — detecting them properly needs the
  NLP/LLM layer the spec explicitly places later (§43, §74: "do not start
  with an LLM" until deterministic data infrastructure is solid). Building
  a brittle keyword-matcher now to approximate it was judged worse than
  leaving it out and saying so.
- **Coarser publication precision than every other adapter.** XBRL facts
  give a filing *date*, not a timestamp — `Event.publication_time` here is
  midnight UTC on that date, whereas Form 4/13F/13D-G all carry the exact
  SEC acceptance time.
- **No universe-wide discovery.** Unlike every prior adapter, there is no
  "recent filings" feed for this data — it only ever covers companies you
  explicitly point it at (`--cik`) or that some other adapter already
  found (`--from-tracked`). A company nobody has filed a Form 4, 13F, or
  13D/13G for yet gets no capital-allocation data even if one exists.
- **Two real bugs found via live validation (Apple's actual 22-year filing
  history), both fixed with regression tests before this shipped:**
  (1) a fact's `fp: "FY"` tag does not reliably mean its own duration
  spans a full fiscal year — a 10-K's "selected quarterly data" footnote
  tags each ~90-day quarter with `fp: "FY"` too, since `fp` describes the
  *filing's* period, not each individual fact's; fixed by checking each
  fact's actual start/end duration (330-380 days) directly. (2) An
  earlier version tried `PaymentsOfDividendsCommonStock` before falling
  back to `PaymentsOfDividends` as if they were interchangeable aliases —
  they are not: for the one period both report, they disagree
  ($11.965B vs. $12.150B), and the "preferred" tag only had 2 annual facts
  against the other's 34, so preferring it would have silently discarded
  32 real fiscal years of data. Fixed by using one fixed, standard concept
  per category with no automatic fallback — a company that only tags the
  non-standard variant simply gets no `DIVIDEND_PAYMENT` fact, which is
  conservative and honest rather than guessing which of two disagreeing
  numbers is "the" figure.
- Validated against Apple's complete real buyback/dividend/debt history
  (FY2013-FY2025, ~55 facts) end to end: zero duplicate periods, every
  canonical fact keyed to its earliest disclosure, confirmed idempotent,
  served through `/api/v1/capital-allocation` over HTTP.

## Known limitations (Phase 8)

- **Market and estimate context are deliberately NOT built.** The
  roadmap line reads "market/fundamental/estimate context", but only the
  fundamental third is here. Analyst estimates (consensus EPS/revenue,
  price targets, ratings) have no free, public, SEC-equivalent source —
  they're commercial data (FactSet, I/B/E/S, Zacks, etc.), and spec §62
  requires resolving licensing terms before integrating a source; none
  have been. Market microstructure (price, volume, short interest) is
  explicitly the spec's Phase 10, not Phase 8. Building either with a
  scraped or low-quality substitute was judged worse than leaving both out
  and saying so plainly, same reasoning as Phase 7's M&A omission.
- **No acceleration/deceleration detection or scoring yet** — spec §21
  asks for it, but this phase is ingestion + plain derived margins
  (arithmetic on disclosed figures), matching the ingestion-before-scoring
  split every prior phase pair has followed (Phase 2→3, Phase 4→5).
  Computing YoY/QoQ growth and margin trend is natural future work once
  this data exists to compute it from.
- **`Event.publication_time` here is the 10-Q/10-K filing date, not the
  earnings-release date** — the actual "results are out" moment is
  typically days-to-weeks earlier (an 8-K Item 2.02 or press release),
  neither of which this system ingests. A radar or convergence check
  built on this data would be measuring "when financials were formally
  filed", not "when the market first learned the numbers."
- **A period can have some metrics and not others** — a real, honest
  characteristic of the underlying data (confirmed live: Apple's own
  earliest XBRL-tagged fiscal years, FY2009-2015, have net income and EPS
  but no revenue under either concept this adapter recognizes), not a
  gap this phase tries to paper over.
- **A real bug found via live validation, fixed with a regression test
  before shipping:** an initial version of `canonicalize_fundamental_facts`
  picked one "canonical accession" per period and kept only that
  accession's metrics — wrong, because a period's earliest-filed
  accession for one metric isn't guaranteed to be the earliest (or even
  present) for every metric. That silently dropped real, correctly
  disclosed values (e.g. net income) whenever the accession happened to
  be missing an unrelated metric (e.g. revenue). Fixed by canonicalizing
  each metric independently, then regrouping by period.
- Validated against Apple's real fundamentals (FY2009-FY2025, ~83
  quarterly + annual reports) end to end: zero duplicate periods, correct
  partial-data handling for early years, computed margins matching hand
  calculation, served through `/api/v1/fundamentals` over HTTP.
