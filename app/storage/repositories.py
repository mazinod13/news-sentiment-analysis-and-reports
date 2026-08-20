"""Every query lives here. The pipeline never writes SQL."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.pipeline.dedupe import from_signed64, to_signed64
from app.pipeline.normalize import Article as ArticleDTO
from app.storage.models import Article, FetchLog, SourceState


def existing_url_hashes(session: Session, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    rows = session.execute(
        select(Article.url_hash).where(Article.url_hash.in_(hashes))
    ).scalars()
    return set(rows)


def recent_simhashes(session: Session, *, since: datetime, limit: int = 5000) -> list[int]:
    """Simhashes of everything published recently, for near-duplicate checks.

    Bounded by time and count so this stays cheap as the corpus grows.
    """
    rows = session.execute(
        select(Article.simhash)
        .where(Article.published_at >= since, Article.simhash != 0)
        .order_by(Article.published_at.desc())
        .limit(limit)
    ).scalars()
    return [from_signed64(value) for value in rows]


def upsert_article(session: Session, article: ArticleDTO) -> bool:
    """Insert an article, ignoring it if url_hash is already present.

    Returns True when a row was actually inserted. Making this idempotent is
    what lets a cycle be re-run safely.
    """
    statement = (
        insert(Article)
        .values(
            source_id=article.source_id,
            url=article.url,
            url_hash=article.url_hash,
            title=article.title,
            body=article.body,
            summary=article.summary,
            author=article.author,
            lang=article.lang,
            category=article.category,
            published_at=article.published_at,
            published_estimated=article.published_estimated,
            fetched_at=article.fetched_at,
            image_url=article.image_url,
            simhash=to_signed64(article.simhash),
        )
        .on_conflict_do_nothing(index_elements=["url_hash"])
        .returning(Article.id)
    )
    return session.execute(statement).scalar_one_or_none() is not None


def get_state(session: Session, source_id: str) -> SourceState:
    state = session.get(SourceState, source_id)
    if state is None:
        state = SourceState(id=source_id)
        session.add(state)
        session.flush()
    return state


def save_state(
    session: Session,
    source_id: str,
    *,
    etag: str | None,
    last_modified: str | None,
    ran_at: datetime,
    interval_minutes: int,
    error: str | None = None,
) -> None:
    """Record the outcome of a run and schedule the next one.

    Failures back off exponentially (capped at 6h) but a source is never
    auto-disabled -- transient downtime is routine for these hosts.
    """
    state = get_state(session, source_id)
    if etag:
        state.etag = etag
    if last_modified:
        state.last_modified = last_modified
    state.last_run_at = ran_at

    if error:
        state.consecutive_failures += 1
        state.last_error = error[:2000]
        backoff = min(interval_minutes * (2 ** state.consecutive_failures), 360)
    else:
        state.consecutive_failures = 0
        state.last_error = None
        backoff = interval_minutes

    state.next_run_at = ran_at + timedelta(minutes=backoff)


def not_due_source_ids(session: Session, now: datetime) -> set[str]:
    """Sources still inside their poll interval.

    Callers subtract this from the configured set, so a source that has never
    run (no state row) is due by default.
    """
    rows = session.execute(
        select(SourceState.id).where(SourceState.next_run_at > now)
    ).scalars()
    return set(rows)


def log_fetch(session: Session, **kwargs) -> None:
    session.add(FetchLog(**kwargs))
