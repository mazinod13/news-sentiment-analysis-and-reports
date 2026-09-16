"""Engine and session lifecycle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import certifi
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.settings import Settings
from app.storage.models import Base, TermStat

log = logging.getLogger(__name__)

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def connect_args(settings: Settings) -> dict:
    """TLS options for PyMySQL, which takes an `ssl` dict rather than a URL mode.

    `{}` means "connect without TLS". The modes mirror MySQL's own: `require`
    encrypts without checking the certificate, `verify-ca` checks it, and
    `verify-full` also checks the hostname -- against certifi's roots, since the
    Alpine image ships no system CA bundle.

    TLS is not just good practice here: MySQL 8 authenticates with
    caching_sha2_password, and over an encrypted connection PyMySQL can do that
    on its own. Without TLS it needs the `cryptography` package (~16 MB with its
    dependencies), which this image deliberately does not carry.
    """
    if make_url(settings.database_url).get_driver_name() != "pymysql":
        return {}
    mode = (settings.database_sslmode or "").strip().lower()
    if mode in ("", "disable"):
        return {}
    options: dict = {"ssl": {"ca": settings.database_ssl_root_cert or certifi.where()}}
    if mode in ("verify-ca", "verify_ca"):
        options["ssl_verify_cert"] = True
    elif mode in ("verify-full", "verify_full", "verify-identity"):
        options["ssl_verify_cert"] = True
        options["ssl_verify_identity"] = True
    return options


def get_engine(settings: Settings) -> Engine:
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(
            make_url(settings.database_url),
            pool_pre_ping=True,
            future=True,
            connect_args=connect_args(settings),
        )
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def wait_for_database(
    settings: Settings,
    *,
    timeout: float | None = None,
    stop: threading.Event | None = None,
) -> None:
    """Block until the database accepts a connection.

    An external server may still be starting, or be briefly unreachable, when
    the container starts. Retries with backoff capped at 30 s; `timeout=None`
    waits indefinitely, and `stop` lets a shutdown signal end the wait.
    """
    engine = get_engine(settings)
    delay = waited = 0.0
    while True:
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except DBAPIError as exc:
            if timeout is not None and waited >= timeout:
                raise
            delay = min(max(delay * 2, 1.0), 30.0)
            # The driver's own message names host and user, never the password.
            reason = str(exc.orig).strip().splitlines()[0] if exc.orig else type(exc).__name__
            log.warning("database not reachable (%s); retrying in %.0fs", reason, delay)
            if stop is not None:
                if stop.wait(delay):
                    return
            else:
                threading.Event().wait(delay)
            waited += delay
            continue
        if waited:
            log.info("database reachable after %.0fs", waited)
        return


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
