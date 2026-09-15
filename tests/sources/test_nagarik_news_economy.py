"""Per-outlet test for Nagarik News's अर्थ section. This file is yours alone.

A `method: html` section source: the advertised /feed/economy is the main feed
in disguise. Stories that nagarik_news already stored as `news` are relabelled
`economic`. Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract

SOURCE_ID = "nagarik_news-economy"
PARENT_ID = "nagarik_news"
LISTING_URL = "https://nagariknews.nagariknetwork.com/economy"


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
    assert source.lang == parent.lang == "ne"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert "listing" in selectors


def test_listing_yields_section_stories_with_machine_dates(outlet, listing_html):
    source, selectors = outlet
    items = HTMLScraper(source, StubFetcher(listing_html, LISTING_URL), selectors).fetch()

    assert len(items) == 24
    assert len({i.url for i in items}) == 24
    assert {urlsplit(i.url).netloc for i in items} == {"nagariknews.nagariknetwork.com"}
    assert all("/economy/" in i.url for i in items)
    # data-pdate carries the time, unlike the visible Bikram Sambat text.
    assert all(i.published for i in items)
    assert items[0].published.isoformat() == "2026-09-15T13:02:07+05:45"


def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 600
    assert article.body.startswith("धितोपत्र बजारमा सेयर कारोबार")
    assert article.author == "नागरिक"
    assert article.published.date().isoformat() == "2026-09-15"
