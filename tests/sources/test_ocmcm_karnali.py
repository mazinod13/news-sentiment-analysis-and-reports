"""Per-outlet test for Karnali Province OCMCM.

This file is yours alone -- no one else edits it.

Everything runs off saved fixtures; no network.

A `method: html` government portal on the standard Government-of-Nepal CMS,
shared by seven hosts. Three things make it awkward:

  * two widgets masquerade as content -- a site-wide carousel on the listing
    and div.detail__block, the shared relief-fund banner, on the article page;
  * the publish date is on the LISTING, not the article;
  * the date format differs by host, so `auto` is required.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.settings import ROOT

SOURCE_ID = "ocmcm-karnali"
LISTING_URL = "https://ocmcm.karnali.gov.np/category/cabinet-decisions/"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

EXPECTED_ITEMS = 4
FIRST_TITLE_HEAD = 'कर्णाली प्रदेश निजामति सेवा (पहिलो'
FIRST_DATE_RAW = '१७ भदौ, २०८३'
FIRST_DATE_ISO = "2026-09-02"

# A site-wide widget that looks like a category listing. See the trap test
# below for what selecting it would actually cost.
SLIDER_ITEM = "div.slider__item"


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


def test_date_is_configured_on_the_listing_not_the_article(outlet):
    """Article pages do not carry a usable date on this CMS: the same selector
    returns a related item's date on three hosts and nothing on the other four.
    Moving `published` into the `article` block makes every item fall back to
    fetch time and silently report today."""
    _, selectors = outlet

    assert selectors["listing"]["published"]
    assert selectors["listing"]["published_format"] == "auto"
    assert "published" not in selectors["article"]


def test_fixtures_are_utf8_and_not_empty():
    for name in (LISTING_FIXTURE, STORY_FIXTURE):
        raw = (ROOT / "tests" / "fixtures" / name).read_bytes()
        assert raw, f"{name} is empty -- re-capture with curl.exe -sL <url> -o <file>"
        assert not raw.startswith(b"\xff\xfe"), f"{name} is UTF-16 -- re-capture with curl -o"
        raw.decode("utf-8")


# -- listing page -------------------------------------------------------------


def test_listing_yields_dated_items(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert len(items) == EXPECTED_ITEMS
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.title for i in items)
    assert all("/content/" in i.url for i in items)
    # The href is wrapped in newlines and indentation in the source HTML;
    # clean() has to strip it or every URL is unusable.
    assert all(i.url == i.url.strip() and "\n" not in i.url for i in items)
    # The date is the point of this outlet -- the body often is not.
    assert all(i.published for i in items), "an item reached normalisation with no date"
    assert items[0].title.startswith(FIRST_TITLE_HEAD)


def test_listing_takes_the_category_list_not_the_site_wide_widget(outlet, listing_html):
    """The outlet's signature trap.

    div.slider__item looks like a listing and is not one. It is a SITE-WIDE
    widget: measured on the saved fixtures it carries between 5 and 196
    /content/ links that this category's card list does not have. Selecting it
    would make the configured category URL meaningless -- every category source
    on the host would ingest the whole site, and each other's items.

    Its first slide is also the same national relief-fund announcement on six
    of the seven hosts. Each portal republishes that under its OWN /content/
    id, so url_hash dedupe does NOT collapse them -- they arrive as seven near
    duplicates for the simhash stage. Measured cross-host overlap of /content/
    paths is zero for both widgets: what repeats across this CMS family is
    text, not URLs.
    """
    _, selectors = outlet
    tree = HTMLParser(listing_html)
    assert selectors["listing"]["item"] == "div.grid__card"

    def content_paths(nodes):
        out = set()
        for node in nodes:
            for a in node.css("a[href]"):
                href = (a.attributes.get("href") or "").strip()
                if "/content/" in href:
                    out.add(href[href.index("/content/"):])
        return out

    widget = content_paths(tree.css(SLIDER_ITEM))
    cards = content_paths(tree.css("div.grid__card"))
    assert widget, "fixture has no widget -- this test is not testing anything"

    # The load-bearing fact: the widget reaches beyond this category.
    assert widget - cards, (
        "the site-wide widget carries nothing the category list lacks -- "
        "re-check whether it really is site-wide on this host"
    )
    scraped = {i.url for i in scrape(outlet, listing_html)}
    assert len(scraped) <= len(cards), "scraped more than the category's own cards"


def test_date_parses_in_this_hosts_format(listing_html):
    """`published_format: auto` is required, not laziness: this family mixes
    Devanagari Bikram Sambat with English Gregorian depending on the host."""
    card = HTMLParser(listing_html).css("div.grid__card")[0]

    raw = select_one(card, "div.post__date p")

    assert raw == FIRST_DATE_RAW
    assert parse_datetime(raw, fmt="auto").date().isoformat() == FIRST_DATE_ISO


def test_link_takes_the_title_anchor_not_the_thumbnail(outlet, listing_html):
    """Each card wraps its thumbnail in its own <a> to the same URL. Pointing
    `link` at the card's first anchor makes the pack depend on a thumbnail that
    not every card has."""
    _, selectors = outlet

    assert selectors["listing"]["link"].startswith("h3.card__title")


# -- article page -------------------------------------------------------------


def test_detail_block_is_the_shared_banner_not_the_body(outlet, story_html):
    """The second trap, and the nastier one.

    `div.detail__block` matches exactly once per article page, which is exactly
    what a body container looks like. It is the same national relief-fund
    banner as the carousel. Selecting it yields plausible Devanagari prose that
    is identical on every host and has nothing to do with the article.
    """
    _, selectors = outlet
    tree = HTMLParser(story_html)

    block = tree.css("div.detail__block")
    assert len(block) == 1, "if this changes, re-check which container is the body"
    assert selectors["article"]["body"] != "div.detail__block"
    assert selectors["article"]["body"] == "div.detail__page-desc"


def test_body_matches_this_hosts_documented_shape(outlet, story_html):
    """Documents reality, not a success.

    There is NO article body on this host. The selector cleans down to
    the literal 'A\n\nA' -- four characters, two placeholder glyphs. The
    notice text sits in attachments we do not fetch, so the listing title
    and date are the entire payload. If this starts failing with a real
    body, the portal began publishing full text -- good news, and the
    pack is worth revisiting.
    """
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) < 20


def test_image_is_configured_from_open_graph(outlet, story_html):
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert selectors["article"]["image"].startswith("meta[property='og:image']")
    assert article.image is None or article.image.startswith("http")


def test_no_author_is_configured(outlet):
    """Institutional notices carry no byline. Asserting the absence stops
    someone wiring `author` to a nav element that happens to hold text."""
    _, selectors = outlet

    assert "author" not in selectors["article"]
