"""Per-outlet test for ShareSansar.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` market-news site. It sets ng-app but is fully
server-rendered, and its listing section contains a search form that makes
the obvious card selector wrong.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "sharesansar"
LISTING_URL = "https://www.sharesansar.com/category/latest"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 10
FIRST_DATE_RAW = 'Thursday, September 10, 2026'
FIRST_DATE_ISO = "2026-09-10"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def listing_html(fixture_text):
    return fixture_text(LISTING_FIXTURE)


@pytest.fixture
def story_html(fixture_text):
    return fixture_text(STORY_FIXTURE)


class StubFetcher:
    """Returns a saved page instead of making a request, so the real
    HTMLScraper code path runs offline."""

    def __init__(self, text: str, url: str) -> None:
        self._text, self._url = text, url
        self.requested: list[str] = []

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        self.requested.append(url)
        return FetchResult(url=self._url, status=200, text=self._text, content=b"")


def scrape(outlet, html):
    source, selectors = outlet
    return HTMLScraper(source, StubFetcher(html, LISTING_URL), selectors).fetch()


# -- config -------------------------------------------------------------------


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.id == SOURCE_ID
    assert source.method == "html"
    assert source.category == "economic"
    assert "listing" in selectors, "an html source needs a listing block"


def test_fixtures_are_utf8_and_not_empty():
    for name in (LISTING_FIXTURE, STORY_FIXTURE):
        raw = (ROOT / "tests" / "fixtures" / name).read_bytes()
        assert raw, f"{name} is empty -- re-capture with curl.exe -sL <url> -o <file>"
        assert not raw.startswith(b"\xff\xfe"), f"{name} is UTF-16 -- re-capture with curl -o"
        raw.decode("utf-8")


# -- listing page -------------------------------------------------------------


def test_listing_yields_unique_dated_items(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert len(items) == EXPECTED_ITEMS
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.title for i in items)
    assert all(i.published for i in items), "an item reached normalisation with no date"


def test_date_parses_in_this_hosts_format(outlet, listing_html):
    _, selectors = outlet
    card = HTMLParser(listing_html).css(selectors["listing"]["item"])[0]

    raw = select_one(card, selectors["listing"]["published"])

    assert raw == FIRST_DATE_RAW
    parsed = parse_datetime(raw, fmt=selectors["listing"]["published_format"])
    assert parsed.date().isoformat() == FIRST_DATE_ISO
    assert parsed.utcoffset().total_seconds() == 5 * 3600 + 45 * 60


# -- the outlet's own traps ---------------------------------------------------


def test_card_is_not_the_surrounding_section(outlet, listing_html):
    """The outlet's signature trap.

    `div.news-list` looks like the card list and is not: it is the whole
    section, and it contains the company/date SEARCH FORM as well as the
    results. Selecting it yields one enormous item whose "title" is a form.
    `div.featured-news-list` is the actual card.
    """
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    assert selectors["listing"]["item"] == "div.featured-news-list"

    section = tree.css_first("div.news-list")
    assert section is not None, "fixture has no div.news-list -- trap may be gone"
    assert section.css("form"), "the section should contain the search form"
    assert len(tree.css("div.featured-news-list")) == EXPECTED_ITEMS


def test_listing_page_is_fully_server_rendered(listing_html):
    """ShareSansar's HOMEPAGE sets ng-app, which is the marker that got
    psc.gov.np and fenegosida.org written off as SPAs. It is decoration there,
    and this listing page does not carry it at all -- the HTML holds the whole
    list. Worth pinning: if the listing ever moves behind Angular, every
    selector in this pack goes quiet at once rather than erroring.
    """
    tree = HTMLParser(listing_html)

    assert len((tree.body.text() or "").strip()) > 50_000
    assert len(tree.css("div.featured-news-list")) == EXPECTED_ITEMS


def test_article_body_is_real_prose(outlet, story_html):
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) > 1000

