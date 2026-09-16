"""Every query lives here. The pipeline never writes SQL.

Written for MySQL 8. Three places where MySQL differs from PostgreSQL and the
difference is not cosmetic:

  * no RETURNING and no "insert, do nothing": upsert_article uses
    ON DUPLICATE KEY UPDATE with LAST_INSERT_ID(id) and reads rowcount to tell
    "inserted" from "already there";
  * no GIN index over JSON: candidate lookup joins the article_terms table;
  * GET_LOCK is held by a CONNECTION, not a transaction, so the clusterer keeps
    one connection open for its whole run and releases the lock in a finally.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import Connection, delete, func, select, update
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.nlp.analysis import Analysis
from app.nlp.stories import Member, StoryArticle, story_terms
from app.pipeline.dedupe import from_signed64, to_signed64
from app.pipeline.normalize import Article as ArticleDTO
from app.storage.models import (
    Article,
    ArticleAnalysis,
    ArticleCluster,
    ArticleTerm,
    FetchLog,
    SourceState,
    StoryCluster,
    TermStat,
)

# Names the "one clusterer at a time" lock. MySQL lock names are global to the
# server, so this is specific rather than "cluster".
CLUSTER_LOCK_NAME = "news_sentiment_cluster"


def existing_url_hashes(session: Session, hashes: list[str]) -> set[str]:
    if not hashes:
        return set()
    rows = session.execute(
        select(Article.url_hash).where(Article.url_hash.in_(hashes))
    ).scalars()
    return set(rows)


def relabel_category(session: Session, hashes: set[str], category: str) -> int:
    """Give already-stored articles a section's category, e.g. `economic`.

    A finance story usually appears in an outlet's main feed as well as its
    economy section, and it is stored once, by whichever feed ran first. Only
    rows still in the generic `news` category move, so a govt or disaster label
    is never overwritten. Returns how many rows changed.
    """
    if not hashes:
        return 0
    result = session.execute(
        update(Article)
        .where(Article.url_hash.in_(hashes), Article.category == "news")
        .values(category=category)
    )
    return result.rowcount or 0


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


def upsert_article(session: Session, article: ArticleDTO) -> int | None:
    """Insert an article, ignoring it if url_hash is already present.

    Returns the new row's id, or None when the article was already stored.
    Making this idempotent is what lets a cycle be re-run safely.

    MySQL has neither RETURNING nor ON CONFLICT DO NOTHING. The standard idiom:
    ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id) makes the *existing* row's
    id readable through lastrowid, and rowcount separates the cases -- 1 for an
    insert, 0 when the duplicate key made the update a no-op. INSERT IGNORE
    would be shorter and would also swallow real errors such as truncation.
    """
    statement = insert(Article).values(
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
    result = session.execute(
        statement.on_duplicate_key_update(id=func.LAST_INSERT_ID(Article.__table__.c.id))
    )
    return int(result.lastrowid) if result.rowcount == 1 else None


def save_analysis(
    session: Session, article_id: int, analysis: Analysis, *, analysed_at: datetime
) -> None:
    """Insert or replace one article's keywords and grade."""
    values = {**analysis.as_row(), "analysed_at": analysed_at}
    statement = insert(ArticleAnalysis).values(article_id=article_id, **values)
    session.execute(statement.on_duplicate_key_update(**values))


def articles_to_analyse(
    session: Session, *, after_id: int, limit: int, only_missing: bool
) -> list[tuple[int, str, str]]:
    """(id, title, text) of stored articles in id order, for batch analysis.

    Paging by id rather than offset keeps each batch cheap and stable while
    rows are being written.
    """
    query = select(Article.id, Article.title, Article.body, Article.summary).where(
        Article.id > after_id
    )
    if only_missing:
        query = query.outerjoin(ArticleAnalysis, ArticleAnalysis.article_id == Article.id).where(
            ArticleAnalysis.article_id.is_(None)
        )
    rows = session.execute(query.order_by(Article.id).limit(limit)).all()
    return [(row.id, row.title, row.body or row.summary) for row in rows]


def try_lock_clustering(connection: Connection) -> bool:
    """Take the clustering lock, without waiting. Two clusterers running at
    once could each start a separate story for the same event.

    MySQL's GET_LOCK belongs to the CONNECTION: it survives commits and is
    released by RELEASE_LOCK or when the connection closes. PostgreSQL's
    transaction lock released itself at commit; here the caller must hold the
    connection open for the whole run and release explicitly.
    """
    return bool(connection.scalar(select(func.GET_LOCK(CLUSTER_LOCK_NAME, 0))))


def unlock_clustering(connection: Connection) -> None:
    connection.execute(select(func.RELEASE_LOCK(CLUSTER_LOCK_NAME)))


def articles_to_cluster(session: Session, *, limit: int) -> list:
    """Stored articles with no story yet, oldest first so stories form in order."""
    return session.execute(
        select(
            Article.id, Article.source_id, Article.title, Article.body, Article.summary,
            Article.lang, Article.published_at,
        )
        .outerjoin(ArticleCluster, ArticleCluster.article_id == Article.id)
        .where(ArticleCluster.article_id.is_(None))
        .order_by(Article.published_at, Article.id)
        .limit(limit)
    ).all()


def top_stories(session: Session, *, since: datetime, limit: int, min_sources: int) -> list:
    """Recent stories, most outlets first, with the most critical member grade."""
    top_grade = (
        select(func.min(ArticleAnalysis.grade))
        .join(ArticleCluster, ArticleCluster.article_id == ArticleAnalysis.article_id)
        .where(ArticleCluster.cluster_id == StoryCluster.id)
        .scalar_subquery()
    )
    return session.execute(
        select(
            StoryCluster.id, StoryCluster.title, StoryCluster.article_count,
            StoryCluster.source_count, StoryCluster.sources, StoryCluster.first_published_at,
            StoryCluster.last_published_at, top_grade.label("grade"),
        )
        .where(StoryCluster.last_published_at >= since, StoryCluster.source_count >= min_sources)
        .order_by(
            StoryCluster.source_count.desc(),
            StoryCluster.article_count.desc(),
            StoryCluster.last_published_at.desc(),
        )
        .limit(limit)
    ).all()


class DbClusterStore:
    """app.nlp.stories.ClusterStore on MySQL, inside one session."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._corpus_size: int | None = None

    def corpus_size(self) -> int:
        # Every stored article: term_stats is counted at ingest, not at clustering.
        if self._corpus_size is None:
            self._corpus_size = self.session.scalar(select(func.count()).select_from(Article)) or 0
        return self._corpus_size

    def document_frequencies(self, terms: Iterable[str]) -> dict[str, int]:
        wanted = list(set(terms))
        found: dict[str, int] = {}
        for start in range(0, len(wanted), 1000):   # keep IN lists bounded
            chunk = wanted[start:start + 1000]
            rows = self.session.execute(
                select(TermStat.term, TermStat.df).where(TermStat.term.in_(chunk))
            )
            found.update({row.term: row.df for row in rows})
        return found

    def candidates(
        self, *, lang: str, start: datetime, end: datetime, any_of_terms: list[str], limit: int
    ) -> list[Member]:
        """Recent same-language articles sharing any of these terms.

        Two statements on purpose: the first finds the articles through the
        indexed article_terms table, grouping away the one-row-per-shared-term
        fan-out; the second reads their term maps. Grouping by the JSON column
        itself is not something MySQL will do.
        """
        if not any_of_terms:
            return []
        matches = self.session.execute(
            select(ArticleCluster.article_id, ArticleCluster.cluster_id)
            .join(ArticleTerm, ArticleTerm.article_id == ArticleCluster.article_id)
            .where(
                ArticleCluster.lang == lang,
                ArticleCluster.published_at.between(start, end),
                ArticleTerm.term.in_(any_of_terms),
            )
            .group_by(ArticleCluster.article_id, ArticleCluster.cluster_id)
            .order_by(func.max(ArticleCluster.published_at).desc())
            .limit(limit)
        ).all()
        if not matches:
            return []
        terms_by_article = dict(
            self.session.execute(
                select(ArticleCluster.article_id, ArticleCluster.terms).where(
                    ArticleCluster.article_id.in_([m.article_id for m in matches])
                )
            ).all()
        )
        return [
            Member(m.article_id, m.cluster_id, terms_by_article.get(m.article_id) or {})
            for m in matches
        ]

    def create_cluster(self, article: StoryArticle) -> int:
        cluster = StoryCluster(
            lang=article.lang,
            title=article.title,
            first_published_at=article.published_at,
            last_published_at=article.published_at,
            article_count=1,
            source_count=1,
            sources=[article.source_id],
        )
        self.session.add(cluster)
        self.session.flush()
        return cluster.id

    def join_cluster(self, cluster_id: int, article: StoryArticle) -> None:
        cluster = self.session.get(StoryCluster, cluster_id, with_for_update=True)
        cluster.article_count += 1
        cluster.first_published_at = min(cluster.first_published_at, article.published_at)
        cluster.last_published_at = max(cluster.last_published_at, article.published_at)
        if article.source_id not in cluster.sources:
            # Reassign rather than append: in-place JSON mutation is not tracked.
            cluster.sources = [*cluster.sources, article.source_id]
            cluster.source_count = len(cluster.sources)

    def add_member(self, article: StoryArticle, cluster_id: int, similarity: float) -> None:
        self.session.add(
            ArticleCluster(
                article_id=article.article_id,
                cluster_id=cluster_id,
                similarity=similarity,
                terms=article.terms,
                lang=article.lang,
                published_at=article.published_at,
            )
        )
        # Flush first: article_terms points at the row above.
        self.session.flush()
        if article.terms:
            self.session.execute(
                insert(ArticleTerm).values(
                    [{"article_id": article.article_id, "term": term} for term in article.terms]
                ).prefix_with("IGNORE")
            )


def count_terms(session: Session, document_frequencies: Counter[str]) -> None:
    """Add newly stored articles' terms to the IDF statistics.

    Called once per source run, in the transaction that stores the articles.
    One statement with rows in sorted term order: concurrent sources then lock
    shared term rows in the same order and cannot deadlock each other.
    """
    if not document_frequencies:
        return
    rows = [{"term": term, "df": n} for term, n in sorted(document_frequencies.items())]
    statement = insert(TermStat).values(rows)
    session.execute(
        statement.on_duplicate_key_update(df=TermStat.__table__.c.df + statement.inserted.df)
    )


def rebuild_term_stats(session: Session, *, batch: int = 1000) -> int:
    """Recount document frequencies over every stored article. Returns how many.

    For a database that held articles before story clustering existed; ingest
    keeps the counts current afterwards.

    Run it while the worker is stopped. PostgreSQL could hold a table lock for
    the delete-and-reinsert; MySQL's LOCK TABLES would commit the transaction
    and block access to every other table, which is worse than the race it
    would prevent. Concurrent ingest increments can be lost, and the fix is to
    run this again.
    """
    counts: Counter[str] = Counter()
    documents = after_id = 0
    while True:
        rows = session.execute(
            select(Article.id, Article.title, Article.body, Article.summary)
            .where(Article.id > after_id)
            .order_by(Article.id)
            .limit(batch)
        ).all()
        if not rows:
            break
        for row in rows:
            counts.update(story_terms(row.title, row.body or row.summary).keys())
        documents += len(rows)
        after_id = rows[-1].id

    session.execute(delete(TermStat))
    items = sorted(counts.items())
    for start in range(0, len(items), 5000):
        session.execute(
            insert(TermStat), [{"term": t, "df": n} for t, n in items[start:start + 5000]]
        )
    return documents


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
