"""Per-outlet test for OnlineKhabar English's Economy section. This file is yours alone.

A section source: stories that onlinekhabar_english already stored as `news`
are relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "onlinekhabar_english-economy"
PARENT_ID = "onlinekhabar_english"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def feed(fixture_text):
    return feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))


def test_config_is_a_section_of_the_main_outlet(outlet, load_outlet):
    source, _ = outlet
    parent, _ = load_outlet(PARENT_ID)
    assert source.id == SOURCE_ID
    assert source.method == "rss"
    assert source.lang == parent.lang == "en"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert source.selectors == parent.selectors, "same site template -- reuse the main pack"


def test_feed_is_the_economy_section_only(feed):
    assert feed.feed.title.startswith("Economy")
    assert len(feed.entries) == 20
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"english.onlinekhabar.com"}
    tagged = [e for e in feed.entries if "Economy" in {t.term for t in e.get("tags", [])}]
    assert len(tagged) == 19


def test_article_extraction_with_the_main_outlets_pack(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 800
    assert article.body.startswith("Kathmandu, September 15")
    assert article.author == "Onlinekhabar"
    assert article.published.date().isoformat() == "2026-09-15"
