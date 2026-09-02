"""Per-outlet test for the House of Representatives. This file is yours alone.

Everything runs off saved fixtures; no network.

A `method: html` government source, not a news outlet: institutional press
releases, no bylines. Three things make it awkward:

  * the publish date is on the LISTING, not the article -- story pages have no
    date element at all;
  * that date must come from the <em>, because its <li> carries a label that
    parse_datetime rejects;
  * body markup is inconsistent -- some stories use <p>, some a bare styled
    <div> -- so a paragraph selector silently returns an empty body.

The host also serves an incomplete certificate chain; that is covered by
tests/test_fetcher_tls.py, since the fix is shared code.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import DateParseError, parse_datetime
from app.settings import ROOT

SOURCE_ID = "house-of-representatives"
LISTING_URL = "https://hr.parliament.gov.np/np/news"

LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
# The story whose body sits in a styled <div> with no <p> -- the layout that
# returned an empty body before the selector was widened to the container.
STORY_DIV_FIXTURE = f"{SOURCE_ID}_story.html"
# A story using the ordinary <p> layout, so both are covered.
STORY_P_FIXTURE = f"{SOURCE_ID}_story_paragraph.html"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


@pytest.fixture
def listing_html(fixture_text):
    return fixture_text(LISTING_FIXTURE)


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
    assert source.lang == "ne"
    assert "listing" in selectors, "an html source needs a listing block"


def test_date_is_configured_on_the_listing_not_the_article(outlet):
    """Story pages carry no date element, so the listing is the only source.
    If someone moves `published` into the `article` block, every item falls
    back to fetch time and silently reports today's date."""
    _, selectors = outlet

    assert selectors["listing"]["published"], "the listing must carry the date"
    assert selectors["listing"]["published_format"] == "iso"
    assert "published" not in selectors["article"]


def test_fixtures_are_utf8():
    for name in (LISTING_FIXTURE, STORY_DIV_FIXTURE, STORY_P_FIXTURE):
        raw = (ROOT / "tests" / "fixtures" / name).read_bytes()
        assert not raw.startswith(b"\xff\xfe"), f"{name} is UTF-16 -- re-capture with curl -o"
        raw.decode("utf-8")


# -- listing page -------------------------------------------------------------


def test_listing_yields_unique_absolute_urls_with_dates(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert len(items) == 30
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.url.startswith("https://hr.parliament.gov.np/np/news/") for i in items)
    assert all(i.title for i in items)
    # The date comes from the listing, so every item must arrive with one.
    assert all(i.published for i in items), "an item reached normalisation with no date"


def test_card_selector_does_not_double_count_the_grid(outlet, listing_html):
    """`div.row div.col-sm-6` looks like the card but matches 60 nodes for 30
    stories, because the bootstrap grid classes nest.

    URL dedupe in HTMLScraper hides the damage -- the item count comes out at
    30 either way -- so this has to check the CONFIGURED selector directly, or
    the mistake is invisible to every other test in this file.
    """
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    matched = tree.css(selectors["listing"]["item"])
    assert len(matched) == 30, (
        f"item selector matched {len(matched)} nodes for 30 stories -- "
        "the bootstrap grid classes nest, use div.grid-news"
    )
    # The shape of the trap, for the next person reading this.
    assert len(tree.css("div.row div.col-sm-6")) == 60


def test_date_needs_the_em_not_the_li(listing_html):
    """The outlet's signature trap.

    The <li> reads "प्रकाशित मिति: 2026-08-11" -- label and all. Only the <em>
    holds a bare date, and parse_datetime rejects the labelled form outright,
    so pointing at the <li> loses the date on every single item.
    """
    card = HTMLParser(listing_html).css("div.grid-news")[0]

    li = select_one(card, "li")
    em = select_one(card, "li em")

    assert li.startswith("प्रकाशित मिति:")
    assert em == "2026-08-11"
    assert parse_datetime(em, fmt="iso").date().isoformat() == "2026-08-11"
    with pytest.raises(DateParseError):
        parse_datetime(li, fmt="iso")


def test_listing_dates_are_npt_midnight(outlet, listing_html):
    items = scrape(outlet, listing_html)

    for item in items[:5]:
        assert item.published.utcoffset().total_seconds() == 5 * 3600 + 45 * 60
        # Date-only source: there is no clock time anywhere on this site.
        assert (item.published.hour, item.published.minute) == (0, 0)


def test_listing_date_agrees_with_the_bikram_sambat_dateline(outlet, fixture_text):
    """Two independent sources of one fact, asserted equal.

    The article body opens with a BS dateline; the listing gives ISO. They must
    land on the same day -- which is also what proves the ISO date is Nepal
    time rather than UTC.
    """
    _, selectors = outlet
    article = extract(fixture_text(STORY_P_FIXTURE), selectors, source_id=SOURCE_ID)

    assert article.body.startswith("२६ असार २०८३")
    from_dateline = parse_datetime("२६ असार २०८३", fmt="bs")

    items = scrape(outlet, fixture_text(LISTING_FIXTURE))
    matching = [i for i in items if i.url.endswith("/1783677264")]
    assert matching, "the fixture story is not in the saved listing"

    assert matching[0].published.date() == from_dateline.date()


# -- article page -------------------------------------------------------------


def test_body_survives_both_markup_layouts(outlet, fixture_text):
    """The site is inconsistent: most stories use <p>, some use a bare
    <div style="text-align: justify;"> with no <p> at all. A `p`-based selector
    returns an EMPTY body for the second kind -- no error, just nothing."""
    _, selectors = outlet

    div_layout = extract(fixture_text(STORY_DIV_FIXTURE), selectors, source_id=SOURCE_ID)
    p_layout = extract(fixture_text(STORY_P_FIXTURE), selectors, source_id=SOURCE_ID)

    assert len(div_layout.body) > 300, "the styled-div layout came back empty"
    assert len(p_layout.body) > 300
    assert div_layout.body.startswith("आज मिति २०८२ चैत २२ गते")


def test_the_div_layout_really_has_no_paragraph(fixture_text):
    """Pins the reason the body selector takes the container: it is the site's
    markup, not our selector. If this starts failing the site became
    consistent and the pack could be tightened."""
    tree = HTMLParser(fixture_text(STORY_DIV_FIXTURE))
    # The tag rail is itself a non-empty <p>, so it has to go first or it looks
    # like the article had a paragraph all along -- which is exactly why the
    # pack excludes it.
    for junk in tree.css("div.tags-share"):
        junk.decompose()
    container = tree.css_first("div.single-item")

    assert container is not None
    assert not [p for p in container.css("p") if p.text().strip()]
    assert [d for d in container.css("div[style]") if d.text().strip()]


def test_tag_rail_is_excluded_from_the_body(outlet, fixture_text):
    """div.tags-share sits inside div.single-item, so without the exclusion
    every body ends with a stray "ट्यागहरु:"."""
    _, selectors = outlet

    assert "div.tags-share" in selectors["exclude"]
    article = extract(fixture_text(STORY_P_FIXTURE), selectors, source_id=SOURCE_ID)

    assert "ट्यागहरु" not in article.body


def test_no_author_is_configured(outlet):
    """Institutional press releases carry no byline. Asserting the absence
    stops someone wiring `author` to a nav element that happens to hold text."""
    _, selectors = outlet

    assert "author" not in selectors["article"]
