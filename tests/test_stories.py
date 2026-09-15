"""Story clustering, run in memory on real saved article pages. No database, no network.

The in-memory store below implements the same ClusterStore protocol as
DbClusterStore, so `assign` is exactly the code that runs in production. As in
production, every article's terms are counted when it is stored -- before any
clustering -- so IDF describes the whole corpus.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

import pytest

from app.nlp.stories import (
    JOIN_THRESHOLD,
    Member,
    StoryArticle,
    assign,
    cosine,
    query_terms,
    story_terms,
    vector,
)
from app.parsing.article import extract
from app.settings import NPT, ROOT
from app.sources import load_selectors, load_source

FIXTURES = ROOT / "tests" / "fixtures"
BASE = datetime(2026, 9, 15, 9, 0, tzinfo=NPT)

# Same events, identified by reading the saved pages.
NEPSE_NEPALI = {   # NEPSE jumps 48.57 points after the capital-market reform plan
    "annapurna-post-economy", "himalpress-economy", "setopati-economy",
    "nagarik_news-economy", "onlinekhabar_nepali-economy",
    "bizmandu",   # "स्वर्णिमको कार्ययोजनाले सेयर बजारमा हरियाली, नेप्सेले हाफ सेन्चुरी..."
}
NEPSE_ENGLISH = {"himalpress_english-economy", "onlinekhabar_english-economy"}
REAL_ESTATE = {"ekantipur-economy", "khabarhub_nepali-economy"}   # NRB real-estate figures

_OG_TITLE = re.compile(
    r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']*)"
    r"|<meta[^>]+content=[\"']([^\"']*)[\"'][^>]+property=[\"']og:title"
)


@dataclass(frozen=True)
class Doc:
    source_id: str
    lang: str
    title: str
    body: str


@lru_cache(maxsize=1)
def saved_articles() -> tuple[Doc, ...]:
    docs = []
    for path in sorted(FIXTURES.glob("*_story.html")):
        source_id = path.name[: -len("_story.html")]
        source = load_source(ROOT / "config" / "sources" / f"{source_id}.yaml")
        selectors = load_selectors(ROOT / "config" / "selectors", source.selectors)
        raw = path.read_text(encoding="utf-8")
        try:
            body = extract(raw, selectors, source_id=source_id).body
        except Exception:   # the BBC packs are broken; not this test's concern
            continue
        if len(body) < 100:
            continue
        match = _OG_TITLE.search(raw)
        title = html.unescape(match.group(1) or match.group(2)).strip() if match else ""
        docs.append(Doc(source_id, source.lang, title, body))
    return tuple(docs)


class MemoryStore:
    """ClusterStore in memory, mirroring DbClusterStore and ingest-time counting."""

    def __init__(self) -> None:
        self.documents = 0
        self.dfs: Counter[str] = Counter()
        self.members: list[tuple[Member, str, datetime]] = []
        self.cluster_sources: dict[int, list[str]] = {}

    def ingest(self, article: StoryArticle) -> None:
        """What run_source + repositories.count_terms do when an article is stored."""
        self.documents += 1
        self.dfs.update(article.terms.keys())

    def corpus_size(self) -> int:
        return self.documents

    def document_frequencies(self, terms):
        return {term: self.dfs[term] for term in terms if term in self.dfs}

    def candidates(self, *, lang, start, end, any_of_terms, limit):
        wanted = set(any_of_terms)
        found = [
            member for member, member_lang, published in self.members
            if member_lang == lang and start <= published <= end and wanted & member.terms.keys()
        ]
        return found[-limit:]

    def create_cluster(self, article):
        cluster_id = len(self.cluster_sources) + 1
        self.cluster_sources[cluster_id] = []
        return cluster_id

    def join_cluster(self, cluster_id, article):
        pass

    def add_member(self, article, cluster_id, similarity):
        member = Member(article.article_id, cluster_id, article.terms)
        self.members.append((member, article.lang, article.published_at))
        self.cluster_sources[cluster_id].append(article.source_id)

    def cluster_of(self, source_id: str) -> int:
        return next(c for c, sources in self.cluster_sources.items() if source_id in sources)


def article(article_id, title, body, *, source_id="x", lang="ne", at=BASE):
    return StoryArticle(article_id, source_id, title, lang, at, story_terms(title, body))


def store_and_assign(store: MemoryStore, new: StoryArticle):
    store.ingest(new)
    return assign(store, new)


@pytest.fixture(scope="module")
def clustered() -> MemoryStore:
    """The saved pages as a backfill: all stored first, then clustered in order."""
    store = MemoryStore()
    articles = [
        article(i + 1, doc.title, doc.body, source_id=doc.source_id, lang=doc.lang,
                at=BASE + timedelta(minutes=i))
        for i, doc in enumerate(saved_articles())
    ]
    for new in articles:
        store.ingest(new)
    for new in articles:
        assign(store, new)
    return store


class TestTerms:
    def test_title_counts_twice_and_verbs_are_dropped(self):
        terms = story_terms("नेप्से बढ्यो", "नेप्से परिसूचक बढेको छ।")
        assert terms["नेप्से"] == 3          # twice from the title, once from the body
        assert "परिसूचक" in terms
        assert not {"बढ्यो", "बढेको", "छ"} & terms.keys()

    def test_identical_articles_are_fully_similar(self):
        terms = story_terms("घरजग्गा कारोबार बढ्यो", "घरजग्गा कारोबार प्रतिशतले बढेको छ।")
        a = vector(terms, {}, 10)
        assert cosine(a, a) == pytest.approx(1.0)

    def test_rare_terms_are_the_query(self):
        """A term every document has cannot identify an event."""
        terms = {"प्रतिशत": 5, "नेप्से": 1}
        assert query_terms(terms, {"प्रतिशत": 99, "नेप्से": 1}, 100, k=1) == ["नेप्से"]


class TestSavedArticles:
    def test_six_nepali_outlets_on_one_nepse_jump_are_one_story(self, clustered):
        assert len({clustered.cluster_of(s) for s in NEPSE_NEPALI}) == 1

    def test_english_outlets_on_the_same_jump_are_one_story(self, clustered):
        assert len({clustered.cluster_of(s) for s in NEPSE_ENGLISH}) == 1

    def test_two_outlets_on_the_same_real_estate_figures_are_one_story(self, clustered):
        assert len({clustered.cluster_of(s) for s in REAL_ESTATE}) == 1

    def test_different_events_stay_separate_stories(self, clustered):
        stories = {clustered.cluster_of(next(iter(g)))
                   for g in (NEPSE_NEPALI, NEPSE_ENGLISH, REAL_ESTATE)}
        assert len(stories) == 3
        # Unrelated pages -- Apple launch, suicide-prevention MoU, stolen bikes --
        # must each remain alone.
        for source_id in ("sharesansar", "moha", "traffic-police"):
            assert clustered.cluster_sources[clustered.cluster_of(source_id)] == [source_id]

    def test_no_unexpected_merges(self, clustered):
        expected = [NEPSE_NEPALI, NEPSE_ENGLISH, REAL_ESTATE]
        merged = [set(s) for s in clustered.cluster_sources.values() if len(s) > 1]
        assert sorted(map(sorted, merged)) == sorted(map(sorted, expected))

    def test_idf_needs_the_whole_corpus(self):
        """Why terms are counted at ingest, not at clustering.

        Counted at clustering time, IDF knew only the articles clustered before
        -- here the pages ahead of eKantipur's real-estate story -- and could not
        yet tell that कारोबार (trading) is filler, so an unrelated NEPSE story
        scored almost at the join threshold. Counted over every stored article,
        the same pair falls well clear of it.
        """
        docs = list(saved_articles())
        position = next(i for i, d in enumerate(docs) if d.source_id == "ekantipur-economy")
        real_estate = docs[position]
        nepse = next(d for d in docs if d.source_id == "annapurna-post-economy")
        a = story_terms(real_estate.title, real_estate.body)
        b = story_terms(nepse.title, nepse.body)

        def score(counted):
            dfs = Counter(t for d in counted for t in story_terms(d.title, d.body))
            return cosine(vector(a, dfs, len(counted)), vector(b, dfs, len(counted)))

        clustered_so_far = score(docs[:position])
        whole_corpus = score(docs)
        assert whole_corpus < clustered_so_far
        assert whole_corpus < JOIN_THRESHOLD - 0.05


class TestRules:
    TITLE = "घरजग्गा कारोबार ४.७४ प्रतिशतले बढ्यो"
    BODY = "राष्ट्र बैंकका अनुसार गत वर्ष घरजग्गा कारोबार संख्या ४.७४ प्रतिशत र राजस्व बढेको छ।"

    def test_a_copy_inside_the_window_joins(self):
        store = MemoryStore()
        first = store_and_assign(store, article(1, self.TITLE, self.BODY))
        second = store_and_assign(
            store, article(2, self.TITLE, self.BODY, at=BASE + timedelta(hours=20))
        )
        assert not first.joined
        assert second.joined and second.cluster_id == first.cluster_id
        assert second.similarity >= JOIN_THRESHOLD

    def test_the_same_words_days_later_are_a_new_story(self):
        store = MemoryStore()
        first = store_and_assign(store, article(1, self.TITLE, self.BODY))
        later = store_and_assign(
            store, article(2, self.TITLE, self.BODY, at=BASE + timedelta(days=3))
        )
        assert not later.joined and later.cluster_id != first.cluster_id

    def test_languages_never_share_a_story(self):
        store = MemoryStore()
        text = "Nepse rises 48.57 points as turnover climbs"
        first = store_and_assign(store, article(1, text, text, lang="en"))
        other = store_and_assign(store, article(2, text, text, lang="ne"))
        assert other.cluster_id != first.cluster_id

    def test_an_article_with_no_terms_starts_its_own_story(self):
        store = MemoryStore()
        result = store_and_assign(store, article(1, "", "२०८३।०५।१६ ।"))
        assert not result.joined
