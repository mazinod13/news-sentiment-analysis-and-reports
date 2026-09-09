"""Per-outlet test for Ratopati Bagmati. This file is yours alone -- no one else edits it.

Everything runs off saved fixtures; no network.

A `method: html` outlet. The thing to know about Ratopati: every province
subdomain wraps the same national feed in a provincial skin. Measured live
across koshi/bagmati/madhesh, 36 of 45 front-page stories were byte-identical
on all three. Only the two प्रदेश rails carry province news, which is why the
`item` selector is scoped to those sections and must stay that way.
"""

from __future__ import annotations

import re

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.settings import ROOT

SOURCE_ID = "ratopati-bagmati"
LISTING_URL = "https://bagmati.ratopati.com"
LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"

STORY_HREF = re.compile(r"/story/\d+")

# The two rails that carry province news, and the two that are the national
# feed. Verified identical across all three province subdomains.
PROVINCE_RAILS = "section.pradesh-samachar, section.pradesh-headline"
NATIONAL_RAILS = "section.section-world-news, section.section-special-news"

# What the pack must NOT be widened to. Kept here so the trap test can show
# exactly what widening costs.
WIDE_ITEM = "div.columnnews, div.thumbnail-news, div.overlay-news"


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


def story_hrefs(tree: HTMLParser, scope: str) -> set[str]:
    """Every /story/<id> href inside the given sections."""
    return {
        a.attributes["href"]
        for a in tree.css(", ".join(f"{s} a[href]" for s in scope.split(", ")))
        if STORY_HREF.search(a.attributes.get("href", ""))
    }


def scrape(outlet, html):
    source, selectors = outlet
    return HTMLScraper(source, StubFetcher(html, LISTING_URL), selectors).fetch()


# -- config -------------------------------------------------------------------


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.id == SOURCE_ID
    # Ratopati publishes no usable feed for a province; the portal front page
    # is the item source.
    assert source.method == "html"
    assert source.lang == "ne"
    assert "listing" in selectors, "an html source needs a listing block"
    # The DOM date is a Bikram Sambat string. See the pack for why jsonld: is
    # deliberately not used on this outlet.
    assert selectors["article"]["published_format"] == "bs"


def test_fixture_is_utf8():
    raw = (ROOT / "tests" / "fixtures" / LISTING_FIXTURE).read_bytes()
    assert not raw.startswith(b"\xff\xfe"), "UTF-16 -- re-capture with curl.exe -sL <url> -o <file>"
    raw.decode("utf-8")


# -- listing page -------------------------------------------------------------


def test_listing_yields_unique_absolute_story_urls(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert 5 <= len(items) <= 30, f"{len(items)} items -- the province rails hold roughly a dozen"
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.title for i in items)
    # Cards link to www.ratopati.com, NOT to the province subdomain. A `link`
    # selector missing @href returns the anchor TEXT, and urljoin then builds
    # "https://bagmati.ratopati.com/<headline>" -- a URL made out of a headline,
    # which 404s on every fetch. That was the original bug in this pack.
    assert all(STORY_HREF.search(i.url) for i in items)
    assert all(i.url.startswith("https://") and "ratopati.com/story/" in i.url for i in items)
    assert not any("bagmati.ratopati.com" in i.url for i in items)


def test_listing_takes_only_the_province_rails(outlet, listing_html):
    """The outlet's signature trap, and the reason `item` is scoped.

    Every Ratopati province subdomain republishes the same national feed. If
    `item` reaches beyond the प्रदेश rails, this source starts ingesting stories
    that its sibling provinces also ingest -- and because articles.url_hash is
    globally UNIQUE with on_conflict_do_nothing, the story is stored once and
    attributed to whichever province ingested first. Province attribution then
    depends on scheduler order rather than on what Ratopati published.
    """
    tree = HTMLParser(listing_html)
    province = story_hrefs(tree, PROVINCE_RAILS)
    national = story_hrefs(tree, NATIONAL_RAILS)
    assert province and national, "fixture is missing the rails this test is about"

    scraped = {i.url for i in scrape(outlet, listing_html)}

    assert scraped <= province, f"leaked outside the province rails: {scraped - province}"
    assert not scraped & national, f"picked up national stories: {scraped & national}"


def test_widening_the_item_selector_pulls_in_the_national_feed(outlet, listing_html):
    """Shows what the scoping buys, so nobody has to take it on faith.

    Swapping in the unscoped card selector roughly triples the item count and
    drags in the national rails wholesale.
    """
    source, selectors = outlet
    wide = {**selectors, "listing": {**selectors["listing"], "item": WIDE_ITEM}}

    scoped = {i.url for i in scrape(outlet, listing_html)}
    widened_items = HTMLScraper(source, StubFetcher(listing_html, LISTING_URL), wide).fetch()
    widened = {i.url for i in widened_items}
    national = story_hrefs(HTMLParser(listing_html), NATIONAL_RAILS)

    assert len(widened) > len(scoped) * 2
    assert widened & national, "expected the wide selector to reach the national rails"
    assert not scoped & national


def test_title_matches_the_class_not_the_tag(listing_html):
    """`h2.news-title` looks reasonable and silently drops most of the page:
    this CMS uses h3 for nearly every card and h2 for a handful."""
    tree = HTMLParser(listing_html)

    h3 = len(tree.css("h3.news-title"))
    h2 = len(tree.css("h2.news-title"))

    assert h3 > h2, "if this flips, revisit the title selector"
    assert len(tree.css(".news-title")) == h2 + h3


@pytest.mark.skip(reason="TODO: save ratopati-bagmati_story.html, then pin body/author/published")
def test_article_extraction(outlet, fixture_text):
    """Unpinned for now: there is no story fixture for this outlet yet.

    The article selectors were verified by hand against four live Ratopati
    story pages, but nothing stops a redesign from breaking them silently
    until this test has a fixture to run against.
    """
