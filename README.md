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

## Test

```bash
pytest
```

## Layout

- `src/capint/models/` — SQLAlchemy models: Entity/EntityIdentifier,
  Source/Document (provenance), Event, Company, Person/PersonCompanyRole,
  InsiderTransaction.
- `src/capint/temporal.py` — point-in-time query helpers. Read this before
  writing any historical/backtest query — see its module docstring.
- `src/capint/adapters/` — source adapter interface. No adapter performs a
  live fetch yet (see `sec_stub.py` for why).
- `src/capint/api/` — FastAPI app, versioned under `/api/v1`.
- `migrations/` — Alembic migrations.
- `tests/fixtures/synthetic.py` — synthetic-only fixture builders, clearly
  labeled, used by every test. Never a source of real financial data.
