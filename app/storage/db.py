"""Engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import Settings
from app.storage.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def get_engine(settings: Settings) -> Engine:
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def session_scope(settings: Settings) -> Iterator[Session]:
    get_engine(settings)
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all(settings: Settings) -> None:
    """Create any missing tables.

    Fine while the schema is still moving and there is no production data.
    Alembic migrations land with the NLP columns -- see GUIDE.md.
    """
    Base.metadata.create_all(get_engine(settings))
