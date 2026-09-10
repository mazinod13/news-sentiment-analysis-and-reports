"""Per-outlet test for Ministry of Home Affairs.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` government portal. Its listing is a Livewire-rendered
TABLE, and its dates are all-numeric Bikram Sambat in Devanagari numerals --
the shape that made app/parsing/dates.py grow a numeric BS pattern.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "moha"
LISTING_URL = "https://moha.gov.np/page/news"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 10
FIRST_DATE_RAW = '२०८१-०९-०८'
FIRST_DATE_ISO = "2024-12-23"


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
    assert sum(1 for i in items if i.published) >= 10


def test_date_parses_in_this_hosts_format(outlet, listing_html):
    _, selectors = outlet
    card = HTMLParser(listing_html).css(selectors["listing"]["item"])[0]

    raw = select_one(card, selectors["listing"]["published"])

    assert raw == FIRST_DATE_RAW
    fmt = selectors["listing"]["published_format"]
    assert parse_datetime(raw, fmt=fmt).date().isoformat() == FIRST_DATE_ISO


# -- the outlet's own traps ---------------------------------------------------


def test_listing_is_a_table_not_a_card_grid(outlet, listing_html):
    """MOHA renders its listing as a Livewire TABLE. Reaching for a card class
    finds nothing at all here."""
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    assert selectors["listing"]["item"] == "table.table tbody tr"
    assert len(tree.css("table.table tbody tr")) == EXPECTED_ITEMS
    assert not tree.css("div.grid__card"), "this is not the GoN CMS card grid"


def test_date_is_all_numeric_bikram_sambat(listing_html):
    """The outlet's signature trap, and the reason app/parsing/dates.py grew a
    numeric Bikram Sambat pattern.

    The date reads "२०८१-०९-०८". Before this outlet the parser only understood
    month-name forms, so every item lost its date. The numeric pattern is gated
    on Devanagari numerals: the ASCII form is indistinguishable from an ISO
    Gregorian date, and parse_datetime tries BS before ISO under `auto`.
    """
    tree = HTMLParser(listing_html)
    raw = select_one(tree.css("table.table tbody tr")[0], "td.d-none.d-md-table-cell")

    assert any(d in raw for d in "०१२३४५६७८९"), "expected Devanagari numerals"
    assert "-" in raw and not any(c.isalpha() for c in raw), "expected an all-numeric date"
    # 2081 BS is 2024-25 CE. Read as Gregorian it would land in the 2080s.
    assert parse_datetime(raw, fmt="bs").year < 2030


def test_article_body_is_real_prose(outlet, story_html):
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) > 500

