# Phase 18: one image for both the read-only API server and the ingestion
# CLI (they're the same codebase — see README "Deployment" section for how
# to run each). Base image is a normal, widely-available Python version,
# not this dev machine's own interpreter (3.14) — nothing here depends on
# a version newer than 3.11's syntax/stdlib.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first so this layer is cached across source-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini .
COPY migrations/ migrations/
COPY src/ src/

# No packaging/build backend in pyproject.toml (see that file) — this
# project has always been run via PYTHONPATH=src rather than `pip install
# -e .`; matching that existing convention here rather than introducing a
# separate packaging setup just for the image.
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Never --reload here (that's the dev-only hot-reload flag from the
# `uvicorn --reload` command in README's "Run the API" section — it adds a
# file-watcher and is not meant for production). For more than one worker
# process, pass `--workers N` (CPU-bound on migrations/ingestion, not on
# serving this read-only API, so start with 1 and measure before scaling
# up) — not hardcoded here since the right number depends on the host's
# CPU allocation, which this Dockerfile can't know.
CMD ["uvicorn", "capint.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
