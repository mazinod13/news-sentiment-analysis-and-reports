"""Per-outlet test for Bizmandu's Corporate section. This file is yours alone.

A section source: stories that bizmandu already stored as `news` are
relabelled `economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import feedparser
import pytest

SOURCE_ID = "bizmandu-corporate"
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
    # 15 items over 12 days does not justify the 30-minute tier.
    assert source.priority == 3


def test_feed_is_the_corporate_section(feed):
    assert feed.feed.title == "Corporate - BIZMANDU"
    assert len(feed.entries) == 15
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"bizmandu.com"}
    assert all(e.get("published_parsed") for e in feed.entries)
    tagged = [e for e in feed.entries if "Corporate" in {t.term for t in e.get("tags", [])}]
    assert len(tagged) == 14
