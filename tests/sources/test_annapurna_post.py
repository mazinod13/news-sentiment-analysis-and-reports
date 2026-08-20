"""Per-outlet test. Copy this file when you add an outlet -- it is yours alone,
so two people adding two outlets never touch the same file.

Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

import feedparser
import pytest

from app.ingestion.base import RawItem
from app.parsing.article import extract
from app.parsing.dates import parse_feed_datetime
from app.pipeline.normalize import canonical_url, normalize

SOURCE_ID = "annapurna-post"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.id == SOURCE_ID
    assert source.method == "rss"
    assert source.lang == "ne"
    # /rss 301-redirects to /rss/; configure the destination directly.
    assert source.url.endswith("/rss/")
    assert selectors["article"]["published_format"] == "bs"


def test_feed_parses(fixture_text):
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    assert len(feed.entries) == 20
    entry = feed.entries[0]
    assert entry.title
    assert entry.link.startswith("http")


def test_feed_has_no_publish_dates(fixture_text):
    """The defining quirk: not one item carries a date. If this ever starts
    failing, Annapurna Post fixed their feed and the body fetch can be relaxed."""
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    assert all(parse_feed_datetime(entry) is None for entry in feed.entries)


def test_feed_language_header_lies(fixture_text):
    """Feed declares en-us while publishing Nepali. sources.yaml is the authority."""
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    assert feed.feed.get("language") == "en-us"


def test_http_links_are_upgraded():
    assert canonical_url("http://annapurnapost.com/story/505752") == (
        "https://annapurnapost.com/story/505752"
    )


def test_article_extraction(outlet, fixture_text):
    source, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert article.author == "अन्नपूर्ण"
    assert "सिप्रदी ट्रेडिंग" in article.body
    assert len(article.body) > 300
    # Excluded blocks must not leak into the body.
    assert "विज्ञापन" not in article.body
    assert "तपाईंको प्रतिक्रिया" not in article.body
    assert article.image and article.image.endswith(".jpg")


def test_bikram_sambat_date_from_article_page(outlet, fixture_text):
    """भदौ ३, २०८३ -> 2026-08-19, at 14:29:53 NPT (+05:45)."""
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert article.published is not None
    assert article.published.date().isoformat() == "2026-08-19"
    assert (article.published.hour, article.published.minute) == (14, 29)
    assert article.published.utcoffset().total_seconds() == 5 * 3600 + 45 * 60


def test_normalize_marks_missing_dates_as_estimated(outlet):
    source, _ = outlet
    item = RawItem(url="http://annapurnapost.com/story/1", title="शीर्षक", published=None)
    article = normalize(item, source)

    assert article.published_estimated is True
    assert article.published_at == article.fetched_at
    assert article.lang == "ne"
    assert article.url.startswith("https://")
