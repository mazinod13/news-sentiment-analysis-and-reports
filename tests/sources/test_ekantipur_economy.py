"""Per-outlet test for eKantipur's अर्थ / वाणिज्य section. This file is yours alone.

A `method: html` source: eKantipur has no RSS anywhere. No section_of, because
eKantipur is only configured as province listings. Everything runs off saved
fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract

SOURCE_ID = "ekantipur-economy"
LISTING_URL = "https://ekantipur.com/business"


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


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.method == "html"
    assert source.lang == "ne"
    assert source.category == "economic"
    assert source.section_of is None
    assert selectors["article"]["published"] == "jsonld:datePublished"


def test_listing_yields_business_stories_with_summaries(outlet, listing_html):
    source, selectors = outlet
    items = HTMLScraper(source, StubFetcher(listing_html, LISTING_URL), selectors).fetch()

    assert len(items) == 30
    assert len({i.url for i in items}) == 30
    assert {urlsplit(i.url).netloc for i in items} == {"ekantipur.com"}
    assert all("/business/2026/" in i.url for i in items)
    assert all(i.summary for i in items)


def test_article_extraction_is_lede_only(outlet, fixture_text):
    """Bodies are client-rendered; the server HTML carries only the lede."""
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert 50 < len(article.body) < 300
    assert article.author == "कान्तिपुर संवाददाता"
    assert article.published.isoformat() == "2026-09-15T14:12:13+05:45"
