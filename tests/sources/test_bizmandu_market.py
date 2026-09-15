"""Per-outlet test for Bizmandu's Market section. This file is yours alone.

A section source: stories that bizmandu already stored as `news` are
relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

SOURCE_ID = "bizmandu-market"
PARENT_ID = "bizmandu"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def feed(fixture_text):
    return feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))


def test_config_is_a_section_of_the_main_outlet(outlet, load_outlet):
    source, _ = outlet
    parent, _ = load_outlet(PARENT_ID)
    assert source.method == "rss"
    assert source.lang == parent.lang == "ne"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert source.selectors == parent.selectors, "same site template -- reuse the main pack"
    # The page is .../market.html; the feed drops the extension and keeps /content.
    assert source.url == "https://bizmandu.com/content/category/market/feed"


def test_feed_is_the_market_section(feed):
    assert feed.feed.title == "Market - BIZMANDU"
    assert len(feed.entries) == 15
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"bizmandu.com"}
    assert all(e.get("published_parsed") for e in feed.entries)
    tagged = [e for e in feed.entries if "Market" in {t.term for t in e.get("tags", [])}]
    assert len(tagged) == 12, "the rest are filed under sub-topics such as NEPSE or Gold"
