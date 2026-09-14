from sqlalchemy import text

from capint.db import make_engine


def test_make_engine_enables_pool_pre_ping():
    """Phase 18: pool_pre_ping guards against a pooled connection going
    stale between requests (DB restart, idle-timeout) — see make_engine's
    docstring. Asserting the private `_pre_ping` flag is the only direct
    way to observe this was actually passed through to the pool; the
    reconnect behavior itself only manifests against a real network
    database (Postgres) going stale mid-session, which isn't reproducible
    against SQLite in this test environment."""
    engine = make_engine("sqlite:///:memory:")
    assert engine.pool._pre_ping is True


def test_make_engine_produces_a_working_engine():
    engine = make_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
