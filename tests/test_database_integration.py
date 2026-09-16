"""End-to-end checks against a real MySQL 8. Skipped unless you point it at one.

The rest of the suite is offline, so nothing here runs by default:

    TEST_DATABASE_URL=mysql+pymysql://user:pass@host:3306/scratch?charset=utf8mb4 \
        pytest tests/test_database_integration.py

WARNING: the database is emptied at the start of the run. Point it at a scratch
database, never at the live one.

What it is for: the storage layer leans on MySQL-specific behaviour that the
offline suite cannot reach -- ON DUPLICATE KEY UPDATE with LAST_INSERT_ID to
emulate an upsert that returns its id, the article_terms lookup that replaces a
JSON index, GET_LOCK, and utf8mb4 round-tripping of Devanagari.
"""

from __future__ import annotations

import dataclasses
import os
from collections import Counter
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, inspect, select
from test_stories import BASE, MemoryStore, article, saved_articles

import app.storage.db as dbmod
from app.ingestion.fetcher import FetchResult
from app.nlp.stories import assign, story_terms
from app.pipeline.cluster import cluster_pending
from app.pipeline.dedupe import simhash
from app.pipeline.grading import grade_stored
from app.pipeline.normalize import Article as ArticleDTO
from app.pipeline.normalize import url_hash
from app.pipeline.run import run_source
from app.scheduler import health, worker
from app.settings import ROOT, load_settings
from app.sources import load_source
from app.storage import repositories as repo
from app.storage.models import (
    Article,
    ArticleAnalysis,
    ArticleCluster,
    Base,
    StoryCluster,
    TermStat,
)

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL, reason="set TEST_DATABASE_URL to a scratch database to run these"
)

FIXTURES = ROOT / "tests" / "fixtures"
SECTION_ID = "onlinekhabar_english-economy"


class StubFetcher:
    """Replays saved pages: the listing for the source URL, the story for
    anything else. Keeps the real scrapers on their real code path, offline."""

    def __init__(self, source_url: str, listing: bytes, story: bytes) -> None:
        self.source_url, self.listing, self.story = source_url, listing, story

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        body = self.listing if url == self.source_url else self.story
        return FetchResult(url=url, status=200, text=body.decode("utf-8"), content=body)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("HEARTBEAT_FILE", str(tmp_path / "heartbeat"))
    if dbmod._engine is not None:
        dbmod._engine.dispose()
    monkeypatch.setattr(dbmod, "_engine", None)
    monkeypatch.setattr(dbmod, "_Session", None)
    loaded = load_settings()
    engine = dbmod.get_engine(loaded)
    Base.metadata.drop_all(engine)
    return loaded


@pytest.fixture
def fresh(settings):
    dbmod.create_all(settings)
    return settings


def test_create_all_makes_every_table(settings):
    dbmod.create_all(settings)
    tables = set(inspect(dbmod.get_engine(settings)).get_table_names())
    assert {
        "articles", "article_analysis", "story_clusters", "article_clusters",
        "term_stats", "source_state", "fetch_log",
    } <= tables


def test_section_source_relabels_what_the_main_feed_stored(fresh):
    """The whole point of section_of, end to end: same stories, new category."""
    section = load_source(ROOT / "config" / "sources" / f"{SECTION_ID}.yaml")
    as_main = dataclasses.replace(
        section, id="onlinekhabar_english", category="news", section_of=None
    )
    feed = (FIXTURES / f"{SECTION_ID}_feed.xml").read_bytes()
    story = (FIXTURES / f"{SECTION_ID}_story.html").read_bytes()
    summaries = dataclasses.replace(fresh, fetch_bodies=False)

    stored = run_source(as_main, summaries, StubFetcher(section.url, feed, story))
    relabelled = run_source(section, summaries, StubFetcher(section.url, feed, story))
    again = run_source(section, summaries, StubFetcher(section.url, feed, story))

    assert stored.ok and stored.new == 20
    assert relabelled.relabelled == 20 and relabelled.new == 0
    assert (again.new, again.relabelled, again.duplicate) == (0, 0, 20), "ingest is idempotent"
    with dbmod.session_scope(fresh) as session:
        assert Counter(session.execute(select(Article.category)).scalars()) == {"economic": 20}


def test_grading_backfills_only_what_is_missing(fresh):
    section = load_source(ROOT / "config" / "sources" / f"{SECTION_ID}.yaml")
    feed = (FIXTURES / f"{SECTION_ID}_feed.xml").read_bytes()
    story = (FIXTURES / f"{SECTION_ID}_story.html").read_bytes()
    run_source(
        section,
        dataclasses.replace(fresh, fetch_bodies=False),
        StubFetcher(section.url, feed, story),
    )

    with dbmod.session_scope(fresh) as session:
        articles = session.scalar(select(func.count()).select_from(Article))
        graded = session.scalar(select(func.count()).select_from(ArticleAnalysis))
        assert articles == graded, "ingest grades as it stores"
        ids = session.execute(select(ArticleAnalysis.article_id).limit(5)).scalars().all()
        session.execute(delete(ArticleAnalysis).where(ArticleAnalysis.article_id.in_(ids)))

    assert grade_stored(fresh, only_missing=True).graded == 5


def test_ingest_term_counts_match_a_full_rebuild(fresh):
    """IDF is counted at ingest; `cluster --rebuild-terms` recounts from scratch.
    If these ever disagree, story similarity silently changes."""
    section = load_source(ROOT / "config" / "sources" / f"{SECTION_ID}.yaml")
    run_source(
        section,
        dataclasses.replace(fresh, fetch_bodies=False),
        StubFetcher(
            section.url,
            (FIXTURES / f"{SECTION_ID}_feed.xml").read_bytes(),
            (FIXTURES / f"{SECTION_ID}_story.html").read_bytes(),
        ),
    )

    with dbmod.session_scope(fresh) as session:
        counted = dict(session.execute(select(TermStat.term, TermStat.df)).all())
    with dbmod.session_scope(fresh) as session:
        rebuilt_over = repo.rebuild_term_stats(session)
    with dbmod.session_scope(fresh) as session:
        rebuilt = dict(session.execute(select(TermStat.term, TermStat.df)).all())

    assert counted and counted == rebuilt and rebuilt_over == 20


def test_clustering_in_mysql_matches_the_in_memory_result(fresh):
    """Same articles, same stories, whether grouped in memory or through the
    article_terms candidate lookup and the real tables."""
    docs = saved_articles()
    articles = [
        article(i + 1, d.title, d.body, source_id=d.source_id, lang=d.lang,
                at=BASE + timedelta(minutes=i))
        for i, d in enumerate(docs)
    ]
    memory = MemoryStore()
    for item in articles:
        memory.ingest(item)
    for item in articles:
        assign(memory, item)
    expected = {frozenset(v) for v in memory.cluster_sources.values() if len(v) > 1}

    with dbmod.session_scope(fresh) as session:
        terms: Counter[str] = Counter()
        for item, doc in zip(articles, docs, strict=True):
            url = f"https://fixture.test/{doc.source_id}"
            session_article = ArticleDTO(
                source_id=doc.source_id, url=url, url_hash=url_hash(url), title=doc.title,
                body=doc.body, summary="", author=None, lang=doc.lang, category="news",
                published_at=item.published_at, published_estimated=False,
                fetched_at=item.published_at, image_url=None,
                simhash=simhash(f"{doc.title} {doc.body}"),
            )
            assert repo.upsert_article(session, session_article)
            terms.update(story_terms(doc.title, doc.body).keys())
        repo.count_terms(session, terms)

    first = cluster_pending(fresh)
    second = cluster_pending(fresh)

    with dbmod.session_scope(fresh) as session:
        grouped = {
            frozenset(v) for v in session.execute(
                select(StoryCluster.sources).where(StoryCluster.article_count > 1)
            ).scalars()
        }
        top = repo.top_stories(session, since=BASE - timedelta(days=1), limit=10, min_sources=2)

    assert first.clustered == len(docs)
    assert grouped == expected
    assert second.clustered == 0, "nothing left to cluster"
    assert sorted(row.source_count for row in top) == sorted(len(g) for g in expected)


def test_worker_catch_up_leaves_nothing_pending(fresh):
    section = load_source(ROOT / "config" / "sources" / f"{SECTION_ID}.yaml")
    run_source(
        section,
        dataclasses.replace(fresh, fetch_bodies=False),
        StubFetcher(
            section.url,
            (FIXTURES / f"{SECTION_ID}_feed.xml").read_bytes(),
            (FIXTURES / f"{SECTION_ID}_story.html").read_bytes(),
        ),
    )

    worker.catch_up(fresh)

    with dbmod.session_scope(fresh) as session:
        unclustered = session.scalar(
            select(func.count()).select_from(Article)
            .outerjoin(ArticleCluster, ArticleCluster.article_id == Article.id)
            .where(ArticleCluster.article_id.is_(None))
        )
    assert unclustered == 0
    assert health.check(fresh.heartbeat_file, 60)[0], "catch-up beats the heartbeat"


def test_only_one_clusterer_holds_the_advisory_lock(fresh):
    first, second = dbmod._Session(), dbmod._Session()
    try:
        assert repo.try_lock_clustering(first)
        assert not repo.try_lock_clustering(second)
        first.commit()
        assert repo.try_lock_clustering(second), "released on commit"
    finally:
        second.rollback()
        first.close()
        second.close()


def test_wait_for_database_gives_up_after_its_timeout(settings):
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import DBAPIError

    unreachable = dataclasses.replace(
        settings,
        database_url=make_url(settings.database_url).set(port=1).render_as_string(
            hide_password=False
        ),
    )
    dbmod._engine = dbmod._Session = None
    with pytest.raises(DBAPIError):
        dbmod.wait_for_database(unreachable, timeout=2)
