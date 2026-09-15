"""Per-outlet test for OnlineKhabar Nepali's Business section. This file is yours alone.

A section source: stories that onlinekhabar_nepali already stored as `news`
are relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "onlinekhabar_nepali-economy"
PARENT_ID = "onlinekhabar_nepali"


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


def test_feed_is_the_business_section(feed):
    assert feed.feed.title.startswith("बिजनेस")
    assert len(feed.entries) == 55
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"www.onlinekhabar.com"}


def test_section_also_carries_it_stories(feed):
    """Documents why this is not purely finance: the business section nests the
    IT desk. If this ever drops to zero, the notes can be simplified."""
    it_items = [
        e for e in feed.entries
        if "सूचना प्रविधि -समाचार" in {t.term for t in e.get("tags", [])}
    ]
    assert len(it_items) == 8


def test_article_extraction_with_the_main_outlets_pack(article):
    assert len(article.body) > 1500
    assert "नेप्से ४८.५७ अंक बढेर" in article.body
    assert article.author == "अनलाइनखबर"
    assert article.published.isoformat() == "2026-09-15T15:21:00+05:45"


@pytest.mark.xfail(strict=True, reason="onlinekhabar_nepali pack keeps .ai_summary_block")
def test_body_excludes_the_ai_summary_block(article):
    assert not article.body.startswith("News Summary")
