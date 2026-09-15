"""Per-outlet test for Khabarhub English's Business section. This file is yours alone.

A section source: stories that khabarhub_english already stored as `news`
are relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "khabarhub_english-economy"
PARENT_ID = "khabarhub_english"


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


def test_feed_is_the_business_section_only(feed):
    assert feed.feed.title.startswith("Business")
    assert len(feed.entries) == 12
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"english.khabarhub.com"}
    assert all("Business" in {t.term for t in e.get("tags", [])} for e in feed.entries)


def test_feed_items_carry_a_publish_time(feed):
    """The feed pubDate dates these articles first; the page date is the fallback."""
    assert all(e.get("published_parsed") for e in feed.entries)


def test_article_extraction_with_the_main_outlets_pack(article):
    assert len(article.body) > 400
    assert article.body.startswith("KATHMANDU: Finance Minister")
    assert article.author == "Khabarhub"


def test_page_publish_date_parses_despite_its_label(article):
    """The page writes "Publish Date :\\n15 September 2026 11:55 AM"; the shared
    date parser strips the label (see tests/test_dates.py)."""
    assert article.published.isoformat() == "2026-09-15T11:55:00+05:45"
