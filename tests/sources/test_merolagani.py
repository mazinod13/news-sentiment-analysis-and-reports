"""Per-outlet test for MeroLagani.

This file is yours alone -- no one else edits it. Everything runs off saved
fixtures; no network.

A `method: html` market-news site on ASP.NET WebForms -- .aspx URLs and
query-string article ids. One of the few Nepali sources whose dates carry a
real clock rather than landing at midnight.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "merolagani"
LISTING_URL = "https://merolagani.com/NewsList.aspx?id=7&type=latest"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 8
FIRST_DATE_RAW = 'Sep 10, 2026 09:21 AM'
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


def test_link_takes_the_title_anchor_not_the_thumbnail(outlet, listing_html):
    """Every card opens with a thumbnail wrapped in its own <a> to the same
    URL. Pointing `link` at the card's first anchor works today and makes the
    pack depend on a thumbnail that not every card carries."""
    _, selectors = outlet

    assert selectors["listing"]["link"] == "h4.media-title a@href"
    card = HTMLParser(listing_html).css("div.media-news")[0]
    assert card.css_first("div.media-wrap a"), "fixture card has no thumbnail anchor"


def test_urls_are_query_strings_and_stay_distinguishable(outlet, listing_html):
    """ASP.NET WebForms: the article id lives in a QUERY STRING
    (/NewsDetail.aspx?newsID=130642), not a path. If newsID were ever added to
    the tracking-parameter strip list in canonical_url, every article on this
    outlet would collapse to one URL.
    """
    items = scrape(outlet, listing_html)

    assert all("newsID=" in i.url for i in items)
    assert len({i.url for i in items}) == len(items)


def test_dates_carry_a_real_clock(outlet, listing_html):
    """One of the few Nepali sources whose timestamps are not midnight --
    "Sep 10, 2026 09:21 AM". Worth protecting: a format change that drops the
    time would silently flatten every article to 00:00."""
    items = scrape(outlet, listing_html)

    assert any((i.published.hour, i.published.minute) != (0, 0) for i in items)


def test_article_body_is_real_prose(outlet, story_html):
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) > 500

