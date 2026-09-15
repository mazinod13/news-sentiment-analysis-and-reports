"""Per-outlet test for Spotlight Nepal's Economy topic. This file is yours alone.

A `method: html` section source: the topic page has no feed. Stories that
spotlightnepal already stored as `news` are relabelled `economic`. Everything
runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract

SOURCE_ID = "spotlightnepal-economy"
PARENT_ID = "spotlightnepal"
LISTING_URL = "https://www.spotlightnepal.com/topic/economy/"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def listing_html(fixture_text):
    return fixture_text(f"{SOURCE_ID}_listing.html")


class StubFetcher:
    """Returns a saved page instead of making a request, so the real
    HTMLScraper code path runs offline."""

    def __init__(self, text: str, url: str) -> None:
        self._text, self._url = text, url

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        return FetchResult(url=self._url, status=200, text=self._text, content=b"")


def test_config_is_a_section_of_the_main_outlet(outlet, load_outlet):
    source, selectors = outlet
    parent, _ = load_outlet(PARENT_ID)
    assert source.method == "html"
    assert source.lang == parent.lang == "en"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert "listing" in selectors
    # Loosely tagged topic -- not worth the 30-minute tier.
    assert source.priority == 3


def test_listing_yields_dated_stories_with_summaries(outlet, listing_html):
    source, selectors = outlet
    items = HTMLScraper(source, StubFetcher(listing_html, LISTING_URL), selectors).fetch()

    assert len(items) == 20
    assert len({i.url for i in items}) == 20
    assert {urlsplit(i.url).netloc for i in items} == {"www.spotlightnepal.com"}
    assert all(i.published and i.summary for i in items)
    assert items[0].title.startswith("ERC Furthers Initiatives")


def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 5000
    assert article.body.startswith("The Electricity Regulatory Commission (ERC)")
    assert article.author == "NEW SPOTLIGHT ONLINE"
    assert article.published.isoformat() == "2026-09-15T13:28:00+05:45"
