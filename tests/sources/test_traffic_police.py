"""Per-outlet test for Nepal Police Traffic Directorate.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` government portal, and the richest of them: the listing
carries the FULL story text, and two thirds of its cards are hidden behind a
"more" button while still being present in the server HTML.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "traffic-police"
LISTING_URL = "https://traffic.nepalpolice.gov.np/news/latest-news/"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 33
FIRST_DATE_RAW = '२०८३-०४-२८'
FIRST_DATE_ISO = "2026-08-13"


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
    assert source.category == "govt"
    assert "listing" in selectors, "an html source needs a listing block"


def test_fixtures_are_utf8_and_not_empty():
    for name in (LISTING_FIXTURE, STORY_FIXTURE):
        raw = (ROOT / "tests" / "fixtures" / name).read_bytes()
        assert raw, f"{name} is empty -- re-capture with curl.exe -sL <url> -o <file>"
        assert not raw.startswith(b"\xff\xfe"), f"{name} is UTF-16 -- re-capture with curl -o"
        raw.decode("utf-8")


def test_date_is_configured_on_the_listing(outlet):
    """Article pages on this portal do not carry a usable date, so the listing
    is the only source. Moving `published` into `article` makes every item fall
    back to fetch time and silently report today."""
    _, selectors = outlet

    assert selectors["listing"]["published"]
    assert "published" not in selectors["article"]


# -- listing page -------------------------------------------------------------


def test_listing_yields_unique_dated_items(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert len(items) == EXPECTED_ITEMS
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.title for i in items)
    assert sum(1 for i in items if i.published) >= 33


def test_date_parses_in_this_hosts_format(outlet, listing_html):
    _, selectors = outlet
    card = HTMLParser(listing_html).css(selectors["listing"]["item"])[0]

    raw = select_one(card, selectors["listing"]["published"])

    assert raw == FIRST_DATE_RAW
    fmt = selectors["listing"]["published_format"]
    assert parse_datetime(raw, fmt=fmt).date().isoformat() == FIRST_DATE_ISO


# -- the outlet's own traps ---------------------------------------------------


def test_listing_carries_the_full_story_text(outlet, listing_html):
    """This portal is unusual and worth exploiting: the whole story is already
    in the listing, so items are useful before any article fetch."""
    items = scrape(outlet, listing_html)

    assert all(i.summary for i in items), "every card should carry its own text"
    assert max(len(i.summary) for i in items) > 300


def test_hidden_cards_are_still_ingested(listing_html):
    """22 of the 33 cards carry `d-none` and are revealed by a "more" button.
    They are fully present in the server HTML, so the scraper sees them. Do not
    "fix" this by filtering on visibility -- it would drop two thirds of the
    feed.
    """
    tree = HTMLParser(listing_html)

    total = tree.css("div.textbox-04")
    hidden = [c for c in total if "d-none" in (c.attributes.get("class") or "")]

    assert len(hidden) > len(total) / 2
    assert len(total) == EXPECTED_ITEMS


def test_date_is_all_numeric_bikram_sambat(listing_html):
    """Same numeric BS shape as moha -- see app/parsing/dates.py."""
    raw = select_one(HTMLParser(listing_html).css("div.textbox-04")[0], "a span")

    assert any(d in raw for d in "०१२३४५६७८९")
    assert parse_datetime(raw, fmt="bs").year < 2030


def test_article_body_is_real_prose(outlet, story_html):
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) > 1000

