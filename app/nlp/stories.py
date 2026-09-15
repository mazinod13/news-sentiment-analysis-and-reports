"""Story clustering: the same event from several outlets becomes one story.

simhash (app/pipeline/dedupe.py) only catches the same *text*. Five outlets
reporting one NEPSE jump in their own words measured 22-36 bits apart -- far
past its threshold -- so each was stored and counted as a separate event.

Here an article joins the story of its most similar recent article:

    terms       noun-like stems of title + body, title counted twice
    weights     TF-IDF, so filler every finance story shares (प्रतिशत, करोड,
                कारोबार) counts for little and event words (नेप्से, घरजग्गा) a lot
    similarity  cosine of the two weight vectors
    join        best match >= JOIN_THRESHOLD, same language, published within
                WINDOW either side; otherwise start a new story

Plain keyword overlap could not separate same-event pairs from unrelated ones
on saved pages; TF-IDF cosine could (see tests/test_stories.py).

IDF must describe the whole corpus, not just what has been clustered so far.
Counted only at clustering time, the first stories after a deploy saw two or
three documents, IDF could not yet tell filler from event words, and an
unrelated real-estate story scored 0.24 against a NEPSE story. So terms are
counted when an article is STORED (app/pipeline/run.py), and `db upgrade`
counts every article already in the database.

The algorithm talks to a ClusterStore, so the tests run exactly this code in
memory and production runs it against Postgres (app/storage/repositories.py).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.nlp import NN
from app.nlp.tokens import tokenize

JOIN_THRESHOLD = 0.25
WINDOW = timedelta(hours=48)
# Terms kept per article. Enough for a stable vector, small enough to store.
MAX_TERMS = 60
# How many of an article's most distinctive terms a candidate must share one of.
QUERY_TERMS = 12
MAX_CANDIDATES = 400
TITLE_WEIGHT = 2
_MAX_TERM_LENGTH = 100


@dataclass(frozen=True)
class StoryArticle:
    article_id: int
    source_id: str
    title: str
    lang: str
    published_at: datetime
    terms: dict[str, int]


@dataclass(frozen=True)
class Member:
    """An already-clustered article, as a candidate to join."""

    article_id: int
    cluster_id: int
    terms: dict[str, int]


@dataclass(frozen=True)
class Assignment:
    cluster_id: int
    similarity: float
    joined: bool


class ClusterStore(Protocol):
    # Both cover every STORED article, clustered or not -- see the module docstring.
    def corpus_size(self) -> int: ...

    def document_frequencies(self, terms: Iterable[str]) -> dict[str, int]: ...

    def candidates(
        self, *, lang: str, start: datetime, end: datetime, any_of_terms: list[str], limit: int
    ) -> list[Member]: ...

    def create_cluster(self, article: StoryArticle) -> int: ...

    def join_cluster(self, cluster_id: int, article: StoryArticle) -> None: ...

    def add_member(self, article: StoryArticle, cluster_id: int, similarity: float) -> None: ...


def story_terms(title: str | None, body: str | None) -> dict[str, int]:
    """Noun-like stems with counts; the title counts TITLE_WEIGHT times."""
    counts: Counter[str] = Counter()
    for weight, text in ((TITLE_WEIGHT, title), (1, body)):
        for token in tokenize(text):
            if token.tag == NN and 2 <= len(token.stem) <= _MAX_TERM_LENGTH:
                counts[token.stem] += weight
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TERMS])


def _idf(term: str, dfs: dict[str, int], corpus_size: int) -> float:
    # Smoothed, so a term no document has yet still gets a finite weight.
    return math.log((corpus_size + 1) / (dfs.get(term, 0) + 1)) + 1


def vector(terms: dict[str, int], dfs: dict[str, int], corpus_size: int) -> dict[str, float]:
    weights = {t: (1 + math.log(c)) * _idf(t, dfs, corpus_size) for t, c in terms.items() if c > 0}
    norm = math.sqrt(sum(w * w for w in weights.values()))
    return {t: w / norm for t, w in weights.items()} if norm else {}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def query_terms(
    terms: dict[str, int], dfs: dict[str, int], corpus_size: int, k: int = QUERY_TERMS
) -> list[str]:
    """The article's k most distinctive terms -- what a candidate must share."""
    weights = vector(terms, dfs, corpus_size)
    return [t for t, _ in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def assign(
    store: ClusterStore,
    article: StoryArticle,
    *,
    threshold: float = JOIN_THRESHOLD,
    window: timedelta = WINDOW,
) -> Assignment:
    """Put one article into a story, creating the story if nothing matches."""
    best: tuple[int, float] | None = None

    if article.terms:
        corpus_size = store.corpus_size()
        wanted = query_terms(article.terms, store.document_frequencies(article.terms), corpus_size)
        candidates = store.candidates(
            lang=article.lang,
            start=article.published_at - window,
            end=article.published_at + window,
            any_of_terms=wanted,
            limit=MAX_CANDIDATES,
        )
        if candidates:
            vocabulary = set(article.terms).union(*(m.terms for m in candidates))
            dfs = store.document_frequencies(vocabulary)
            own = vector(article.terms, dfs, corpus_size)
            for member in candidates:
                score = cosine(own, vector(member.terms, dfs, corpus_size))
                if score >= threshold and (best is None or score > best[1]):
                    best = (member.cluster_id, score)

    if best is None:
        cluster_id, similarity, joined = store.create_cluster(article), 1.0, False
    else:
        cluster_id, similarity, joined = best[0], best[1], True
        store.join_cluster(cluster_id, article)

    similarity = round(similarity, 4)
    store.add_member(article, cluster_id, similarity)
    return Assignment(cluster_id, similarity, joined)
