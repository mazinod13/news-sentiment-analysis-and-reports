"""Per-outlet test for Bizmandu's News desk. This file is yours alone.

A plain `news` source, not a section: the desk mixes sports, politics and
lifestyle with business. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

SOURCE_ID = "bizmandu-news"
PARENT_ID = "bizmandu"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def feed(fixture_text):
    return feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))


def test_config_is_a_plain_news_source(outlet, load_outlet):
    source, _ = outlet
    parent, _ = load_outlet(PARENT_ID)
    assert source.method == "rss"
    assert source.lang == parent.lang == "ne"
    assert source.category == "news"
    # As a section it would have to be `economic`, relabelling politics stories.
    assert source.section_of is None
    assert source.selectors == parent.selectors, "same site template -- reuse the main pack"


def test_feed_is_the_news_desk(feed):
    assert feed.feed.title == "News - BIZMANDU"
    assert len(feed.entries) == 15
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"bizmandu.com"}
    assert all("News" in {t.term for t in e.get("tags", [])} for e in feed.entries)


def test_desk_is_not_finance_only(feed):
    """Why this is `news` and not an economic section."""
    tags = {t.term for e in feed.entries for t in e.get("tags", [])}
    assert {"Sports", "Politics"} <= tags


def test_overlaps_the_main_feed(feed, fixture_text):
    main = feedparser.parse(fixture_text(f"{PARENT_ID}_feed.xml"))
    shared = {e.link for e in feed.entries} & {e.link for e in main.entries}
    assert len(shared) == 7
