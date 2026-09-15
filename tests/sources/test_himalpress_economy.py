"""Per-outlet test for Himal Press's अर्थतन्त्र section. This file is yours alone.

A section source: stories that himalpress already stored as `news` are
relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "himalpress-economy"
PARENT_ID = "himalpress"


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


def test_feed_is_the_finance_section(feed):
    assert feed.feed.title.startswith("अर्थतन्त्र")
    assert len(feed.entries) == 50
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"himalpress.com"}
    assert all(e.get("published_parsed") for e in feed.entries)


def test_article_extraction_with_the_main_outlets_pack(article):
    assert len(article.body) > 2000
    assert article.body.startswith("काठमाडौँ- पुँजी बजारलाई")
    assert article.published.isoformat() == "2026-09-15T15:49:00+05:45"


@pytest.mark.xfail(strict=True, reason="himalpress pack's author selector also captures the date")
def test_author_is_just_the_byline(article):
    assert "\n" not in article.author
