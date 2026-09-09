"""Per-outlet test for Gandaki Province OCMCM.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` government portal running its OWN template -- not the CMS
the other six provincial OCMCM portals share. Its date lives in an ATTRIBUTE
on an element whose text is empty.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "ocmcm-gandaki"
LISTING_URL = "https://ocmcm.gandaki.gov.np/list/news"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 20
FIRST_DATE_RAW = '2026-09-03'
FIRST_DATE_ISO = "2026-09-03"


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
    assert sum(1 for i in items if i.published) >= 20


def test_date_parses_in_this_hosts_format(outlet, listing_html):
    _, selectors = outlet
    card = HTMLParser(listing_html).css(selectors["listing"]["item"])[0]

    raw = select_one(card, selectors["listing"]["published"])

    assert raw == FIRST_DATE_RAW
    fmt = selectors["listing"]["published_format"]
    assert parse_datetime(raw, fmt=fmt).date().isoformat() == FIRST_DATE_ISO


# -- the outlet's own traps ---------------------------------------------------


def test_date_lives_in_an_attribute_on_an_empty_element(outlet, listing_html):
    """The outlet's signature trap, and the only one of its kind in the project.

    The page ships <span class="nepaliDate" englishdate="2026-09-03"></span>.
    The visible Nepali date is written in by JavaScript we never run, so the
    element's TEXT is empty -- a text selector returns nothing at all, with no
    error. The @englishdate attribute is already ISO, which makes this the one
    outlet whose machine-readable date is cleaner than the human-readable one.
    """
    _, selectors = outlet
    card = HTMLParser(listing_html).css("li.tap-box-list")[0]

    assert selectors["listing"]["published"] == "span.nepaliDate@englishdate"
    span = card.css_first("span.nepaliDate")
    assert span is not None
    assert span.text().strip() == "", "the element gained text -- revisit the selector"
    assert select_one(card, "span.nepaliDate@englishdate") == FIRST_DATE_RAW


def test_this_is_not_the_gon_cms_pack(outlet, listing_html):
    """Gandaki does NOT run the same CMS as the other six provincial OCMCMs.
    Copying the ocmcm-koshi pack here yields zero items."""
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    assert selectors["listing"]["item"] == "li.tap-box-list"
    assert not tree.css("div.grid__card"), "no GoN CMS card grid on this host"


def test_article_pages_carry_no_prose(outlet, story_html):
    """Documents a limitation, not a success. The notice text is in attachments
    we do not fetch, so the listing is the payload. If this starts failing with
    a real body, the portal began publishing full text -- good news."""
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert "body" not in selectors["article"]
    assert article.body == ""

