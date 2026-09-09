"""Per-outlet test for the National Assembly. This file is yours alone.

Everything runs off saved fixtures; no network.

The upper house, on the same CMS as house-of-representatives -- the HoR
selector pack was applied here unchanged and returned 30 cards, ISO dates and a
6193-character body on the first attempt. The two packs should stay in step.

The traps are the same ones, and they are pinned here independently so a change
made on one outlet cannot silently break the other:

  * the publish date is on the LISTING, not the article;
  * it must come from the <em>, because its <li> carries a label that
    parse_datetime rejects;
  * `div.row div.col-sm-6` matches 60 nodes for 30 stories.

The host also serves an incomplete certificate chain (the same wildcard
*.parliament.gov.np), covered by tests/test_fetcher_tls.py.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from app.ingestion.fetcher import FetchResult
from app.ingestion.html import HTMLScraper
from app.parsing.article import extract, select_one
from app.parsing.dates import DateParseError, parse_datetime
from app.settings import ROOT

SOURCE_ID = "national-assembly"
LISTING_URL = "https://na.parliament.gov.np/np/news"

LISTING_FIXTURE = f"{SOURCE_ID}_listing.html"
STORY_FIXTURE = f"{SOURCE_ID}_story.html"

FIRST_DATE = "2026-09-07"


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


def test_pack_still_matches_house_of_representatives(sources_dir, selectors_dir):
    """These two outlets run the same CMS and the same selectors. Keeping the
    packs identical is the point -- if one is tuned and the other is not, the
    next site change breaks only one of them and nobody notices which."""
    import yaml

    mine = yaml.safe_load((selectors_dir / f"{SOURCE_ID}.yaml").read_text(encoding="utf-8"))
    theirs = yaml.safe_load(
        (selectors_dir / "house-of-representatives.yaml").read_text(encoding="utf-8")
    )

    assert mine == theirs, (
        "national-assembly and house-of-representatives packs have diverged -- "
        "if that is deliberate, delete this test and say why in the notes"
    )


def test_date_is_configured_on_the_listing_not_the_article(outlet):
    """Story pages carry no date element, so the listing is the only source."""
    _, selectors = outlet

    assert selectors["listing"]["published"]
    assert selectors["listing"]["published_format"] == "iso"
    assert "published" not in selectors["article"]


def test_fixtures_are_utf8_and_not_empty():
    for name in (LISTING_FIXTURE, STORY_FIXTURE):
        raw = (ROOT / "tests" / "fixtures" / name).read_bytes()
        assert raw, f"{name} is empty -- re-capture with curl.exe -sL <url> -o <file>"
        assert not raw.startswith(b"\xff\xfe"), f"{name} is UTF-16 -- re-capture with curl -o"
        raw.decode("utf-8")


# -- listing page -------------------------------------------------------------


def test_listing_yields_unique_dated_urls(outlet, listing_html):
    items = scrape(outlet, listing_html)

    assert len(items) == 30
    assert len({i.url for i in items}) == len(items), "listing produced duplicate URLs"
    assert all(i.url.startswith("https://na.parliament.gov.np/np/news/") for i in items)
    assert all(i.title for i in items)
    assert all(i.published for i in items), "an item reached normalisation with no date"


def test_card_selector_does_not_double_count_the_grid(outlet, listing_html):
    """`div.row div.col-sm-6` looks like the card but matches 60 nodes for 30
    stories, because the bootstrap grid classes nest.

    URL dedupe in HTMLScraper hides the damage -- the item count comes out at
    30 either way -- so this has to check the CONFIGURED selector directly.
    """
    _, selectors = outlet
    tree = HTMLParser(listing_html)

    matched = tree.css(selectors["listing"]["item"])
    assert len(matched) == 30, (
        f"item selector matched {len(matched)} nodes for 30 stories -- "
        "the bootstrap grid classes nest, use div.grid-news"
    )
    assert len(tree.css("div.row div.col-sm-6")) == 60


def test_date_needs_the_em_not_the_li(listing_html):
    """The outlet's signature trap.

    The <li> reads "प्रकाशित मिति: 2026-09-07" -- label and all. Only the <em>
    holds a bare date, and parse_datetime rejects the labelled form, so
    pointing at the <li> loses the date on every single item.
    """
    card = HTMLParser(listing_html).css("div.grid-news")[0]

    li = select_one(card, "li")
    em = select_one(card, "li em")

    assert li.startswith("प्रकाशित मिति:")
    assert em == FIRST_DATE
    assert parse_datetime(em, fmt="iso").date().isoformat() == FIRST_DATE
    with pytest.raises(DateParseError):
        parse_datetime(li, fmt="iso")


def test_listing_dates_are_npt_midnight(outlet, listing_html):
    items = scrape(outlet, listing_html)

    for item in items[:5]:
        assert item.published.utcoffset().total_seconds() == 5 * 3600 + 45 * 60
        assert (item.published.hour, item.published.minute) == (0, 0)


# -- article page -------------------------------------------------------------


def test_article_body_is_real_prose(outlet, story_html):
    """Unlike the Government-of-Nepal CMS portals, the parliament template
    serves full text -- 6193 characters in this fixture."""
    _, selectors = outlet
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert len(article.body) > 3000
    assert article.body.startswith("कम्बोडिया अधिराज्यको सिनेटका अध्यक्ष")


def test_tag_rail_is_excluded_from_the_body(outlet, story_html):
    """div.tags-share sits inside div.single-item, so without the exclusion
    every body ends with a stray "ट्यागहरु:"."""
    _, selectors = outlet

    assert "div.tags-share" in selectors["exclude"]
    article = extract(story_html, selectors, source_id=SOURCE_ID)

    assert "ट्यागहरु" not in article.body


def test_no_author_is_configured(outlet):
    """Institutional press releases carry no byline."""
    _, selectors = outlet

    assert "author" not in selectors["article"]
