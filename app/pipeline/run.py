"""Orchestrates one source, end to end.

This is the flow GUIDE.md walks through:

    1  fetch listing (conditional GET -> 304 short-circuits everything)
    2  dedupe within the batch
    3  drop items already stored (one query, by url_hash)
    4  fetch article bodies -- only for what survived step 3
    5  normalise into Articles
    6  near-duplicate check against recent simhashes
    7  upsert
    8  record state + fetch log

Steps 1-6 need no database, which is what `probe` runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.ingestion.base import ScrapeError
from app.ingestion.fetcher import Fetcher
from app.ingestion.registry import build_scraper
from app.pipeline.dedupe import dedupe_batch, is_near_duplicate
from app.pipeline.normalize import Article, normalize, url_hash
from app.settings import NPT, Settings
from app.sources import Source
from app.storage import repositories as repo
from app.storage.db import session_scope

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    source_id: str
    seen: int = 0
    new: int = 0
    duplicate: int = 0
    not_modified: bool = False
    ok: bool = True
    error: str | None = None
    articles: list[Article] = field(default_factory=list)

    def __str__(self) -> str:
        if not self.ok:
            return f"{self.source_id}: FAILED - {self.error}"
        if self.not_modified:
            return f"{self.source_id}: unchanged (304)"
        return (
            f"{self.source_id}: {self.seen} seen, {self.new} new, {self.duplicate} duplicate"
        )


def run_source(
    source: Source,
    settings: Settings,
    fetcher: Fetcher,
    *,
    dry_run: bool = False,
    limit: int | None = None,
) -> RunReport:
    """Run one source. Never raises for a source-level failure -- the error is
    captured in the report so one bad outlet cannot stop a cycle."""

    report = RunReport(source_id=source.id)
    started = datetime.now(NPT)
    extra = {"source_id": source.id, "method": source.method, "url": source.url}

    try:
        scraper = build_scraper(source, fetcher, settings.selectors_dir)

        # 1. conditional GET
        etag = last_modified = None
        if not dry_run:
            with session_scope(settings) as session:
                state = repo.get_state(session, source.id)
                etag, last_modified = state.etag, state.last_modified

        items = scraper.fetch(etag=etag, last_modified=last_modified)
        state_after = scraper.state
        report.not_modified = bool(state_after.get("not_modified"))
        report.seen = len(items)

        if report.not_modified:
            if not dry_run:
                _persist_state(settings, source, state_after, started, error=None)
            return report

        # 2. duplicates inside this one batch
        items = dedupe_batch(items, key=lambda item: url_hash(item.url))

        # 3. drop what we already have
        if not dry_run:
            hashes = [url_hash(item.url) for item in items]
            with session_scope(settings) as session:
                known = repo.existing_url_hashes(session, hashes)
            fresh = [item for item in items if url_hash(item.url) not in known]
            report.duplicate += len(items) - len(fresh)
            items = fresh

        if limit is not None:
            items = items[:limit]

        # 4. bodies, only for what survived
        if settings.fetch_bodies:
            for item in items[: settings.max_bodies_per_run]:
                scraper.fetch_body(item)

        # 5. normalise
        articles = [normalize(item, source, fetched_at=started) for item in items]

        if dry_run:
            report.articles = articles
            report.new = len(articles)
            return report

        # 6-8. near-duplicate check, store, record
        with session_scope(settings) as session:
            recent = repo.recent_simhashes(session, since=started - timedelta(days=2))
            for article in articles:
                if is_near_duplicate(article.simhash, recent):
                    report.duplicate += 1
                    log.debug("near-duplicate skipped: %s", article.url, extra=extra)
                    continue
                if repo.upsert_article(session, article):
                    report.new += 1
                    recent.append(article.simhash)
                else:
                    report.duplicate += 1

            repo.save_state(
                session,
                source.id,
                etag=state_after.get("etag"),
                last_modified=state_after.get("last_modified"),
                ran_at=started,
                interval_minutes=source.poll_interval_minutes,
                error=None,
            )
            repo.log_fetch(
                session,
                source_id=source.id,
                started_at=started,
                finished_at=datetime.now(NPT),
                items_seen=report.seen,
                items_new=report.new,
                items_duplicate=report.duplicate,
                not_modified=report.not_modified,
                ok=True,
            )

    except ScrapeError as exc:
        report.ok, report.error = False, str(exc)
        log.error("scrape failed: %s", exc, extra=extra)
        if not dry_run:
            _record_failure(settings, source, started, str(exc))
    except Exception as exc:  # a bug in one scraper must not kill the cycle
        report.ok, report.error = False, f"{type(exc).__name__}: {exc}"
        log.exception("unexpected failure", extra=extra)
        if not dry_run:
            _record_failure(settings, source, started, report.error)

    log.info("%s", report, extra=extra)
    return report


def _persist_state(settings, source, state_after, started, error) -> None:
    with session_scope(settings) as session:
        repo.save_state(
            session,
            source.id,
            etag=state_after.get("etag"),
            last_modified=state_after.get("last_modified"),
            ran_at=started,
            interval_minutes=source.poll_interval_minutes,
            error=error,
        )


def _record_failure(settings, source, started, error: str) -> None:
    try:
        with session_scope(settings) as session:
            repo.save_state(
                session,
                source.id,
                etag=None,
                last_modified=None,
                ran_at=started,
                interval_minutes=source.poll_interval_minutes,
                error=error,
            )
            repo.log_fetch(
                session,
                source_id=source.id,
                started_at=started,
                finished_at=datetime.now(NPT),
                ok=False,
                error=error[:2000],
            )
    except Exception:
        log.exception("could not record failure for %s", source.id)
