"""Engine and session lifecycle."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import Settings
from app.storage.models import Base, TermStat

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

    Additive only: new tables are created, existing ones are never altered.
    A column change to an existing table needs a migration.

    When term_stats is created on a database that already holds articles, their
    terms are counted here, so story clustering starts with a warm IDF.
    """
    engine = get_engine(settings)
    seed_term_stats = not inspect(engine).has_table(TermStat.__tablename__)
    Base.metadata.create_all(engine)
    if seed_term_stats:
        from app.storage.repositories import rebuild_term_stats

        with session_scope(settings) as session:
            rebuild_term_stats(session)
