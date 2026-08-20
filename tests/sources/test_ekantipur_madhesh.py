"""Per-outlet test for eKantipur Madhesh. This file is yours alone -- no one else edits it.

Everything runs off saved fixtures; no network.

A `method: html` outlet. eKantipur publishes no RSS anywhere, so the province
index page is the item source. Three things make it awkward:

  * article bodies are rendered client-side, so the server HTML carries only a
    headline, a one-sentence lede and a byline -- `body` here is the LEDE ONLY;
  * the listing summary needs a child combinator, or it silently captures the
    journalist's name instead of the story summary;
  * the DOM Bikram Sambat date runs month and day together ("श्रावण२९, २०८३"),
    so the real timestamp comes from the page's schema.org block instead.
"""

from __future__ import annotations

import re

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.base import RawItem
from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.pipeline.normalize import normalize
from app.settings import ROOT

SOURCE_ID = "ekantipur-madhesh"

LISTING_URL = "https://ekantipur.com/pradesh-2"
STORY_URL = (
    "https://ekantipur.com/madhesh-pradesh/2026/08/14/"
    "high-court-issues-short-term-interim-order-to-stop-road-expansion-in-"
    "birgunj-41-33.html"
)
STORY_TITLE = "वीरगन्जमा सडक विस्तार रोक्न उच्च अदालतको अल्पकालीन अन्तरिम आदेश"
STORY_AUTHOR = "शंकर आचार्य"
STORY_PUBLISHED = "2026-08-14T17:41:30+05:45"   # from JSON-LD, which carries a time
STORY_BS_DATE = "श्रावण२९, २०८३"                 # the DOM equivalent, date only


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


class StubFetcher:
    """Returns a saved page instead of making a request, so the real
    HTMLScraper code path runs offline."""

    def __init__(self, text: str, url: str) -> None:
        self._text, self._url = text, url
        self.requested: list[str] = []

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        self.requested.append(url)
        return FetchResult(url=self._url, status=200, text=self._text, content=b"")


# -- config -------------------------------------------------------------------


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.id == SOURCE_ID
    # eKantipur has no feed anywhere: /rss, /feed and /pradesh-2/rss all return
    # HTTP 200 with HTML. Configured as rss this dies with
    # "unparseable feed: not well-formed (invalid token)".
    assert source.method == "html"
    assert source.lang == "ne"
    assert "listing" in selectors, "an html source needs a listing block"
    assert selectors["article"]["published_format"] == "iso"


def test_fixtures_are_utf8():
    """Regression: the story fixture arrived UTF-16LE.

    PowerShell's `>` redirect writes UTF-16 and the loader reads UTF-8, so the
    fixture failed to decode at all. Capture with `curl.exe -sL <url> -o <file>`.
    """
    for name in (f"{SOURCE_ID}_listing.html", f"{SOURCE_ID}_story.html"):
        raw = (ROOT / "tests" / "fixtures" / name).read_bytes()
        assert not raw.startswith(b"\xff\xfe"), f"{name} is UTF-16 -- re-capture with curl -o"
        raw.decode("utf-8")


# -- listing page -------------------------------------------------------------


def test_listing_yields_unique_absolute_links(outlet, fixture_text):
    source, selectors = outlet
    fetcher = StubFetcher(fixture_text(f"{SOURCE_ID}_listing.html"), LISTING_URL)
    scraper = HTMLScraper(source, fetcher, selectors)

    items = scraper.fetch()

    assert len(items) == 30
    assert all(i.url.startswith("https://ekantipur.com/") for i in items)
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.title for i in items)


def test_listing_summary_needs_a_child_combinator(fixture_text):
    """The outlet's signature trap.

    Inside a card the byline sits in `div.author-name > p` and the summary in
    `div.category-description > p`. A descendant selector matches the byline
    first, so every stored summary becomes a journalist's name -- silently, with
    no error and no empty field. Checked on two cards so a single coincidence
    cannot make this pass.
    """
    tree = HTMLParser(fixture_text(f"{SOURCE_ID}_listing.html"))
    cards = tree.css("div.category")

    for index, byline in ((0, "शंकर आचार्य"), (1, "शिव पुरी")):
        card = cards[index]
        descendant = select_one(card, "div.category-description p")
        child = select_one(card, "div.category-description > p")

        assert descendant == byline          # the trap: a person's name
        assert child != descendant
        assert len(child) > 30               # a real summary, not a byline


def test_configured_summary_selector_avoids_the_trap(outlet, fixture_text):
    source, selectors = outlet
    fetcher = StubFetcher(fixture_text(f"{SOURCE_ID}_listing.html"), LISTING_URL)

    items = HTMLScraper(source, fetcher, selectors).fetch()

    assert items[0].summary.startswith("वीरगन्ज भन्सार कार्यालयले")
    assert items[0].summary != STORY_AUTHOR


def test_listing_cards_carry_no_date(fixture_text):
    """Cards show only a read-time badge, which is why the article page has to
    be fetched even though it yields no full body."""
    tree = HTMLParser(fixture_text(f"{SOURCE_ID}_listing.html"))
    card = tree.css("div.category")[0]

    badge = select_one(card, "div.time-wrapper span")
    assert re.fullmatch(r"\d+ MINS? READ", badge), badge


# -- article page -------------------------------------------------------------


def test_article_gives_author_and_timestamp_from_jsonld(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert article.author == STORY_AUTHOR
    assert article.published is not None
    assert article.published.isoformat() == STORY_PUBLISHED
    assert article.published.utcoffset().total_seconds() == 5 * 3600 + 45 * 60
    # A real time, not midnight -- that is the whole reason for preferring
    # JSON-LD over the date-only Bikram Sambat string in the DOM.
    assert (article.published.hour, article.published.minute) == (17, 41)


def test_jsonld_timestamp_agrees_with_the_dom_bikram_sambat_date(outlet, fixture_text):
    """Two independent sources of the same fact, asserted equal.

    This is also what pins the timezone: the JSON-LD timestamp is naive, and
    read as UTC it would fall on the following day and contradict श्रावण २९.

    Note the DOM string has no space between month and day. Other pradesh
    sections write "भाद्र ३, २०८३"; this one writes "श्रावण२९, २०८३". The
    parser copes with both -- do not "correct" the fixture.
    """
    _, selectors = outlet
    html = fixture_text(f"{SOURCE_ID}_story.html")

    from_jsonld = extract(html, selectors, source_id=SOURCE_ID).published

    dom_raw = select_one(HTMLParser(html), "div.author-date span")
    assert dom_raw == STORY_BS_DATE
    assert " " not in dom_raw.split(",")[0], "the no-space month/day form is the point here"
    from_dom = parse_datetime(dom_raw, fmt="bs")

    assert from_jsonld.date() == from_dom.date()


def test_jsonld_survives_the_script_exclusion(outlet, fixture_text):
    """`exclude` lists "script", which decomposes the ld+json block. Extraction
    must read JSON-LD before exclusions run, or author and published silently
    come back empty."""
    _, selectors = outlet

    assert "script" in selectors["exclude"]
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert article.author is not None
    assert article.published is not None


def test_exclude_does_not_delete_the_byline_block(outlet):
    """`exclude` runs BEFORE any CSS field is read, so listing `div.author-date`
    there silently empties both author and published while the body still looks
    fine. The DOM byline is the documented fallback if the JSON-LD block ever
    disappears, so it must survive."""
    _, selectors = outlet

    assert "div.author-date" not in selectors.get("exclude", [])


def test_body_is_the_lede_only(outlet, fixture_text):
    """Documents a limitation, not a success.

    eKantipur renders article bodies client-side, so the server HTML holds
    ~140 chars total. If this starts failing with a much longer body, the site
    began server-rendering and the selector pack should be revisited.
    """
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert 50 < len(article.body) < 400
    assert article.body.startswith("नेपाल टेलिकमको तर्फबाट दायर रिटमाथि")
    # The byline must not be swept into the body by the child combinator.
    assert STORY_AUTHOR not in article.body


def test_full_article_text_is_absent_from_server_html(fixture_text):
    """Pins the reason body is short: it is the site, not our selector.

    The longest Devanagari run in the page is the site's own boilerplate
    description, not article prose.
    """
    html = fixture_text(f"{SOURCE_ID}_story.html")
    runs = re.findall(r"[ऀ-ॿ\s,।!?\-–—:;()'\"]{80,}", html)
    article_prose = [r for r in runs if "कान्तिपुर राष्ट्रिय दैनिकको" not in r]

    assert all(len(r) < 300 for r in article_prose), (
        "a long prose run appeared -- eKantipur may now server-render bodies"
    )


# -- normalisation ------------------------------------------------------------


def test_normalize_keeps_the_real_date(outlet, fixture_text):
    source, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    item = RawItem(
        url=STORY_URL,
        title=STORY_TITLE,
        published=article.published,
        author=article.author,
        body=article.body,
    )
    normalised = normalize(item, source)

    assert normalised.published_estimated is False
    assert normalised.published_at.isoformat() == STORY_PUBLISHED
    assert normalised.author == STORY_AUTHOR
    assert normalised.lang == "ne"
