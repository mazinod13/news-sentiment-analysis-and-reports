"""Per-outlet test for Ratopati's economy category. This file is yours alone.

A `method: html` source: the category feeds 404 and /feed is site-wide. No
section_of, because Ratopati is only configured as province portals. Everything
runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract

SOURCE_ID = "ratopati-economy"
LISTING_URL = "https://www.ratopati.com/category/economy"


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


def scrape(source, selectors, html):
    return HTMLScraper(source, StubFetcher(html, LISTING_URL), selectors).fetch()


def test_config_is_valid(outlet):
    source, _ = outlet
    assert source.method == "html"
    assert source.lang == "ne"
    assert source.category == "economic"
    assert source.section_of is None


def test_rate_limit_honours_robots_crawl_delay(outlet):
    """robots.txt sets Crawl-delay: 20, which the fetcher does not read itself."""
    source, _ = outlet
    assert source.rate_limit >= 20


def test_listing_yields_the_categorys_stories(outlet, listing_html):
    source, selectors = outlet
    items = scrape(source, selectors, listing_html)

    assert len(items) == 41
    assert len({i.url for i in items}) == 41
    assert {urlsplit(i.url).netloc for i in items} == {"www.ratopati.com"}
    assert all("/story/" in i.url for i in items)
    assert items[0].title == "राष्ट्र बैंकले ल्यायो ५ वर्षे फिनटेक रणनीति खाका"


def test_widening_the_item_selector_pulls_in_menu_and_trending_stories(outlet, listing_html):
    """The mega-menu reuses div.columnnews, so an unscoped selector ingests
    politics and province stories as `economic`."""
    source, selectors = outlet
    wide = {**selectors, "listing": {**selectors["listing"], "item": "div.columnnews"}}

    scoped = {i.url for i in scrape(source, selectors, listing_html)}
    widened = {i.url for i in scrape(source, wide, listing_html)}

    assert widened - scoped, "expected the unscoped selector to reach outside the category"


def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 1500
    assert article.body.startswith("काठमाडौँ । नेपाल राष्ट्र बैंकको ५ वर्षे")
    assert article.author == "रातोपाटी संवाददाता"
    # Date only: Ratopati's "०५ : १७" time format is a known shared parser gap.
    assert article.published.date().isoformat() == "2026-09-15"
