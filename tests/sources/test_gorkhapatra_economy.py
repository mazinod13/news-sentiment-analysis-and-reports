"""Per-outlet test for Gorkhapatra's अर्थ section. This file is yours alone.

A section source: stories that gorkhapatra already stored as `news` are
relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "gorkhapatra-economy"
PARENT_ID = "gorkhapatra"


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
    assert source.method == "rss"
    assert source.lang == parent.lang == "ne"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert source.selectors == parent.selectors, "same site template -- reuse the main pack"
    # /rss?category=economy is ignored by the site and returns the main feed.
    assert source.url.endswith("/rss/economy")


def test_feed_parses_with_dated_items(feed):
    assert len(feed.entries) == 10
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"gorkhapatraonline.com"}
    assert all(e.get("published_parsed") for e in feed.entries)


def test_article_extraction_with_the_main_outlets_pack(article):
    assert len(article.body) > 2000
    assert article.author == "गोरखापत्र अनलाइन"
    assert article.published.date().isoformat() == "2026-09-15"


@pytest.mark.xfail(strict=True, reason="gorkhapatra pack's body includes the meta and share blocks")
def test_body_starts_with_the_story(article):
    assert not article.body.startswith("गोरखापत्र अनलाइन")
