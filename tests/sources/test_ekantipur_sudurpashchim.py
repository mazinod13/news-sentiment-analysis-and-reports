"""Per-outlet test for eKantipur Sudurpashchim. This file is yours alone.

Everything runs off saved fixtures; no network.

The first `method: html` outlet. eKantipur publishes no RSS anywhere, so the
province index page is the item source. Two things make it awkward:

  * article bodies are rendered client-side, so the server HTML carries only a
    headline, a one-sentence lede and a byline -- `body` here is the LEDE ONLY;
  * the listing summary needs a child combinator, or it silently captures the
    journalist's name instead of the story summary.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.base import RawItem
from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import parse_datetime
from app.pipeline.normalize import normalize
from app.settings import ROOT

SOURCE_ID = "ekantipur-sudurpashchim"

LISTING_URL = "https://ekantipur.com/pradesh-7"
STORY_URL = (
    "https://ekantipur.com/sudurpaschim-pradesh/2026/08/19/"
    "four-wheeler-traffic-banned-on-vanbasa-bridge-after-mahakali-river-flow-"
    "continues-to-decline-09-18.html"
)
STORY_PUBLISHED = "2026-08-19T20:09:16+05:45"   # from JSON-LD, which carries a time
STORY_BS_DATE = "भाद्र ३, २०८३"                  # the DOM equivalent, date only


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
    # eKantipur has no feed anywhere: /rss, /feed and /pradesh-7/rss all return
    # HTTP 200 with HTML. Configured as rss this dies with
    # "unparseable feed: not well-formed (invalid token)".
    assert source.method == "html"
    assert source.lang == "ne"
    assert "listing" in selectors, "an html source needs a listing block"
    assert selectors["article"]["published_format"] == "iso"


def test_fixtures_are_utf8():
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
    first, so every stored summary becomes a journalist's name.
    """
    tree = HTMLParser(fixture_text(f"{SOURCE_ID}_listing.html"))
    card = tree.css("div.category")[0]

    descendant = select_one(card, "div.category-description p")
    child = select_one(card, "div.category-description > p")

    assert descendant == "भवानी भट्ट"          # the trap: a person's name
    assert child.startswith("जिल्ला प्रहरी")   # the real summary
    assert descendant != child


def test_configured_summary_selector_avoids_the_trap(outlet, fixture_text):
    source, selectors = outlet
    fetcher = StubFetcher(fixture_text(f"{SOURCE_ID}_listing.html"), LISTING_URL)

    items = HTMLScraper(source, fetcher, selectors).fetch()

    assert items[0].summary.startswith("जिल्ला प्रहरी")
    assert items[0].summary != "भवानी भट्ट"


def test_listing_cards_carry_no_date(fixture_text):
    """Cards show only a "1 MIN READ" badge, which is why the article page has
    to be fetched even though it yields no full body."""
    tree = HTMLParser(fixture_text(f"{SOURCE_ID}_listing.html"))
    card = tree.css("div.category")[0]

    assert select_one(card, "div.time-wrapper span") == "1 MIN READ"


# -- article page -------------------------------------------------------------


def test_article_gives_author_and_timestamp_from_jsonld(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert article.author == "भवानी भट्ट"
    assert article.published is not None
    assert article.published.isoformat() == STORY_PUBLISHED
    assert article.published.utcoffset().total_seconds() == 5 * 3600 + 45 * 60
    # A real time, not midnight -- that is the whole reason for preferring
    # JSON-LD over the date-only Bikram Sambat string in the DOM.
    assert (article.published.hour, article.published.minute) == (20, 9)


def test_jsonld_timestamp_agrees_with_the_dom_bikram_sambat_date(outlet, fixture_text):
    """Two independent sources of the same fact, asserted equal.

    This is also what pins the timezone: the JSON-LD timestamp is naive, and
    read as UTC it would fall on the following day and contradict भाद्र ३.
    """
    _, selectors = outlet
    html = fixture_text(f"{SOURCE_ID}_story.html")

    from_jsonld = extract(html, selectors, source_id=SOURCE_ID).published

    dom_raw = select_one(HTMLParser(html), "div.author-date span")
    assert dom_raw == STORY_BS_DATE
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
    """Regression: `exclude` runs BEFORE any CSS field is read, so listing
    `div.author-date` there silently emptied both author and published while
    the body still looked fine. Still worth pinning -- the DOM byline is the
    documented fallback if the JSON-LD block ever disappears."""
    _, selectors = outlet

    assert "div.author-date" not in selectors.get("exclude", [])


def test_body_is_the_lede_only(outlet, fixture_text):
    """Documents a limitation, not a success.

    eKantipur renders article bodies client-side, so the server HTML holds
    ~200 chars total. If this starts failing with a much longer body, the site
    began server-rendering and the selector pack should be revisited.
    """
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert 50 < len(article.body) < 400
    assert article.body.startswith("जिल्ला प्रहरी कार्यालयका अनुसार")
    # The byline must not be swept into the body by the child combinator.
    assert "भवानी भट्ट" not in article.body


def test_full_article_text_is_absent_from_server_html(fixture_text):
    """Pins the reason body is short: it is the site, not our selector.

    The longest Devanagari run in the page is the site's own boilerplate
    description, not article prose.
    """
    import re

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
        title="महाकालीको बहाव नघटेपछि वनबासा पुलमा चारपांग्रे गाडी चलाउन रोक",
        published=article.published,
        author=article.author,
        body=article.body,
    )
    normalised = normalize(item, source)

    assert normalised.published_estimated is False
    assert normalised.published_at.isoformat() == STORY_PUBLISHED
    assert normalised.author == "भवानी भट्ट"
    assert normalised.lang == "ne"
