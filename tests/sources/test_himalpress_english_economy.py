"""Per-outlet test for Himal Press English's Business section. This file is yours alone.

No section_of: the English site is only configured as province feeds. Everything
runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "himalpress_english-economy"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def feed(fixture_text):
    return feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))


@pytest.fixture
def article(outlet, fixture_text):
    _, selectors = outlet
    return extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)


def test_config_is_valid(outlet):
    source, _ = outlet
    assert source.method == "rss"
    assert source.lang == "en", "en.himalpress.com publishes in English"
    assert source.category == "economic"
    assert source.section_of is None
    assert source.selectors == "himalpress-province-1"


def test_feed_is_the_business_section_only(feed):
    assert feed.feed.title.startswith("Business")
    assert len(feed.entries) == 10
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"en.himalpress.com"}
    assert all("Business" in {t.term for t in e.get("tags", [])} for e in feed.entries)


def test_article_extraction_with_the_english_sites_pack(article):
    assert len(article.body) > 2500
    assert article.body.startswith("KATHMANDU: The Nepal Stock Exchange")
    assert article.published.date().isoformat() == "2026-09-15"


@pytest.mark.xfail(strict=True, reason="the pack's `@directtext` author is unsupported")
def test_author_is_extracted(article):
    assert article.author
