"""Per-outlet test for Setopati's बजार अर्थतन्त्र section. This file is yours alone.

A `method: html` section source: /kinmel has no feed. Stories that setopati
already stored as `news` are relabelled `economic`. Everything runs off saved
fixtures; no network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract
from app.settings import ROOT
from app.sources import load_selectors

SOURCE_ID = "setopati-economy"
PARENT_ID = "setopati"
LISTING_URL = "https://www.setopati.com/kinmel"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def listing_html(fixture_text):
    return fixture_text(f"{SOURCE_ID}_listing.html")


@pytest.fixture
def story_html(fixture_text):
    return fixture_text(f"{SOURCE_ID}_story.html")


class StubFetcher:
    """Returns a saved page instead of making a request, so the real
    HTMLScraper code path runs offline."""

    def __init__(self, text: str, url: str) -> None:
        self._text, self._url = text, url

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        return FetchResult(url=self._url, status=200, text=self._text, content=b"")


def scrape(outlet, html):
    source, selectors = outlet
    return HTMLScraper(source, StubFetcher(html, LISTING_URL), selectors).fetch()


def test_config_is_a_section_of_the_main_outlet(outlet, load_outlet):
    source, selectors = outlet
    parent, _ = load_outlet(PARENT_ID)
    assert source.method == "html"
    assert source.lang == parent.lang == "ne"
    assert source.category == "economic"
    assert source.section_of == PARENT_ID
    assert "listing" in selectors


def test_listing_yields_dated_section_stories(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert len(items) == 30
    assert len({i.url for i in items}) == 30
    assert all(urlsplit(i.url).netloc == "www.setopati.com" for i in items)
    assert all("/kinmel/" in i.url for i in items)
    assert all(i.published for i in items)
    assert items[0].published.date().isoformat() == "2026-09-15"


def test_item_selector_is_scoped_to_the_section_list(listing_html):
    tree = HTMLParser(listing_html)
    assert len(tree.css("div.items")) == 38, "a bare div.items also takes header/footer widgets"
    assert len(tree.css("section.cat-list div.news-cat-list div.items")) == 30


def test_article_extraction(outlet, story_html):
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) > 1500
    assert article.body.startswith("सरकारले २५ बुँदे बजार सुधारका")
    assert article.author == "सेतोपाटी संवाददाता"
    assert article.published.isoformat() == "2026-09-15T15:34:00+05:45"


def test_main_setopati_pack_loses_this_body(story_html):
    """Why this pack does not exclude `aside`: Setopati wraps the article column
    in <aside class="left-side ...">, so the main pack deletes the body and date.
    If this starts failing, the main pack has been fixed and this pack can
    simply reuse it."""
    main_pack = load_selectors(ROOT / "config" / "selectors", PARENT_ID)
    article = extract(story_html, main_pack, source_id=PARENT_ID)
    assert article.body == ""
    assert article.published is None
