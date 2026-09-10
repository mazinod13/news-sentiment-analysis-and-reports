"""Per-outlet test for Nepal Rastra Bank.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

The ONLY Financial & market source with a usable feed. The central bank runs
WordPress and publishes a real RSS feed, so this is `method: rss` while every
other financial source needs an HTML listing.

The thing to know: the feed is the payload here. It carries a full
content:encoded and an 812-character summary, and no article body selector is
configured -- see test_no_article_body_selector_is_configured.
"""

from __future__ import annotations

import feedparser
import pytest

from app.ingestion.fetcher import FetchResult
from app.ingestion.rss import RSSScraper
from app.settings import ROOT

SOURCE_ID = "nrb"
FEED_URL = "https://www.nrb.org.np/feed/"
FEED_FIXTURE = f"{SOURCE_ID}_feed.xml"

EXPECTED_ITEMS = 5


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def feed_xml(fixture_text):
    return fixture_text(FEED_FIXTURE)


class StubFetcher:
    """Returns a saved feed instead of making a request. RSSScraper parses
    response.content, not .text, so both are populated."""

    def __init__(self, text: str, url: str) -> None:
        self._text, self._url = text, url
        self.requested: list[str] = []

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        self.requested.append(url)
        return FetchResult(
            url=self._url, status=200, text=self._text, content=self._text.encode("utf-8")
        )


def scrape(outlet, xml):
    source, selectors = outlet
    return RSSScraper(source, StubFetcher(xml, FEED_URL), selectors).fetch()


# -- config -------------------------------------------------------------------


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.id == SOURCE_ID
    assert source.method == "rss"
    assert source.category == "economic"
    # An rss source needs no listing block, and adding one would be misleading.
    assert "listing" not in selectors


def test_fixture_is_utf8_and_not_empty():
    raw = (ROOT / "tests" / "fixtures" / FEED_FIXTURE).read_bytes()
    assert raw, "feed fixture is empty -- re-capture with curl.exe -sL <url> -o <file>"
    assert not raw.startswith(b"\xff\xfe"), "UTF-16 -- re-capture with curl -o"
    raw.decode("utf-8")


# -- the feed -----------------------------------------------------------------


def test_feed_parses_cleanly(feed_xml):
    feed = feedparser.parse(feed_xml)

    assert not feed.bozo, "feed is malformed"
    assert len(feed.entries) == EXPECTED_ITEMS
    assert all(e.title for e in feed.entries)


def test_items_arrive_dated(outlet, feed_xml):
    """WordPress emits proper RFC-822 pubDates, so unlike most sources in this
    project nothing here falls back to fetch time."""
    items = scrape(outlet, feed_xml)

    assert len(items) == EXPECTED_ITEMS
    assert all(i.published for i in items), "an item reached normalisation with no date"
    for item in items:
        assert item.published.utcoffset().total_seconds() == 5 * 3600 + 45 * 60


def test_feed_carries_its_own_summary(outlet, feed_xml):
    """The reason no article body selector is configured: the feed already
    holds the text. Every item is useful before any article fetch."""
    items = scrape(outlet, feed_xml)

    assert all(i.summary for i in items)
    assert max(len(i.summary) for i in items) > 400


def test_feed_also_ships_full_content(feed_xml):
    """content:encoded is richer still (2312 chars in the fixture). Not used
    today, but it is the obvious place to go if summaries prove too short --
    much better than guessing at the article page's DOM."""
    feed = feedparser.parse(feed_xml)

    first = feed.entries[0]
    assert first.get("content"), "content:encoded disappeared from the feed"
    assert len(first.content[0].value) > len(first.summary)


def test_no_article_body_selector_is_configured(outlet):
    """Deliberate, not an oversight.

    The article page's prose did not respond to entry-content, post-content or
    article p. Guessing a container would replace a good 812-character feed
    summary with navigation chrome -- silently, and only visible much later as
    nonsense sentiment scores. Revisit only with a story fixture in hand.
    """
    _, selectors = outlet

    assert "body" not in selectors["article"]


def test_percent_encoded_devanagari_links_survive(outlet, feed_xml):
    """Links are percent-encoded Devanagari slugs
    (https://www.nrb.org.np/2026/08/%e0%a4%b5...). Ugly but valid -- this pins
    that canonical_url does not mangle or collapse them."""
    items = scrape(outlet, feed_xml)

    assert all(i.url.startswith("https://www.nrb.org.np/") for i in items)
    assert len({i.url for i in items}) == len(items)
    assert any("%e0%a4" in i.url for i in items)
