"""Database schema (MySQL 8).

Stores what the scrapers produce, plus keywords and a criticality grade per
article in `article_analysis`, and story membership in `article_clusters`.
Sentiment, entities and embeddings are not part of the schema yet.

MySQL notes, all of which have bitten this schema:
  * timestamps go through UtcDateTime -- MySQL has no aware type;
  * bodies are MEDIUMTEXT: TEXT stops at 64 KB and a long feature would be
    silently truncated;
  * every table is utf8mb4, or Devanagari does not round-trip;
  * `article_terms` exists because MySQL has no GIN index over JSON. It is how
    "which recent article shares one of these terms" stays an indexed lookup.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.storage.types import UtcDateTime

# Article bodies run past MySQL's 64 KB TEXT limit on long features.
LongText = Text().with_variant(MEDIUMTEXT, "mysql")

# Devanagari needs the real 4-byte UTF-8, not MySQL's legacy 3-byte "utf8".
UTF8MB4 = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}


class Base(DeclarativeBase):
    pass


class SourceState(Base):
    """Runtime state per outlet. The YAML files stay the source of truth for
    configuration; this table only holds what changes at runtime."""

    __tablename__ = "source_state"
    __table_args__ = UTF8MB4

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    last_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    next_run_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_articles_url_hash"),
        Index("ix_articles_source_published", "source_id", "published_at"),
        Index("ix_articles_simhash", "simhash"),
        UTF8MB4,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(80), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(LongText, default="")
    summary: Mapped[str] = mapped_column(LongText, default="")
    author: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(2))
    category: Mapped[str] = mapped_column(String(40))
    published_at: Mapped[datetime] = mapped_column(UtcDateTime)
    # True when published_at is a fallback (fetched_at) rather than a real
    # publish time. Reports must not treat these as precise.
    published_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime)
    image_url: Mapped[str | None] = mapped_column(Text)
    simhash: Mapped[int] = mapped_column(BigInteger, default=0)


class ArticleAnalysis(Base):
    """Keywords and criticality grade for one article.

    A separate table rather than columns on `articles`, so `db upgrade` -- which
    only creates missing tables -- adds it to an existing database as is.
    """

    __tablename__ = "article_analysis"
    __table_args__ = UTF8MB4

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    # A (most critical) .. F (routine); see config/criticality.yaml.
    grade: Mapped[str] = mapped_column(String(1), index=True)
    score: Mapped[float] = mapped_column(Float)
    # True when the score was scaled down as a prevention/awareness story.
    dampened: Mapped[bool] = mapped_column(Boolean, default=False)
    # [{text, score, count}], best first
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    # [{term, tier, count, points}], most points first
    matches: Mapped[list] = mapped_column(JSON, default=list)
    analysed_at: Mapped[datetime] = mapped_column(UtcDateTime)


class StoryCluster(Base):
    """One news event, however many outlets reported it (see app/nlp/stories.py)."""

    __tablename__ = "story_clusters"
    __table_args__ = (Index("ix_story_clusters_last_published", "last_published_at"), UTF8MB4)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lang: Mapped[str] = mapped_column(String(2))
    # Headline of the article that started the story -- a label, not a summary.
    title: Mapped[str] = mapped_column(Text)
    first_published_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_published_at: Mapped[datetime] = mapped_column(UtcDateTime)
    article_count: Mapped[int] = mapped_column(Integer, default=1)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    # Distinct source ids, in the order they joined.
    sources: Mapped[list] = mapped_column(JSON, default=list)


class ArticleCluster(Base):
    """Which story an article belongs to, plus the terms it was matched on."""

    __tablename__ = "article_clusters"
    __table_args__ = (Index("ix_article_clusters_window", "lang", "published_at"), UTF8MB4)

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    cluster_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("story_clusters.id", ondelete="CASCADE"), index=True
    )
    # Cosine similarity to the best-matching article; 1.0 for a story's founder.
    similarity: Mapped[float] = mapped_column(Float)
    # {stem: count} -- read back for the similarity maths, never searched.
    # Searching happens through article_terms below.
    terms: Mapped[dict] = mapped_column(JSON, default=dict)
    # Copied from articles so the candidate query needs no join.
    lang: Mapped[str] = mapped_column(String(2))
    published_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ArticleTerm(Base):
    """One row per (clustered article, term): the candidate lookup for story
    clustering.

    PostgreSQL did this with a GIN index over JSONB (`terms ?| array[...]`).
    MySQL has no equivalent -- JSON columns cannot be indexed directly, and a
    multi-valued index would tie the schema to MySQL 8.0.17+ -- so the terms are
    also written here, where a plain index answers "which recent article shares
    any of these terms".
    """

    __tablename__ = "article_terms"
    __table_args__ = (Index("ix_article_terms_term", "term"), UTF8MB4)

    article_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("article_clusters.article_id", ondelete="CASCADE"),
        primary_key=True,
    )
    term: Mapped[str] = mapped_column(String(200), primary_key=True)


class TermStat(Base):
    """Document frequency per term over every STORED article, for IDF.

    Counted at ingest, not at clustering, so IDF describes the whole corpus
    from the first story onwards (see app/nlp/stories.py).
    """

    __tablename__ = "term_stats"
    __table_args__ = UTF8MB4

    term: Mapped[str] = mapped_column(String(200), primary_key=True)
    df: Mapped[int] = mapped_column(Integer, default=0)


class FetchLog(Base):
    __tablename__ = "fetch_log"
    __table_args__ = UTF8MB4

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        String(80), ForeignKey("source_state.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime] = mapped_column(UtcDateTime)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    not_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
