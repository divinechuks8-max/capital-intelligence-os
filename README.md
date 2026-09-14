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

## Ingest real SEC fund/ETF AUM data (Form N-PORT)

Also requires `SEC_EDGAR_USER_AGENT`:

```bash
python -m capint.cli ingest-nport --cik 0000884394 --filing-count 8
```

Per-fund (`--cik`, repeatable — no `--from-tracked` here; funds aren't
reliably discoverable from the companies this system already tracks).
Tracks fund/ETF **assets under management**, not **flows** — confirmed
live, against SPY (SPDR S&P 500 ETF Trust, one of the largest ETFs in the
world) before building this: its real N-PORT filings have no
shares-outstanding figure anywhere, which a true flow calculation (net
creation/redemption, isolated from market price movement) needs.
`net_assets_change_usd` is a plain quarter-over-quarter dollar delta, not
an isolated flow figure — see `capint/models/fund.py`'s docstring. N-PORT
is also quarterly with a ~60-day disclosure lag, a hard ceiling on
granularity regardless of implementation — spec §17's 1-day/5-day/20-day
flow cadence isn't achievable from this source at any quality. Sector
rotation (spec §18) isn't attempted either — it needs either true flow
isolation or a sector taxonomy this phase doesn't build. Per-snapshot
idempotent (each N-PORT is its own distinct point-in-time report, so no
comparative-restatement canonicalization is needed here, unlike Phase 7/8).

## Ingest real FINRA short interest

Uses the same identifying User-Agent convention as the SEC adapters
(`SEC_EDGAR_USER_AGENT`), even though this hits FINRA, not SEC — reusing
one setting for "an identifying contact string this system sends" rather
than inventing a second FINRA-specific env var for what is the same kind
of value:

```bash
python -m capint.cli ingest-short-interest --ticker AAPL --num-cycles 3
```

Per-ticker (`--ticker`, repeatable — no `--from-tracked`; this system has
no reliable way to map its CIK-keyed companies to a *current* ticker
without point-in-time identifier validation, which is out of scope here).
Resolves companies by bare ticker, not CIK/CUSIP — FINRA's consolidated
short interest feed carries no issuer CIK at all. `change_percent` and
`change_quantity` are reported directly by FINRA alongside the position
itself (unlike Phase 9's `net_assets_change_usd`, which this system
computes from two snapshots).

FINRA reports short interest bi-monthly, on settlement dates that fall
near the 15th and the last calendar day of each month, shifted around
holidays/weekends. FINRA's API requires an exact `settlementDate` match
(it's a partition key, not a sortable/browsable column), so this adapter
probes a window of candidate dates newest-first and keeps whichever ones
the live API actually confirms — verified against real data: 2026-08-31,
2026-08-14, and 2026-07-31 all returned real settlement data, while
2026-08-29 (a plausible mid-month guess) returned HTTP 204 and was
correctly skipped.

## Ingest real UK beneficial-ownership disclosures (Companies House PSC)

Requires a free `COMPANIES_HOUSE_API_KEY` — self-service signup at
[developer.company-information.service.gov.uk](https://developer.company-information.service.gov.uk/),
create a "Live" REST application, generate a key (instant, no approval wait):

```bash
python -m capint.cli ingest-uk-psc --company-number 05151321
```

Per-company (`--company-number`, repeatable — Companies House number, not
a ticker — no `--from-tracked`; this system's existing companies are
resolved by CIK/CUSIP, not Companies House numbers). Ingests the UK's
Persons with Significant Control (PSC) register — the closest UK
equivalent of Schedule 13D/13G (Phase 6), though a structurally different
regime: categorical ownership/voting-rights *bands* (e.g.
`voting-rights-25-to-50-percent`), never an exact percentage.

**A real regulatory fact, confirmed live before building this**: UK
companies whose shares trade on a "regulated market" (the LSE Main
Market — Diageo, Barclays, GSK, Rolls-Royce, ...) are exempt from the PSC
regime by law (Companies Act 2006, Sch 1A) and return **zero** PSC
records — confirmed against all four of those. Their major-holder
disclosures instead happen via the FCA's DTR5 regime, distributed through
RNS (the London Stock Exchange's Regulatory News Service). This system
does **not** ingest that data: the only realistic free aggregator found
during research (investegate.co.uk) has Terms of Use that explicitly
prohibit redistributing or processing its content for anything like this
system's purpose, so it was deliberately not scraped, per this project's
legally-accessible-sources-only mandate. PSC data therefore ends up
populated mainly for AIM-listed and smaller UK companies — confirmed live
against Frontier Developments plc and Angling Direct plc (both AIM),
which do have real PSC records, including individual and corporate PSCs
and both active and historical (ceased) ones.

## Ingest real on-chain Bitcoin wallet activity

No API key needed — blockchain.info is free and unauthenticated:

```bash
python -m capint.cli ingest-crypto-treasury --address 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa --limit 50
```

Per-address (`--address`, repeatable — no `--from-tracked`; this system
has no mechanism linking a wallet address to a company/person it already
tracks). Tracks each transaction's net effect on one explicitly-provided
Bitcoin address's balance. **Deliberately narrow scope, and no wallet-
owner attribution is stored or claimed**: the wallet entity's name is
simply its raw address. This does not attempt "whale accumulation" or
"exchange flow" detection (`EventType` also declares
`WHALE_ACCUMULATION`/`EXCHANGE_FLOW`/`TOKEN_UNLOCK`) — those need a
labeled address database (which addresses belong to which exchange, fund,
or whale) with no free, legal source found during research; the realistic
providers (Nansen, Arkham, Chainalysis) are paid/licensed. Ethereum/
ERC-20 support would need its own free Etherscan API key (confirmed live:
unlike blockchain.info, Etherscan's v2 API requires one) — not registered
in this phase.

## Ingest real SEC guidance-relevant 8-K disclosures

Also requires `SEC_EDGAR_USER_AGENT`:

```bash
python -m capint.cli ingest-guidance --cik 0000320193 --filing-count 20
```

Per-company (`--cik`, repeatable). Flags 8-K filings tagged with Item
2.02 ("Results of Operations and Financial Condition" — routine quarterly
earnings, sometimes containing new guidance) or Item 7.01 ("Regulation FD
Disclosure" — the more common vehicle for a standalone guidance
announcement), confirmed live via `data.sec.gov/submissions`'s own
per-filing `items` field — no full-text search or document parsing
needed. **Deliberately does not parse the press-release exhibit or
extract any guidance direction/magnitude** — it stores which item(s) were
disclosed and a link to the filing, an OBSERVATION that a guidance-
relevant disclosure happened, not this system's INTERPRETATION of what it
says. A human (or a future NLP-based phase) has to read the filing.

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

## Short Interest Radar

```
GET /api/v1/radar/short-interest?lookback_cycles=12&top_n=25&as_of=2026-06-01T00:00:00Z
```

Ranks companies by FINRA short-interest acceleration (Phase 13). **Only
rising short interest is scored** — short covering (a falling cycle) is a
different, real signal this radar doesn't evaluate, mirroring how
Insider/Institutional Radar only score buying/accumulation. Three named
components (magnitude-vs-own-history, days-to-cover-vs-own-history,
persistence) plus the underlying settlement-cycle evidence. See
`src/capint/scoring/short_interest_acceleration.py`'s module docstring for
exactly what "acceleration" means here.

## Convergence (three-family)

```
GET /api/v1/radar/convergence?insider_window_days=90&institutional_window_days=365&short_interest_lookback_cycles=12&top_n=25
```

For each company with an insider, institutional, and/or short-interest
signal, returns **every score side by side** (never blended into one
number — spec §33) plus a label. The label still characterizes only the
insider/institutional relationship (unchanged since Phase 5) —
`INSIDER_AND_INSTITUTIONAL_ACCUMULATING`,
`MIXED_INSIDER_BUYING_INSTITUTIONAL_SELLING`, `INSIDER_ONLY`,
`INSTITUTIONAL_ONLY`, or (Phase 13) `SHORT_INTEREST_ONLY` for a company
with no insider/institutional signal at all. `short_interest_score` is
always available independently regardless of label. This is deliberately
a three-family stand-in for the spec's full multi-family Convergence
Engine (§29) — see `src/capint/convergence/engine.py`'s module docstring
for exactly what that does and doesn't mean yet.

## Alerts

```
python -m capint.cli create-alert-rule --name "High insider conviction" --rule-type INSIDER_CONVICTION_THRESHOLD --min-score 75
python -m capint.cli create-alert-rule --name "Convergent buying" --rule-type CONVERGENCE_LABEL --convergence-label INSIDER_AND_INSTITUTIONAL_ACCUMULATING
python -m capint.cli create-alert-rule --name "High insider conviction (Slack)" --rule-type INSIDER_CONVICTION_THRESHOLD --min-score 75 --webhook-url "https://hooks.slack.com/services/..." --webhook-format SLACK
python -m capint.cli evaluate-alerts
```

```
GET /api/v1/alert-rules
GET /api/v1/alerts?rule_id=...&company_entity_id=...
```

The pipeline's ALERT stage (Phase 13) — persists which companies crossed
a configured threshold as of an evaluation run, rather than requiring a
consumer to recompute and compare radar/convergence output themselves
every time. Rules are user configuration created via the CLI (this
system's API is read-only everywhere else); `evaluate-alerts` reuses the
existing radar/convergence modules untouched and simply checks their
output against each active rule. Deduplicated per (rule, company,
calendar date) — re-running the same day is a no-op, but a later day's
evaluation logs a fresh row if the signal still triggers. Every alert
carries `source_event_ids` linking back to the real evidence behind the
triggering score — never a bare number without a why.

**Delivery (Phase 16)** is webhook-only, deliberately. `--webhook-url` is
a plain outbound HTTP endpoint the user supplies and controls (their own
server, their own Slack workspace's Incoming Webhook, a webhook-to-email
bridge, etc.) — this system never holds a third-party notification-
service account or API key of its own, so there is no vendor Terms of
Service to evaluate (the lesson from the Alpha Vantage/Finnhub/Etherscan
removals in "Known limitations (Phase 15)"). `--webhook-format GENERIC`
(default) POSTs this system's own structured JSON payload;
`--webhook-format SLACK` POSTs Slack's documented Incoming Webhook shape
(`{"text": "..."}`) so a rule can point directly at a Slack workspace's
own webhook URL with no separate Slack API integration. Delivery is a
single, synchronous, best-effort POST attempt made at the moment an
alert is created — no retry queue or delivery guarantee — recorded on
the `Alert` row as `delivery_attempted`/`delivery_succeeded`/
`delivery_error` (all served back via `GET /api/v1/alerts`; the Alert
row itself, not delivery, is the durable record of what fired).
`webhook_url` is deliberately never served back by `GET
/api/v1/alert-rules` — only `webhook_configured` (bool) and
`webhook_format` — since a webhook URL such as Slack's typically embeds
a bearer token in its path. Live-validated against a real endpoint
(`https://httpbin.org/post`, confirmed `delivery_succeeded=True` on real
alerts generated from real ingested SEC Form 4 data) and against a real
failing endpoint (`https://httpbin.org/status/500`, confirmed
`delivery_succeeded=False` with `delivery_error="HTTP 500"` recorded
without crashing evaluation). Email delivery was considered and not
built: it would need either user-supplied SMTP credentials (a real,
viable future increment) or a chosen transactional-email API vendor
whose Terms of Service haven't been reviewed — a user who wants email
today can point `--webhook-url` at any webhook-to-email bridge they
choose under their own account and terms.

## Backtesting

```bash
python -m capint.cli backtest-short-interest --holding-trading-days 10
```

```
GET /api/v1/backtest/short-interest?holding_trading_days=10
```

The pipeline's HISTORICAL VALIDATION stage (Phase 13) — reports what
actually followed a real rising-short-interest signal, using real daily
price bars (`capint.models.price.PriceBar`). **Plain descriptive
statistics only, never a trading signal or investment advice.**
**Currently has no working ingestion path** — Phase 13's original price
source (Alpha Vantage) was removed in Phase 15 after its actual Terms of
Service turned out to prohibit this platform's architecture; see "Known
limitations (Phase 15)" below. This command and endpoint remain real,
tested, working code — they'll simply report zero signals with
computable forward returns until a compliant price source is ingested.

## Ingest real M&A corporate-action disclosures

Also requires `SEC_EDGAR_USER_AGENT`:

```bash
python -m capint.cli ingest-corporate-actions --cik 0000789019 --filing-count 20
```

Per-company (`--cik`, repeatable). Flags 8-K filings tagged with Item
2.01 ("Completion of Acquisition or Disposition of Assets"), confirmed
live via `data.sec.gov/submissions`'s own per-filing `items` field — same
mechanism as Phase 12's guidance disclosures, no full-text search or
document parsing needed. Item 1.01 ("Entry into a Material Definitive
Agreement") is deliberately excluded — it covers ordinary commercial
contracts far more often than signed-but-not-yet-closed merger
agreements, and including it would trade real specificity for recall
this increment doesn't need. **Deliberately does not parse the filing
exhibit or extract deal terms, consideration, or counterparty identity**
— an OBSERVATION that a completed acquisition/disposition was disclosed,
not this system's INTERPRETATION of what it says.

## Ingest real Cboe volatility index history

No API key needed:

```bash
python -m capint.cli ingest-volatility-index --index-code VIX
```

```
GET /api/v1/volatility-index?index_code=VIX
```

Ingests the full daily history of a Cboe volatility index (VIX by
default — the "fear index," calculated from S&P 500 index option
prices). **Chosen over per-security unusual-options-activity or a real
put/call ratio, which remain genuinely gated**: confirmed live during
research that Cboe's own market-statistics pages now show sign-in/
subscription indicators, and granular options-level data is explicitly
sold via Cboe DataShop. This endpoint (`cdn.cboe.com/api/global/
us_indices/daily_prices/{CODE}_History.csv`) is different — Cboe's own
site describes it as public, "Updated Daily" data, distinct from
DataShop. No free-tier depth limit either (unlike Phase 13's Alpha
Vantage price data): the complete history (VIX: 1990 to present, ~9,270
real trading days) is available in one request. Other Cboe indices work
the same way (`--index-code VVIX`, `--index-code SKEW`, ...). Not tied to
any Company/Entity — see `src/capint/models/volatility.py`'s module
docstring for why.

## Analyst recommendation trends

```
GET /api/v1/analyst-recommendations?ticker=AAPL
```

**Currently has no working ingestion path.** Phase 14 originally built
one against Finnhub's free tier, which the API docs suggested was
accessible; Phase 15 removed it after reading Finnhub's actual Terms of
Service, which restrict the free tier to personal use and prohibit
redistribution — the same problem as Alpha Vantage's price data (see
"Known limitations (Phase 15)"). The model and this read-only endpoint
remain real, tested, working code, ready for a compliant source.

## Relationships (interlocking directorates)

```
GET /api/v1/relationships/interlocking-directorates?company_entity_id=...
```

The pipeline's RELATIONSHIPS layer (Phase 15) — companies connected by a
shared Person holding an officer/director/10%-owner role at both,
derived entirely from real Form 4 data already in this system (Phase 2).
No new external data source, and so no new licensing risk. Omit
`company_entity_id` for the full graph currently in this system; pass it
to scope to one company's interlocks. Computed on demand, like the radar/
convergence endpoints, not persisted or point-in-time gated — see
`src/capint/relationships/engine.py`'s module docstring for why (Form 4
doesn't disclose when a role formally ends) and for why a "common
institutional ownership" relationship type was considered and
deliberately not built (a large index-fund holder connects nearly every
public company to nearly every other one — noise, not a meaningful
relationship, without a materiality threshold this increment doesn't
build).

## News sentiment

No API key needed:

```bash
python -m capint.cli ingest-news-sentiment --ticker AAPL --query "Apple Inc" --timespan 7d
```

```
GET /api/v1/news-sentiment?company_entity_id=...
```

Real news-tone (sentiment) distributions from the GDELT Project's free
DOC 2.0 API (`api.gdeltproject.org`) — confirmed live to require no API
key or registration, and GDELT's own Terms of Use explicitly permit
"academic, commercial, or governmental use of any kind without fee" and
redistribution "in any form" (attribution required) — the opposite
finding from Alpha Vantage, Twelve Data, Finnhub, Polygon.io, and
Etherscan, all checked and found to restrict free-tier use to personal
purposes only (see "Known limitations (Phase 15)"). `--query` is the
actual GDELT search string, kept deliberately separate from `--ticker`
(used only for entity resolution) — searching by bare ticker symbol
(e.g. "F" for Ford) would return near-random results. **GDELT has no
concept of "company"**, only full-text search over global news — treat
this as directional sentiment context, not a precise per-company signal.
`tone_distribution` is the raw bin/count histogram GDELT returns, never
collapsed to a single opaque score.

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
- `src/capint/adapters/sec_nport.py` / `src/capint/ingestion/sec_nport.py`
  — Form N-PORT fund/ETF AUM adapter and ingestion, per-fund (another
  explicit-CIK-only adapter). Discovers filings via
  data.sec.gov/submissions rather than XBRL company-facts — N-PORT isn't
  a us-gaap document — which conveniently also gives exact acceptance
  timestamps and the fund's ticker in one response.
- `src/capint/api/` — FastAPI app, versioned under `/api/v1`.
- `migrations/` — Alembic migrations.
- `tests/fixtures/synthetic.py` — synthetic-only fixture builders for the
  Phase 1 model tests, clearly labeled, never real financial data.
- `tests/fixtures/sec_form4/`, `tests/fixtures/sec_13f/`,
  `tests/fixtures/sec_13dg/`, `tests/fixtures/sec_xbrl/`,
  `tests/fixtures/sec_nport/` — real (not synthetic) fixture data captured
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

## Known limitations (Phase 9)

- **This tracks AUM, not flow — a deliberate, load-bearing scope
  reduction, not a partial attempt at flow.** The roadmap line reads
  "ETF/fund flows + sector rotation." True flow (net creation/redemption,
  isolated from market price movement) needs a shares-outstanding figure
  that real N-PORT filings simply don't reliably expose — confirmed live
  against SPY, one of the largest, most liquid ETFs in the world, before
  any of this was built: its N-PORT has total/net assets but no
  shares-outstanding-by-class anywhere in the document. `net_assets_change_usd`
  is an honest dollar delta that conflates flow with return, clearly
  labeled as such rather than presented as isolated flow.
- **Sector rotation (spec §18) is not attempted.** It needs either true
  flow isolation (which doesn't exist, see above) or a sector/industry
  taxonomy mapping each fund's holdings to a classification, neither of
  which this phase builds.
- **Quarterly, ~60-day-lagged granularity is a hard ceiling of the source
  data**, not an ingestion limitation — spec §17's 1-day/5-day/20-day/
  60-day flow cadence isn't achievable from N-PORT at any implementation
  quality.
- **No fund discovery mechanism** — unlike every ingestion-phase adapter
  before it, there's no `--from-tracked` convenience; a fund's CIK isn't
  reliably inferable from the companies this system already tracks (13F
  resolves ETF positions as generic CUSIP-keyed "companies", not funds),
  so `--cik` must be supplied explicitly per fund.
- **A real bug found via live validation, fixed before shipping:**
  `ingest_fund_snapshot` added each `FundAumSnapshot` without flushing the
  session — with `autoflush=False`, a fund's second (and every later)
  snapshot in the same ingestion run couldn't see the immediately
  preceding one via `_previous_snapshot`'s query, so
  `net_assets_change_usd` stayed `None` past the very first period.
- **Doesn't parse portfolio holdings at all** — N-PORT's `invstOrSecs`
  section (every position the fund holds) is fetched as part of the same
  document but deliberately not read in this phase; a future phase could
  use it for genuine holdings-based sector/thematic classification.
- Validated against SPY's real assets under management (8 real quarters,
  2024-2026, ~$591B to ~$781B): zero duplicate periods, `net_assets_change_usd`
  correctly `None` for the first observed period and a real dollar delta
  thereafter, confirmed idempotent, served through `/api/v1/fund-aum`
  over HTTP.

## Known limitations (Phase 10)

- **Resolved by ticker alone, not CIK/CUSIP/ISIN — the first adapter in
  this system to do so, and exactly what spec §5 warns against relying on
  solely.** FINRA's consolidated short interest feed carries no issuer CIK
  at all, only a ticker symbol (`symbolCode`). This is a real, load-bearing
  limitation: tickers are reassigned over time, and a stale or reused
  ticker could silently resolve to the wrong company.
- **No point-in-time ticker validity checking.** `EntityIdentifier` has
  `valid_from`/`valid_to` columns in the schema specifically for this, but
  `get_or_create_company_by_ticker` does not consult them — it takes the
  first matching TICKER identifier regardless of when it was valid. A
  future phase should add point-in-time-aware ticker resolution before
  this data is used for anything decision-relevant across a long history.
- **Settlement-date discovery is candidate-probing, not a computed
  calendar.** FINRA short interest settlement dates fall near the 15th and
  last calendar day of each month (per Rule 4560) but shift for
  weekends/holidays in a way this adapter does not compute exactly (that
  would need a full market holiday calendar). Instead it generates a
  window of plausible dates and probes each live, keeping whichever the
  API actually confirms. Confirmed live: 2026-08-31, 2026-08-14, and
  2026-07-31 are real settlement cycles; 2026-08-29, a plausible
  mid-month guess, returned HTTP 204 and was correctly skipped rather than
  treated as an error.
- **No `--from-tracked` convenience**, same reasoning as Phase 9's fund
  CIKs: this system has no reliable mapping from its existing CIK-keyed
  companies to a current ticker, so `--ticker` must be supplied explicitly.
- **No daily short *volume* (FINRA's separate `CNMSshvolYYYYMMDD.txt`
  feed, confirmed free/public during research for this phase) — only the
  bi-monthly *position* data is ingested.** Daily volume is a much higher
  cadence, differently-shaped dataset (per-venue daily totals, not a
  point-in-time position with a reported percent change) that would
  warrant its own adapter, not a bolt-on to this one.
- **No short-interest scoring/radar yet** — this phase only ingests, per
  the same ingest-now/score-later split as Phases 2→3 and 4→5. A future
  phase could compute an acceleration/momentum score from the time series
  this ingests (3+ settlement cycles per ticker).
- A self-contained `_RateLimitedFinraClient` was written instead of
  reusing/renaming `capint.adapters.sec_common.RateLimitedSecClient` — a
  deliberate choice, since FINRA is a different organization from SEC with
  its own (lighter, no-registration) access policy, and a broader rename
  refactor across five existing SEC-adapter files wasn't justified for
  this need.
- Validated against real, live FINRA data end to end: AAPL's confirmed
  real short position rose from 116,327,753 to 139,749,097 shares
  (+20.13%, matching FINRA's own reported `changePercent` exactly) between
  the 2026-08-14 and 2026-08-31 settlement dates, with `days_to_cover`
  correctly at 3.53; a second ticker (MSFT) ingested in the same run to
  confirm multi-ticker batching; re-running ingestion confirmed fully
  idempotent (0 new snapshots, all skipped as duplicates); served
  correctly through `/api/v1/short-interest` over HTTP, including
  ticker-filtered queries.

## Known limitations (Phase 11)

- **Empty for LSE Main Market-listed companies — a real legal exemption,
  not a gap in this system.** Companies whose voting shares trade on a
  "regulated market" (Companies Act 2006, Sch 1A) don't have to disclose
  PSCs at all. Confirmed live: Diageo, Barclays, GSK, and Rolls-Royce all
  return zero PSC records. Their real major-holder disclosures happen
  under the FCA's DTR5 regime instead, distributed via RNS.
- **DTR5/RNS major-holder and PDMR director-dealing disclosures (the
  closer UK analogues of Phase 6's 13D/13G and Phase 2's Form 4) are
  deliberately not ingested.** The FCA does not run a filing repository
  like SEC EDGAR for these — they're distributed through commercial
  newswires. The only realistic free aggregator found during research,
  investegate.co.uk, has Terms of Use that explicitly prohibit
  redistributing or processing its content ("Does not distribute,
  republish or otherwise provide any information or derived works to any
  third party... or use or process information or derived works for any
  commercial purposes") — exactly what this system does, so it was
  deliberately not scraped, per the legally-accessible-sources-only
  mandate. This is a real content gap for UK insider dealing and major
  shareholder data, not something a different implementation approach
  could have closed with free data.
- **No point-in-time identity resolution across companies for individual
  (or non-UK-registered) PSCs.** Companies House gives no identifier for
  a PSC that is stable *across* different companies — each PSC's
  `links.self` path is scoped to one (company, PSC) relationship only. So
  the same real individual serving as PSC of two different UK companies
  resolves to two different Entity rows here. A **corporate** PSC
  registered in the UK is the one exception: it carries a real Companies
  House registration number, so it resolves through the same Company path
  any tracked issuer would use — confirmed live (Gresham House Asset
  Management Ltd, a real corporate PSC of Angling Direct plc, is
  reachable via its own `UK_COMPANY_NUMBER` identifier).
- **Cannot distinguish "PSC-exempt" from "genuinely has no PSC to
  report."** Companies House exposes a separate
  `persons-with-significant-control-statements` endpoint for that
  distinction; this adapter does not call it, so a company with zero PSC
  records could mean either.
- **No officer/director ingestion.** Companies House's `/officers`
  endpoint (confirmed live, real appointment dates and a genuinely
  cross-company-stable officer ID) would be a natural next increment for
  UK insider identification, but is out of scope for this phase to keep
  it focused on the disclosure-equivalent data.
- Validated against real, live Companies House data end to end: Diageo
  plc (00023307, LSE Main Market) confirmed with zero PSC records;
  Angling Direct plc (05151321, AIM) ingested with 4 real PSC records
  (1 active corporate PSC, 1 ceased corporate PSC, 2 ceased individual
  PSCs); Frontier Developments plc (02892559, AIM) ingested with 1 real
  active individual PSC (its founder, Dr David Braben); re-running
  ingestion confirmed fully idempotent (0 new records, all skipped as
  duplicates); an unknown company number handled as a per-company error
  without aborting the batch; served correctly through `/api/v1/uk-psc`
  over HTTP.

## Known limitations (Phase 12)

Phase 12 covers two independent extensions (crypto on-chain tracking and
analyst/guidance signals) rather than one topic — each is scoped and
documented separately below.

**Crypto (on-chain Bitcoin wallet tracking):**

- **No "whale accumulation" or "exchange flow" detection, despite
  `EventType` declaring both.** Both need a labeled address database
  (which addresses belong to which exchange, fund, or whale) — no free,
  legal source was found during research; the realistic providers
  (Nansen, Arkham, Chainalysis) are paid/licensed. Guessing a label from
  heuristics alone would be exactly the kind of unverified attribution
  this project's spec prohibits.
- **No wallet-owner attribution at all** — not even for the "treasury"
  framing in this event type's name. A wallet entity's `canonical_name`
  is simply its raw address; this system never claims a specific
  company/fund/person owns any address it tracks.
- **Bitcoin only.** Ethereum/ERC-20 support would be a natural next
  increment, but Etherscan's v2 API requires a free API key (confirmed
  live: it rejects unauthenticated requests, unlike blockchain.info) —
  not registered in this phase.
- **No running balance field.** Computing a genuine `balance_after` needs
  either an address's complete transaction history or chaining from a
  previously ingested transaction; this adapter only fetches a bounded
  recent window (`--limit`), so a sometimes-null derived balance was
  deliberately left out rather than half-implemented.
- **A real edge case confirmed live**: `rawaddr` can return an
  unconfirmed (mempool) transaction with `block_height: null` — still a
  real, broadcast, publicly visible transaction, just not yet in a block,
  and treated as a valid observation. Such a transaction could
  theoretically be dropped if never confirmed — a minor limitation of
  on-chain data recency, not specific to this adapter.
- Validated against real, live blockchain.info data end to end: the
  Bitcoin genesis block address's 10 most recent transactions ingested
  correctly (including both confirmed and unconfirmed ones), net amounts
  matching hand-computed values from the raw UTXO data; idempotent
  re-ingestion confirmed; served correctly through
  `/api/v1/crypto-treasury` over HTTP.

**Analyst/guidance signals:**

- **No analyst estimates or rating changes were built, despite
  `EventType` declaring `ANALYST_RATING_CHANGE`/`ESTIMATE_REVISION`.**
  Real analyst estimates and ratings are third-party research products
  (IBES/Refinitiv, Zacks, Visible Alpha, ...) — every free-tier option
  checked during research (e.g. Finnhub) requires an API key at minimum,
  and comprehensive historical estimate data is a paid/licensed product
  industry-wide. This is a real content gap, the same conclusion reached
  for UK PDMR/RNS data in Phase 11 — not something a different
  implementation approach could close with free data.
- **No guidance direction, magnitude, or numeric range is extracted.**
  This phase flags WHICH 8-Ks were filed under Item 2.02 or 7.01 and
  links to them — it does not fetch or parse the press-release exhibit.
  Item 2.02 in particular is used for routine quarterly earnings far more
  often than for a standalone guidance update, so a `GuidanceDisclosure`
  row here means "a disclosure in this category happened," not "guidance
  changed."
- **No scoring/radar yet** — ingestion only, per the same ingest-now/
  score-later split as every prior signal-producing phase.
- Validated against real, live SEC EDGAR data end to end: Apple Inc.'s 30
  most recent Item 2.02 8-Ks ingested correctly, matching its real
  quarterly earnings cadence (accession numbers and dates cross-checked
  against SEC EDGAR directly); idempotent re-ingestion confirmed; served
  correctly through `/api/v1/guidance-disclosures` over HTTP.

## Known limitations (Phase 13)

Phase 13 covers three independent extensions the user asked for together
("all"): a third Convergence family (short-interest acceleration), an
alerting layer, and historical validation/backtesting. All three are
complete.

**Short-interest acceleration (third convergence family):**

- **Only rising short interest is scored**, mirroring Phase 3/5's
  "only buying/accumulation counts" convention — short covering (a
  falling cycle) is a different, real signal this module doesn't
  evaluate.
- **The Convergence Engine's `label`/`label_explanation` fields still
  characterize only the insider/institutional relationship, unchanged
  since Phase 5.** `short_interest_score` is exposed as a fully
  independent third field (never blended, per spec §33), but extending
  the label taxonomy itself to a genuine N-way combination (e.g.
  "insiders buying despite rising short interest") would need a
  combinatorial label space this increment deliberately doesn't build.
  A consumer wanting that read compares `label` and `short_interest_score`
  together.
- Validated against real, live FINRA short-interest data already in this
  system (Phase 10): MSFT and AAPL both correctly appear in
  `/api/v1/radar/short-interest` and as `SHORT_INTEREST_ONLY` in
  `/api/v1/radar/convergence` (neither has insider/institutional activity
  ingested), with composite scores correctly built only from the
  persistence component given each has too little settlement-cycle
  history yet for the magnitude/days-to-cover comparisons.

**Alerting layer:**

- **Deliberately thin** — every rule type reuses an existing scoring/
  radar/convergence module untouched; this layer only decides what's
  worth persisting as "surfaced," never computes a signal of its own.
- **No compound/boolean rule logic** — a rule watches exactly one signal
  type against one threshold or label set, not combinations across types.
- **Deduplicated per (rule, company, calendar date), not per underlying
  score value** — re-running evaluation the same day is a no-op; a later
  day's evaluation logs a fresh row if the signal still triggers, acting
  as a simple "still elevated as of this date" log rather than a single
  mutable "currently active" flag.
- **Rule creation is CLI-only** (`create-alert-rule`) — this system's API
  surface stays read-only everywhere, consistent with every prior phase;
  rules are user configuration, not ingested external data.
- Validated end to end against real data already in this system: created
  a `SHORT_INTEREST_ACCELERATION_THRESHOLD` rule and a `CONVERGENCE_LABEL`
  rule, ran `evaluate-alerts` against real AAPL/MSFT short-interest data,
  got 4 real alerts with correct `signal_summary` text (e.g. "+20.13% in
  the cycle settled 2026-08-31"); re-running confirmed fully idempotent
  (0 new alerts); served correctly through `/api/v1/alerts` and
  `/api/v1/alert-rules` over HTTP.

**Historical validation / backtesting harness:**

> **Update (Phase 15): the Alpha Vantage price-data source described
> below was removed.** Reading Alpha Vantage's actual Terms of Service
> (not just its API docs) found the free tier is licensed for personal,
> non-commercial use only and excludes exactly this platform's
> ingest-and-serve architecture. No compliant free replacement was found
> despite checking three more vendors. See "Known limitations (Phase 15)"
> for the full finding. The section below is preserved as an accurate
> record of what was built and validated at the time; the harness itself
> (`capint/backtesting/engine.py`) is unchanged and ready for a compliant
> source.

Used Alpha Vantage daily price bars (free, self-service API key —
`ALPHA_VANTAGE_API_KEY`, same registration pattern as Companies House in
Phase 11) to compute what actually followed a real, already-disclosed
short-interest signal — see "Backtesting" above for usage.
**Plain descriptive statistics only — never a trading signal, a
probability, or investment advice.**

- **Only the trailing ~100 trading days of price history are available,
  confirmed live before building this.** `outputsize=full` (complete
  multi-year history) is a premium-only feature on Alpha Vantage's free
  tier — requesting it returns an explicit "Information" error, not data
  (this adapter treats that as an empty result, not a crash). A signal
  older than that window has no price data to compute a forward return
  against; `compute_forward_return` returns an explicit `note` explaining
  that rather than fabricating a result — never silently omitted.
- **No statistical significance, multiple-comparison correction, or
  claim of predictive power** — deliberately, per the reasoning above.
- **Only backtests the short-interest-acceleration family so far** (the
  one real, quantitative signal already in this system with a natural
  "did the price move afterward" question). Insider/institutional/
  guidance signals could get the same treatment in a future increment;
  not built here to keep this increment's scope to one concrete,
  validated example.
- Validated against real, live data end to end: ingested 100 real trading
  days each for AAPL and MSFT via the live Alpha Vantage API; ran the
  real backtest against this system's own real FINRA short-interest
  signals (both settled 2026-08-31) and got real, hand-verified forward
  returns (AAPL (316.22-316.85)/316.85 = -0.20%; MSFT
  (493.95-507.29)/507.29 = -2.63%, both over the following 5 real trading
  days, 2026-08-31 to 2026-09-08); served correctly through
  `/api/v1/backtest/short-interest` over HTTP.

## Known limitations (Phase 14)

Phase 14 covers three independent extensions the user asked for together
("all"): M&A corporate actions, options/derivatives market signals, and
analyst estimates/ratings. All three are complete.

**M&A corporate-action disclosures (SEC 8-K Item 2.01):**

- **Deliberately excludes Item 1.01** ("Entry into a Material Definitive
  Agreement") — it covers ordinary commercial contracts far more often
  than signed-but-not-yet-closed merger agreements; including it would
  trade real specificity for recall.
- **No deal-term extraction** — this stores which 8-K was filed and a
  link to it, not consideration, counterparty identity, or deal value.
- **Cannot distinguish an acquisition from a spinoff/divestiture.** SEC's
  8-K item taxonomy has no item number specific to spinoffs (`EventType`
  also declares `SPINOFF`, still unused) — they're typically also filed
  under Item 2.01, or disclosed via a separate Form 10 registration this
  system doesn't ingest. A human must read the filing to tell which.
- **A real bug caught and fixed before it could fire**: this module's
  `raw_data_reference` initially reused Phase 12's exact guidance-ingester
  format, which would have collided on `Event.raw_data_reference`'s
  unique constraint the first time a real 8-K carried both a guidance
  item and Item 2.01 at once (a realistic scenario) — fixed with a
  distinct prefix, guarded by a regression test.
- Validated against Microsoft's real 2023-10-13 Item 2.01 filing (its
  Activision Blizzard acquisition completion) end to end, live.

**Options/derivatives market signals (Cboe volatility indices):**

- **Not per-security unusual options activity or a real put/call
  ratio — that data remains genuinely gated.** Confirmed live during
  research: Cboe's own market-statistics pages now show sign-in/
  subscription indicators, and granular options-level data is explicitly
  sold via Cboe DataShop. VIX (and VVIX, SKEW, ...) are Cboe's own
  published volatility indices, derived from S&P 500 option prices but
  market-wide, not attributable to any single security's options flow.
- **Not tied to any Company/Entity** — a volatility index is a market-
  wide signal, and inventing an entity-graph concept for it (identifiers,
  relationships it doesn't have) would add complexity with no real
  benefit. A handful of genuine Cboe single-stock volatility indices
  exist (VXAPL for Apple, VXAZN for Amazon) but aren't ingested here.
- **No scoring/signal layer yet** (e.g. a percentile-based "volatility
  spike" score, mirroring `capint.scoring.short_interest_acceleration`'s
  shape) — ingestion only, per this project's ingest-now/score-later
  precedent.
- Validated end to end: ingested the complete real VIX history (9,271
  real trading days, 1990-01-02 to 2026-09-11) live from the Cboe CDN;
  idempotent re-ingestion confirmed.

**Analyst estimates/ratings (Finnhub):**

> **Update (Phase 15): the Finnhub source described below was removed.**
> Its actual Terms of Service restrict the free tier to personal use and
> prohibit redistribution without written approval — the same problem as
> Alpha Vantage's price data. No compliant replacement was found. See
> "Known limitations (Phase 15)". The model and read-only API endpoint
> are unchanged and ready for a compliant source.

- **Aggregate rating-bucket counts only** (how many covering analysts
  rated strong buy/buy/hold/sell/strong sell each month) — **individual
  named analysts and price targets are a separate, paid Finnhub feature,
  not ingested here.** This is a real, deliberate scope boundary, not a
  partial attempt at more granular estimate data.
- **No genuine disclosure timestamp** — Finnhub's `period` is a monthly
  aggregation bucket re-queryable at any time, not a filing with a real
  "as of" moment, so (like Phase 13/14's price and volatility-index data)
  this isn't Event/Document-based and API reads aren't point-in-time
  gated.
- Every other free/legal analyst-estimate source checked across Phase 12
  and this phase (IBES/Refinitiv, Zacks, Visible Alpha) requires a paid/
  licensed relationship — Finnhub's free tier turned out to be the real
  exception for this specific aggregate view.
- Validated against real, live Finnhub data end to end: ingested AAPL's
  and MSFT's real 4-month recommendation history (e.g. MSFT: 23 strong
  buy / 41 buy / 5 hold / 0 sell / 0 strong sell for 2026-09); confirmed
  the same ticker resolves to the same Company entity already used by
  that ticker's short-interest/price data from earlier phases; idempotent
  re-ingestion confirmed; served correctly through
  `/api/v1/analyst-recommendations` over HTTP.

## Known limitations (Phase 15)

Phase 15 began as three requested extensions (news/sentiment, supply-
chain/relationship graph, crypto expansion) but surfaced a significant
compliance finding partway through that took priority: **two already-
shipped data sources (Alpha Vantage price data from Phase 13, Finnhub
analyst data from Phase 14) were removed after their actual Terms of
Service — not just their API documentation — turned out to prohibit this
platform's architecture.**

**What was found and why it matters:**

- While researching a Phase 15 news/sentiment source, Alpha Vantage's own
  `NEWS_SENTIMENT` endpoint looked promising (real per-article,
  per-ticker sentiment scores, confirmed live) — but reading Alpha
  Vantage's Terms of Service PDF (not just its API docs, which say
  nothing about this) found the free tier is licensed for "personal,
  non-commercial use" and explicitly excludes: using the platform "as or
  on behalf of a corporation, firm, partnership, trust or any other
  association" and "as part of any type of commercial activity that
  allows individuals or entities other than User to access information
  directly or indirectly." This system's architecture — ingest into a
  database, serve back out through an API — is squarely what those
  clauses describe, independent of whether anyone is charged anything.
  This applied retroactively to Phase 13's already-shipped price-bar
  ingestion, which had only been checked against the narrower
  `outputsize=full` premium-feature restriction, not this broader one.
- Checking whether this was Alpha-Vantage-specific, three more vendors
  were checked: **Twelve Data** (explicitly: "(l) Use Free Tier data for
  commercial purposes" is a prohibited use), **Finnhub** (explicitly:
  "strictly for personal use... Personal plan can't be used by any
  business even internally," and "not redistribute or share access to
  data or derived results... with anyone or any 3rd party without
  written approval") — this one applied retroactively to Phase 14's
  already-shipped analyst-recommendation-trends ingestion — and
  **Polygon.io** (splits "Individual Use" free-tier terms from "Business
  Use" paid terms the same way). All four show the identical pattern.
- This is a structural consequence of how US equity market data is
  licensed for redistribution at the exchange level (UTP/CTA plans),
  which is why free tiers exist to hook individual retail users, not to
  power third-party applications — not arbitrary vendor stinginess. A
  good-faith search for a compliant free replacement, for both daily
  equity prices and analyst estimates, found none.
- **FINRA (Phase 10) and Cboe (Phase 14's volatility indices) were
  evaluated against the same question and kept, deliberately.** Both
  have a generic "personal non-commercial use" clause in their *general
  website* Terms of Use, but that is standard boilerplate covering
  reproduction of ordinary web page content — a different thing from a
  data-API product's own specific license, which is what the four
  removed sources explicitly restricted. FINRA's short-interest API
  exists to fulfill a Rule 4560 regulatory disclosure mandate; Cboe's own
  VIX product page explicitly markets the historical index CSV as public,
  "Updated Daily" data, distinct from its paid DataShop product. Neither
  has a data-API-specific clause restricting this use the way Alpha
  Vantage, Twelve Data, Finnhub, and Polygon.io do. This is a real,
  considered distinction, not a convenient excuse to keep working
  features — it was reached by reading each source's actual terms, the
  same standard applied to the sources that were removed.

**What changed as a result:**

- `src/capint/adapters/alpha_vantage.py`, `src/capint/ingestion/alpha_vantage.py`,
  their CLI command (`ingest-prices`), and their tests/fixtures were
  deleted. `src/capint/models/price.py` (`PriceBar`) and
  `src/capint/backtesting/engine.py` were kept — the schema and
  backtesting logic aren't the problem, only the removed ingestion path
  was. The backtesting CLI command and API endpoint still work; they now
  report zero signals with computable forward returns until a compliant
  price source exists.
- `src/capint/adapters/finnhub.py`, `src/capint/ingestion/finnhub.py`,
  their CLI command (`ingest-analyst-recommendations`), and their tests/
  fixtures were deleted. `src/capint/models/analyst.py`
  (`AnalystRecommendationTrend`) and the read-only
  `GET /api/v1/analyst-recommendations` endpoint were kept for the same
  reason.
- Already-ingested Alpha Vantage/Finnhub data (200 real AAPL/MSFT price
  bars, 8 real analyst-recommendation rows) was purged from the local
  dev database — retaining data obtained via a since-identified
  ToS-violating method is part of the same concern as the ingestion code
  itself.
- `ALPHA_VANTAGE_API_KEY` and `FINNHUB_API_KEY` were removed from
  `.env`/`.env.example`/`config.py`.
- The rest of Phase 15 resumed after this remediation: relationship
  graph and news sentiment are complete (see below); crypto expansion
  was investigated and found blocked by the same ToS pattern (see
  below) — not built.

**Relationship graph (interlocking directorates) — complete.** The first
of the three original parts to resume after the remediation above, and
deliberately the one requiring no new external data source at all (no new
licensing risk to evaluate).

- **Only interlocking directorates are built** — see
  `src/capint/relationships/engine.py`'s module docstring for why a
  "common institutional ownership" relationship type was considered and
  rejected (a large index-fund holder would connect nearly every public
  company to nearly every other one, without a materiality threshold this
  increment doesn't build), and why `EntityType`'s SUPPLIER/CUSTOMER/
  COMPETITOR values remain unused (no structured, machine-readable
  supply-chain disclosure source exists in this system — SEC XBRL
  customer-concentration disclosures are unstructured footnote text).
- **No point-in-time gating** — Form 4 doesn't disclose when an officer/
  director role formally ends, so there's no genuine cutoff to gate an
  `as_of` parameter on (same reasoning as Phase 14's volatility-index and
  analyst-trend endpoints).
- **Computed on demand, not persisted** — a live view over current
  PersonCompanyRole data, same pattern as the radar/convergence
  endpoints, avoiding a stale-cache problem as new Form 4 data streams
  in.
- Tested against the real production entity-resolution code path
  (`capint.ingestion.sec_form4.upsert_person_company_role`), not a
  separate synthetic shortcut — covering pairing, company-scoped
  filtering, and the 3-company case (C(3,2)=3 pairs). Live-validated
  against real Form 4 data end to end: ingested 111 real transactions
  from 50 real recent filings and confirmed the endpoint correctly
  returns zero interlocks for that sample (the honest answer — board
  interlocks are real but relatively rare in any small random sample;
  the underlying Form 4 PersonCompanyRole data itself was already
  live-validated in Phase 2).

**Crypto expansion (Ethereum/ERC-20) — not built; blocked by the same
ToS finding.** Applying the scrutiny from the Alpha Vantage/Finnhub
remediation before asking for a key this time: Etherscan's dedicated API
Terms of Service (`etherscan.io/apiterms`, distinct from its general
website terms) explicitly state "you are permitted to view, print,
download, cache and make copies of our API Content... strictly for
personal use only but not for commercial use" — the identical pattern as
the four already-removed sources. No key was requested, and no
alternative Ethereum explorer was found with clearer terms in the time
available (most are commercial businesses with the same underlying
economics as Etherscan). This stays a real, open gap — Phase 12's
Bitcoin-only on-chain tracking (via blockchain.info/blockchain.com,
whose "Explorer" product terms do not repeat the same restriction as
explicitly, though a fully unambiguous reading wasn't reached either) is
what exists today.

**News sentiment (GDELT) — complete.** The last of the three original
parts, and a genuine positive finding: the GDELT Project's Terms of Use
explicitly permit commercial use and redistribution (see "News
sentiment" above for the exact language and full reasoning) — the
opposite result from every commercial vendor checked this phase.

- **Directional sentiment context, not a precise per-company signal** —
  GDELT has no concept of "company," only full-text search; a query like
  a company's name can match unrelated coverage. `query` is stored
  verbatim for transparency, and `--query` is kept separate from
  `--ticker` for exactly this reason.
- **Append-only, not deduplicated** — each ingestion call is its own
  observation over GDELT's continuously moving search window (e.g. "last
  7 days"), unlike every other ingestion module in this system, which
  dedupes against a natural per-fact key. Re-running the same ticker/
  query/timespan creates a new snapshot rather than being treated as a
  duplicate.
- **No point-in-time gating** (no `as_of`) — `retrieved_at` is when this
  system ran the search, not a filing/disclosure timestamp.
- **A real, hard rate limit**: GDELT asks for no more than one request
  every 5 seconds — confirmed live via an actual HTTP 429 with that exact
  guidance when requests came faster — which is why this adapter (unlike
  every other ingestion module here) only handles one ticker/query per
  CLI invocation rather than a repeatable batch.
- Validated against real, live GDELT data end to end: a real query for
  "Apple Inc" over the trailing 7 days returned 330 real matching
  articles with a real mean tone of +0.061 (a hand-verified count-weighted
  average of the real 22-bin tone histogram); confirmed the resolved
  Company entity matches the same AAPL entity used by this system's other
  ticker-resolved data from earlier phases; served correctly through
  `/api/v1/news-sentiment` over HTTP.

## Known limitations (Phase 16)

Phase 16 extends Phase 13's alerting layer — which only ever persisted
`Alert` rows for a consumer to poll via `GET /api/v1/alerts` — with
actual delivery when a rule fires. See "Alerts" above for the full usage
and design writeup; this section covers what's deliberately not built.

- **Webhook-only, no first-party notification-service integration** —
  the design constraint carried directly from Phase 15's compliance
  findings: this system will not hold an account or API key with any
  third-party service (email, SMS, push) until that vendor's actual Terms
  of Service have been read, the same way every ingestion data source now
  is. A webhook URL is the one delivery mechanism that requires no such
  review, because the credential and the account belong entirely to the
  user, not this system.
- **One-shot, synchronous, best-effort** — a single POST attempt at the
  moment an alert is created. No retry queue, no exponential backoff, no
  dead-letter handling, no delivery confirmation beyond the immediate
  HTTP response. A transient network blip at the moment of creation is a
  permanently missed delivery (though never a missed alert — the `Alert`
  row itself is unaffected, and `delivery_succeeded=False` records the
  failure honestly rather than silently).
- **No signature/HMAC verification support** — the payload is posted
  as plain JSON with no shared-secret signing, unlike (for example)
  GitHub or Stripe webhooks. A user who needs to verify the request
  actually came from this system would need to add that themselves (e.g.
  a bearer token embedded in their own webhook URL's query string or
  path, which this system already treats as opaque and never logs or
  re-serves — see `AlertRuleOut`'s docstring in
  `src/capint/api/schemas.py`).
- **No per-rule rate limiting or digesting** — if a rule's threshold is
  low enough to fire for many companies in one `evaluate-alerts` run,
  every one is delivered as a separate webhook POST in sequence, not
  batched or throttled. For a user-facing product this would eventually
  need a digest mode; out of scope for this MVP increment.
- Live-validated against real HTTP endpoints, not just mocked transports:
  created a real rule pointed at `https://httpbin.org/post`, ran
  `evaluate-alerts` against real previously-ingested SEC Form 4 insider
  data, and confirmed 5 real alerts were created with
  `delivery_attempted=True`/`delivery_succeeded=True`/`delivery_error=None`
  recorded from httpbin's real 200 response; separately created a rule
  pointed at `https://httpbin.org/status/500` and confirmed the failure
  path records `delivery_succeeded=False`/`delivery_error="HTTP 500"`
  without crashing evaluation of the remaining rules. Both test rules and
  their alerts were deleted after validation. Also confirmed
  `GET /api/v1/alert-rules` never serves back the raw `webhook_url` (only
  a `webhook_configured` boolean and `webhook_format`), and
  `GET /api/v1/alerts` serves the three new delivery fields correctly.
