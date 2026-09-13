import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from capint.models.base import Base


@pytest.fixture()
def engine():
    # StaticPool + check_same_thread=False so the single in-memory SQLite
    # database is shared across connections within one test.
    from sqlalchemy.pool import StaticPool

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine) -> Session:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as s:
        yield s
