"""Database schema.

Stores what the scrapers produce, plus keywords and a criticality grade per
article in `article_analysis`. Sentiment, entities and embeddings are not part
of the schema yet.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceState(Base):
    """Runtime state per outlet. The YAML files stay the source of truth for
    configuration; this table only holds what changes at runtime."""

    __tablename__ = "source_state"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url_hash", name="uq_articles_url_hash"),
        Index("ix_articles_source_published", "source_id", "published_at"),
        Index("ix_articles_simhash", "simhash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(80), index=True)
    url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(String(2))
    category: Mapped[str] = mapped_column(String(40))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # True when published_at is a fallback (fetched_at) rather than a real
    # publish time. Reports must not treat these as precise.
    published_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    image_url: Mapped[str | None] = mapped_column(Text)
    simhash: Mapped[int] = mapped_column(BigInteger, default=0)


class ArticleAnalysis(Base):
    """Keywords and criticality grade for one article.

    A separate table rather than columns on `articles`, so `db upgrade` -- which
    only creates missing tables -- adds it to an existing database as is.
    """

    __tablename__ = "article_analysis"

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    # A (most critical) .. F (routine); see config/criticality.yaml.
    grade: Mapped[str] = mapped_column(String(1), index=True)
    score: Mapped[float] = mapped_column(Float)
    # True when the score was scaled down as a prevention/awareness story.
    dampened: Mapped[bool] = mapped_column(Boolean, default=False)
    # [{text, score, count}], best first
    keywords: Mapped[list] = mapped_column(JSONB, default=list)
    # [{term, tier, count, points}], most points first
    matches: Mapped[list] = mapped_column(JSONB, default=list)
    analysed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StoryCluster(Base):
    """One news event, however many outlets reported it (see app/nlp/stories.py).

    New tables rather than columns on `articles`, so `db upgrade` adds them to
    an existing database as is.
    """

    __tablename__ = "story_clusters"
    __table_args__ = (Index("ix_story_clusters_last_published", "last_published_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    lang: Mapped[str] = mapped_column(String(2))
    # Headline of the article that started the story -- a label, not a summary.
    title: Mapped[str] = mapped_column(Text)
    first_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    article_count: Mapped[int] = mapped_column(Integer, default=1)
    source_count: Mapped[int] = mapped_column(Integer, default=1)
    # Distinct source ids, in the order they joined.
    sources: Mapped[list] = mapped_column(JSONB, default=list)


class ArticleCluster(Base):
    """Which story an article belongs to, plus the terms it was matched on."""

    __tablename__ = "article_clusters"
    __table_args__ = (
        Index("ix_article_clusters_window", "lang", "published_at"),
        # Candidate lookup is "shares any of these terms": JSONB ?| needs GIN.
        Index("ix_article_clusters_terms", "terms", postgresql_using="gin"),
    )

    article_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    cluster_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("story_clusters.id", ondelete="CASCADE"), index=True
    )
    # Cosine similarity to the best-matching article; 1.0 for a story's founder.
    similarity: Mapped[float] = mapped_column(Float)
    # {stem: count}, the article's top terms
    terms: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Copied from articles so the candidate query needs no join.
    lang: Mapped[str] = mapped_column(String(2))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TermStat(Base):
    """Document frequency per term over every STORED article, for IDF.

    Counted at ingest, not at clustering, so IDF describes the whole corpus
    from the first story onwards (see app/nlp/stories.py).
    """

    __tablename__ = "term_stats"

    term: Mapped[str] = mapped_column(String(200), primary_key=True)
    df: Mapped[int] = mapped_column(Integer, default=0)


class FetchLog(Base):
    __tablename__ = "fetch_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("source_state.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_duplicate: Mapped[int] = mapped_column(Integer, default=0)
    not_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
