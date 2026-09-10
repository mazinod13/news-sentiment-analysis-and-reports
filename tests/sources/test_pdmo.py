"""Per-outlet test for Public Debt Management Office.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` government portal on the standard Government-of-Nepal CMS.
It also publishes an RSS feed that parses cleanly and points at somebody
else's website -- see the trap test below.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "pdmo"
LISTING_URL = "https://pdmo.gov.np/category/cb-notice"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 4
FIRST_DATE_RAW = '२४ भदौ, २०८३'
FIRST_DATE_ISO = "2026-09-09"


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


def test_this_outlet_is_html_not_rss(outlet):
    """The outlet's signature trap, and it is a genuinely dangerous one.

    PDMO publishes a feed at /rss that parses CLEANLY with 20 entries, so it
    looks like the obvious configuration. But every <link> in it points at
    http://ictsamachar.com//story/<id> -- an unrelated domain -- and no entry
    carries a date. Configuring this as method: rss would file another site's
    URLs under PDMO's name, and nothing downstream would notice. Checked
    2026-09-10.
    """
    source, _ = outlet

    assert source.method == "html"
    assert "/rss" not in source.url


def test_listing_takes_the_category_cards_not_the_site_wide_widget(outlet, listing_html):
    """PDMO runs the standard Government-of-Nepal CMS, so it inherits that
    family's trap: `div.slider__item` is a site-wide widget, not this
    category's list."""
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    assert selectors["listing"]["item"] == "div.grid__card"

    widget = len(tree.css("div.slider__item"))
    cards = len(tree.css("div.grid__card"))
    assert widget > cards * 5, "expected the site-wide widget to dwarf the category"


def test_article_body_is_a_placeholder(outlet, story_html):
    """Documents reality, not a success. Like the rest of this CMS family the
    body cleans down to the literal "A\\n\\nA" -- four characters. The debt
    notice is an attachment we do not fetch, so the listing title and date are
    the payload. If this starts failing with real text, the portal began
    publishing prose -- good news, and worth revisiting."""
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) < 20

