"""Per-outlet test for Lumbini Province OCMCM.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` government portal. The listing is the notices TABLE on
/notices -- /notice 404s, and the homepage card class spans six unrelated
sections, so both of the obvious guesses are wrong.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "ocmcm-lumbini"
LISTING_URL = "https://ocmcm.lumbini.gov.np/notices"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 12
FIRST_DATE_RAW = 'भदौ ११, २०८३'
FIRST_DATE_ISO = "2026-08-27"


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
    assert sum(1 for i in items if i.published) >= 12


def test_date_parses_in_this_hosts_format(outlet, listing_html):
    _, selectors = outlet
    card = HTMLParser(listing_html).css(selectors["listing"]["item"])[0]

    raw = select_one(card, selectors["listing"]["published"])

    assert raw == FIRST_DATE_RAW
    fmt = selectors["listing"]["published_format"]
    assert parse_datetime(raw, fmt=fmt).date().isoformat() == FIRST_DATE_ISO


# -- the outlet's own traps ---------------------------------------------------


def test_listing_is_the_notices_table(outlet, listing_html):
    """The outlet's signature trap: three things here look like the listing.

      * /notice (singular) returns HTTP 404 -- the obvious first guess.
      * div.content__card on the HOMEPAGE does show notice cards, but that
        class spans SIX sections (notice, press-releases, bids, policies,
        publications, downloads). Measured on the homepage: 45 cards, of which
        only 4 were notices. It hands you the document library and calls it
        notices.
      * div.slider__item, the Government-of-Nepal CMS site-wide widget, is
        present here too and carries the same 38 links on every page.

    The real list is the table on /notices.
    """
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    assert selectors["listing"]["item"] == "tbody.table-hover-css tr"
    assert tree.css("div.slider__item"), "expected the shared CMS widget to be present"
    assert not tree.css("div.grid__card"), "this host has no GoN CMS card grid"

    items = scrape(outlet, listing_html)
    assert all("/notice/" in i.url for i in items), "leaked outside the notices section"


def test_link_avoids_the_attached_pdf(outlet, listing_html):
    """The third cell of every row links to the attached PDF. A looser anchor
    selector than th a@href eventually stores a .pdf URL as the article."""
    _, selectors = outlet
    row = HTMLParser(listing_html).css("tbody.table-hover-css tr")[0]

    assert selectors["listing"]["link"] == "th a@href"
    assert [a for a in row.css("a[href]") if ".pdf" in (a.attributes.get("href") or "")], (
        "fixture row has no PDF link -- this test is not testing anything"
    )
    assert not any(".pdf" in i.url for i in scrape(outlet, listing_html))


def test_article_pages_carry_no_prose(outlet, story_html):
    """Documents a limitation, not a success -- the notice text is in the
    attached PDF, which we do not fetch."""
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert "body" not in selectors["article"]
    assert article.body == ""

