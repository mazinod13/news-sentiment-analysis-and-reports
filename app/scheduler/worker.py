"""Priority-driven polling loop.

Sources run in a small thread pool. Threads (not async) because the Fetcher
already serialises per host, so the only concurrency that matters is *across*
hosts -- and threads keep the scrapers ordinary, debuggable, synchronous code.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from app.ingestion.fetcher import Fetcher
from app.pipeline.run import RunReport, run_source
from app.settings import NPT, Settings
from app.sources import Source, load_sources
from app.storage import repositories as repo
from app.storage.db import session_scope

log = logging.getLogger(__name__)

TICK_SECONDS = 60


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


def run_forever(settings: Settings) -> None:
    log.info("worker started (concurrency=%s)", settings.ingest_concurrency)
    while True:
        now = datetime.now(NPT)
        sources = due_sources(settings, now)
        if sources:
            log.info("%d source(s) due", len(sources))
            reports = run_once(settings, sources)
            new = sum(r.new for r in reports)
            failed = [r.source_id for r in reports if not r.ok]
            log.info("cycle done: %d new article(s)%s", new,
                     f", failed: {', '.join(failed)}" if failed else "")
        time.sleep(TICK_SECONDS)
