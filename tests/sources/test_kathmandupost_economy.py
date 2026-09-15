"""Per-outlet test for The Kathmandu Post's Money section. This file is yours alone.

A `method: html` section source: /money/rss is the homepage, not a feed. Stories
that kathmandupost already stored as `news` are relabelled `economic`.
Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract

SOURCE_ID = "kathmandupost-economy"
PARENT_ID = "kathmandupost"
LISTING_URL = "https://kathmandupost.com/money"


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


def test_listing_yields_money_stories_with_summaries(outlet, listing_html):
    source, selectors = outlet
    items = HTMLScraper(source, StubFetcher(listing_html, LISTING_URL), selectors).fetch()

    assert len(items) == 20
    assert len({i.url for i in items}) == 20
    # Card hrefs are relative; the scraper must resolve them.
    assert {urlsplit(i.url).netloc for i in items} == {"kathmandupost.com"}
    assert all(urlsplit(i.url).path.startswith("/money/") for i in items)
    assert all(i.summary for i in items)
    assert items[0].title == "Government rolls out sweeping capital market reform plan"


def test_item_selector_skips_the_other_rails(outlet, listing_html):
    _, selectors = outlet
    tree = HTMLParser(listing_html)
    assert len(tree.css("article.article-image")) == 30
    assert len(tree.css(selectors["listing"]["item"])) == 20


def test_article_extraction_reads_the_publish_date_not_the_update(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 5000
    assert article.body.startswith("The government has unveiled a 21-point plan")
    assert article.author == "Yagya Banjade"
    # "Published at : September 15, 2026" -- date only. The "Updated at ... 09:07"
    # line is the edit time and must not be read as the publish time.
    assert article.published.isoformat() == "2026-09-15T00:00:00+05:45"
