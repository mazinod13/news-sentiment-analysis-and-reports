"""Per-outlet test for Janakpur Today's अर्थ/वाणीज्य section. This file is yours alone.

A section source: stories that ejanakpurtoday already stored as `news` are
relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "ejanakpurtoday-economy"
PARENT_ID = "ejanakpurtoday"
SECTION = "अर्थ/वाणीज्य"   # sic -- the site's own spelling


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
    assert source.lang == parent.lang == "ne"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert source.selectors == parent.selectors, "same site template -- reuse the main pack"


def test_feed_is_the_economy_section_only(feed):
    assert feed.feed.title.startswith(SECTION)
    assert len(feed.entries) == 15
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"ejanakpurtoday.com"}
    assert all(SECTION in {t.term for t in e.get("tags", [])} for e in feed.entries)


def test_article_extraction_with_the_main_outlets_pack(article):
    assert len(article.body) > 600
    assert "भारतले नेपाललाई दैनिक ६५४ मेगावाट बिजुली दिने" in article.body
    assert article.author == "जनकपुर टुडे"
    assert article.published.isoformat() == "2026-09-15T10:45:00+05:45"


@pytest.mark.xfail(strict=True, reason="ejanakpurtoday pack's body includes the byline")
def test_body_starts_with_the_story_not_the_byline(article):
    assert not article.body.startswith("जनकपुर टुडे")
