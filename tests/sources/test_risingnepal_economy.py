"""Per-outlet test for The Rising Nepal's Business section. This file is yours alone.

A section source: stories that risingnepal already stored as `news` are
relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "risingnepal-economy"
PARENT_ID = "risingnepal"


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


def test_config_is_a_section_of_the_main_outlet(outlet, load_outlet):
    source, _ = outlet
    parent, _ = load_outlet(PARENT_ID)
    assert source.id == SOURCE_ID
    assert source.method == "rss"
    assert source.lang == parent.lang == "en"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert source.selectors == parent.selectors, "same site template -- reuse the main pack"
    # Section feeds are /rss/<slug>; /categories/<slug>/rss 404s.
    assert source.url.endswith("/rss/business")


def test_feed_parses_with_dated_items(feed):
    assert len(feed.entries) == 10
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"risingnepaldaily.com"}
    assert feed.entries[0].title.startswith("Over 52,700 tonnes of LPG imported")
    assert all(e.get("published_parsed") for e in feed.entries)


def test_article_extraction_with_the_main_outlets_pack(article):
    assert len(article.body) > 5000
    assert article.body.startswith("Kathmandu, Sept. 15: The energy sector")
    assert article.published.date().isoformat() == "2026-09-15"


@pytest.mark.xfail(strict=True, reason="risingnepal pack's author selector returns the dateline")
def test_author_is_not_the_dateline(article):
    assert article.author and not article.author.startswith("Kathmandu")
