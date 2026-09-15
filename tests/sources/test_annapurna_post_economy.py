"""Per-outlet test for Annapurna Post's अर्थतन्त्र section. This file is yours alone.

A `method: html` section source: /rss/economy/ is the main feed in disguise.
Stories that annapurna-post already stored as `news` are relabelled `economic`.
Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract

SOURCE_ID = "annapurna-post-economy"
PARENT_ID = "annapurna-post"
LISTING_URL = "https://annapurnapost.com/category/economy/"


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
    assert selectors["article"]["published_format"] == "bs"


def test_listing_yields_absolute_story_urls(outlet, listing_html):
    source, selectors = outlet
    items = HTMLScraper(source, StubFetcher(listing_html, LISTING_URL), selectors).fetch()

    assert len(items) == 20
    assert len({i.url for i in items}) == 20
    assert {urlsplit(i.url).netloc for i in items} == {"annapurnapost.com"}
    assert all("/story/" in i.url for i in items)


def test_item_selector_skips_the_popular_rail(outlet, listing_html):
    """A bare div.grid__card also takes the 4-card लोकप्रिय rail."""
    _, selectors = outlet
    tree = HTMLParser(listing_html)
    assert len(tree.css("div.grid__card")) == 24
    assert len(tree.css(selectors["listing"]["item"])) == 20


def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 1000
    assert article.body.startswith("काठमाडौं : सेयर बजार सुर्धाने")
    assert article.author == "अन्नपूर्ण"
    assert article.published.isoformat() == "2026-09-15T15:37:48+05:45"
