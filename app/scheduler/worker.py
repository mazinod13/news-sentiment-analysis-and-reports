"""The worker: a self-maintaining service, and the only thing a deployment runs.

`python -m app.main worker` needs no manual steps before or after it:

    1  wait for the database -- an external server may be starting, or briefly
       unreachable; the worker retries instead of crashing
    2  create missing tables -- this also counts terms for articles already
       stored, so story clustering starts with a warm IDF
    3  catch up -- grade articles with no grade, group articles with no story
    4  loop -- poll due sources, catch up again, sleep

Catch-up runs after every cycle that polled sources, and at least every
CATCH_UP_EVERY otherwise, so a lexicon fixed after a bad deploy or a failed
clustering run heals on its own.

Sources run in a small thread pool. Threads (not async) because the Fetcher
already serialises per host, so the only concurrency that matters is *across*
hosts -- and threads keep the scrapers ordinary, debuggable, synchronous code.
"""

from __future__ import annotations

import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from app.ingestion.fetcher import Fetcher
from app.nlp.criticality import LexiconError
from app.pipeline.cluster import cluster_pending
from app.pipeline.grading import grade_stored
from app.pipeline.run import RunReport, run_source
from app.scheduler import health
from app.settings import NPT, Settings
from app.sources import Source, load_sources
from app.storage import repositories as repo
from app.storage.db import create_all, session_scope, wait_for_database

log = logging.getLogger(__name__)

TICK_SECONDS = 60
CATCH_UP_EVERY = timedelta(minutes=30)

_stop = threading.Event()


def due_sources(settings: Settings, now: datetime) -> list[Source]:
    configured = load_sources(settings.sources_dir, active_only=True)
    with session_scope(settings) as session:
        not_due = repo.not_due_source_ids(session, now)
    return [source for source_id, source in configured.items() if source_id not in not_due]


def run_once(settings: Settings, sources: list[Source]) -> list[RunReport]:
    if not sources:
        return []
    with Fetcher(settings) as fetcher:
        with ThreadPoolExecutor(max_workers=settings.ingest_concurrency) as pool:
            return list(pool.map(lambda s: run_source(s, settings, fetcher), sources))


def should_catch_up(
    *, polled_sources: bool, last_catch_up: datetime | None, now: datetime
) -> bool:
    """After any cycle that polled sources; otherwise at most every CATCH_UP_EVERY.

    Not on every idle tick: each check is a full anti-join over articles, and
    once a minute adds up on a small VM for no benefit.
    """
    if polled_sources or last_catch_up is None:
        return True
    return now - last_catch_up >= CATCH_UP_EVERY


def catch_up(settings: Settings) -> None:
    """Grade and cluster whatever is pending. Each step fails on its own: a
    broken lexicon must not stop story grouping, nor the other way round."""

    def beat() -> None:
        health.beat(settings.heartbeat_file)

    try:
        graded = grade_stored(settings, only_missing=True, on_batch=beat)
        if graded.graded:
            log.info("%s", graded)
    except LexiconError as exc:
        log.error("grading skipped, lexicon unusable: %s", exc)
    except Exception:
        log.exception("grading failed; will retry")

    try:
        clustered = cluster_pending(settings, on_batch=beat)
        if clustered.clustered or clustered.locked_out:
            log.info("%s", clustered)
    except Exception:
        log.exception("story clustering failed; will retry")


def run_cycle(settings: Settings) -> bool:
    """Poll whatever is due. Returns whether any source was polled."""
    sources = due_sources(settings, datetime.now(NPT))
    if not sources:
        return False
    log.info("%d source(s) due", len(sources))
    reports = run_once(settings, sources)
    new = sum(r.new for r in reports)
    failed = [r.source_id for r in reports if not r.ok]
    log.info("cycle done: %d new article(s)%s", new,
             f", failed: {', '.join(failed)}" if failed else "")
    return True


def run_forever(settings: Settings) -> None:
    _stop_on_signals()
    wait_for_database(settings, stop=_stop)
    if _stop.is_set():
        return
    create_all(settings)
    log.info("schema up to date")

    catch_up(settings)
    last_catch_up = datetime.now(NPT)
    health.beat(settings.heartbeat_file)
    log.info("worker started (concurrency=%s)", settings.ingest_concurrency)

    while not _stop.is_set():
        try:
            polled = run_cycle(settings)
            now = datetime.now(NPT)
            if should_catch_up(polled_sources=polled, last_catch_up=last_catch_up, now=now):
                catch_up(settings)
                last_catch_up = now
            # Beat only after a cycle SUCCEEDS; see app/scheduler/health.py.
            health.beat(settings.heartbeat_file)
        except Exception:
            # Typically the database went away mid-cycle. No heartbeat, so the
            # container turns unhealthy if this persists; retry next tick.
            log.exception("cycle failed; retrying in %ss", TICK_SECONDS)
        _stop.wait(TICK_SECONDS)

    log.info("worker stopped")


def _stop_on_signals() -> None:
    """Finish the current cycle on SIGTERM (docker stop) instead of dying
    mid-write, then exit cleanly."""

    def handle(signum, _frame) -> None:
        log.info("signal %s received; stopping after the current cycle", signum)
        _stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle)
        except (ValueError, OSError):   # not the main thread, or unsupported here
            pass
