from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from capint.config import settings


def make_engine(url: str | None = None) -> Engine:
    """`pool_pre_ping=True` (Phase 18): tests every pooled connection with a
    lightweight liveness check before handing it out, transparently
    reconnecting if it's gone stale. Matters for the production Postgres
    target — a long-lived pooled connection can silently die (DB restart,
    load-balancer/firewall idle timeout) between requests, and without
    this a request would fail with an opaque "server closed the
    connection unexpectedly" instead of just getting a fresh connection.
    Harmless overhead for SQLite (dev), which this system still supports."""
    return create_engine(url or settings.database_url, future=True, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
