"""Per-outlet test for Bizmandu, the successor to Pahilo Post. This file is yours alone.

The main feed is site-wide, so this source is `news`; the finance desks are
section sources. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

import feedparser
import pytest

from app.parsing.article import extract
from app.settings import NPT

SOURCE_ID = "bizmandu"


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
    assert source.lang == "ne"
    assert source.category == "news"
    assert source.selectors == SOURCE_ID


def test_every_bizmandu_desk_shares_this_pack(load_outlet):
    for section in ("bizmandu-news", "bizmandu-market", "bizmandu-corporate", "bizmandu-banking"):
        source, _ = load_outlet(section)
        assert source.selectors == SOURCE_ID, section


def test_main_feed_is_site_wide(feed):
    """Why this source is `news` and the finance desks are separate sections."""
    assert feed.feed.title == "BIZMANDU"
    assert len(feed.entries) == 15
    assert {urlsplit(e.link).netloc for e in feed.entries} == {"bizmandu.com"}
    tags = {t.term for e in feed.entries for t in e.get("tags", [])}
    assert {"Politics", "Sports"} <= tags


def test_article_body_skips_the_inline_ad(article):
    assert len(article.body) > 1500
    assert article.body.startswith("काठमाडौं। सेयर बजार सुधारका लागि")


def test_author_is_the_name_not_the_whole_byline(article):
    """div.author also holds the date; only the name span is read."""
    assert article.author == "बिजमाण्डू"


def test_published_is_the_machine_readable_instant(article):
    """article:published_time is UTC; the byline shows 15:26 Nepal time."""
    assert article.published == datetime(2026, 9, 15, 15, 26, 11, tzinfo=NPT)
