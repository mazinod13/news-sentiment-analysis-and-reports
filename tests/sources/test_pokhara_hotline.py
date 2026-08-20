"""Per-outlet test for Pokhara Hotline. This file is yours alone -- no one else edits it.

Everything runs off saved fixtures; no network.

Pokhara Hotline is the well-behaved counterpart to Annapurna Post: WordPress
with a complete metadata block, so author/date/image all come from <meta> and
no Bikram Sambat parsing is needed. What it does have is a feed publishing
+0000 while its article pages publish +05:45 -- the two must agree.
"""

from __future__ import annotations

import feedparser
import pytest
from selectolax.parser import HTMLParser

from app.ingestion.base import RawItem
from app.parsing.article import extract
from app.parsing.dates import parse_feed_datetime
from app.pipeline.normalize import canonical_url, normalize
from app.settings import ROOT

SOURCE_ID = "pokhara-hotline"

# The article the story fixture was captured from.
STORY_URL = "https://pokharahotline.com/post-16683/"
STORY_PUBLISHED = "2026-08-17T19:17:57+05:45"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


# -- config -------------------------------------------------------------------


def test_config_is_valid(outlet):
    source, selectors = outlet
    assert source.id == SOURCE_ID
    assert source.method == "rss"
    assert source.lang == "ne"
    # This site gives real ISO timestamps, so BS parsing must NOT be requested.
    assert selectors["article"]["published_format"] == "iso"


# -- fixtures themselves ------------------------------------------------------


def test_story_fixture_is_utf8_and_not_mojibake():
    """Guards the capture step, not the parser.

    This fixture was originally saved through a PowerShell `>` redirect, which
    wrote it as UTF-16 with the Devanagari mangled through cp437. Re-capture
    with `curl.exe -sL <url> -o <file>` so curl writes the bytes itself.
    """
    path = ROOT / "tests" / "fixtures" / f"{SOURCE_ID}_story.html"
    raw = path.read_bytes()

    assert not raw.startswith(b"\xff\xfe"), "fixture is UTF-16 -- re-capture it as UTF-8"
    raw.decode("utf-8")  # raises if it is not valid UTF-8

    text = raw.decode("utf-8")
    assert "पोखरा" in text, "no readable Devanagari -- the capture mangled the encoding"
    assert "αñ" not in text and "à¤" not in text, "mojibake markers present"


# -- feed ---------------------------------------------------------------------


def test_feed_parses(fixture_text):
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    assert len(feed.entries) == 10
    assert feed.entries[0].title
    assert feed.entries[0].link.startswith("http")


def test_feed_publishes_utc_but_lands_in_nepal_time(fixture_text):
    """The feed states +0000. Stored dates must be converted to +05:45, not
    relabelled -- relabelling shifts every article 5h45m early."""
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    entry = feed.entries[0]

    assert entry.published.endswith("+0000"), "feed no longer publishes UTC; re-check the parser"

    parsed = parse_feed_datetime(entry)
    assert parsed.isoformat() == STORY_PUBLISHED
    assert parsed.utcoffset().total_seconds() == 5 * 3600 + 45 * 60


def test_feed_date_agrees_with_article_page(outlet, fixture_text):
    """Cross-check: the same story dated two different ways by the same site
    must resolve to one instant. This is the strongest signal that timezone
    handling is right end to end."""
    _, selectors = outlet
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    from_feed = parse_feed_datetime(feed.entries[0])

    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert from_feed == article.published


def test_trailing_slash_is_canonicalised():
    assert canonical_url(STORY_URL) == "https://pokharahotline.com/post-16683"


# -- article page -------------------------------------------------------------


def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    # meta[name=author] is the site name, not a journalist's byline.
    assert article.author == "पोखरा हटलाइन"
    assert article.image.endswith("/photo-4.jpg")

    assert article.body.startswith("पोखरा, पोखरा महानगरपालिकाका")
    assert article.body.endswith("वृक्षारोपण गरिएको बताए।")
    assert 2000 < len(article.body) < 4000


def test_published_comes_from_meta_as_real_iso(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert article.published is not None
    assert article.published.isoformat() == STORY_PUBLISHED


def test_page_has_three_prose_containers(fixture_text):
    """The article, a footer note and a callout all use .prose; only prose-lg
    is the body."""
    tree = HTMLParser(fixture_text(f"{SOURCE_ID}_story.html"))

    assert len(tree.css("div.prose")) == 3
    assert len(tree.css("div.prose.prose-lg")) == 1


def test_body_selector_ignores_the_other_prose_containers(outlet):
    """The two non-article .prose containers are empty in today's fixture, so
    the fixture alone cannot prove the selector is specific enough. This feeds
    the real selector pack a page shaped like the live one but with all three
    containers populated -- a bare `div.prose p` selector swallows all three.
    """
    _, selectors = outlet
    page = """
    <html><body>
      <div class="prose prose-lg max-w-none">
        <p>असली समाचारको पाठ यहाँ छ।</p>
      </div>
      <div class="prose prose-sm max-w-none border-t">
        <p>FOOTER_NOTE_SHOULD_NOT_APPEAR</p>
      </div>
      <div class="prose prose-red max-w-none">
        <p>CALLOUT_SHOULD_NOT_APPEAR</p>
      </div>
    </body></html>
    """

    article = extract(page, selectors, source_id=SOURCE_ID)

    assert "असली समाचारको पाठ" in article.body
    assert "FOOTER_NOTE_SHOULD_NOT_APPEAR" not in article.body
    assert "CALLOUT_SHOULD_NOT_APPEAR" not in article.body


def test_body_is_one_paragraph_with_br_separators(fixture_text):
    """Structural quirk: the whole article is a single <p> using <br /> for
    paragraph breaks, so those breaks are lost in the stored text. If this
    starts failing the theme changed to real <p> tags and the body selector
    should be revisited."""
    tree = HTMLParser(fixture_text(f"{SOURCE_ID}_story.html"))

    assert len(tree.css("div.prose.prose-lg p")) == 1
    assert len(tree.css("div.prose.prose-lg br")) > 1


def test_related_rails_do_not_leak_into_body(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    for heading in ("सम्बन्धित", "ट्रेन्डिङ", "पनि पढ्नुहोस्"):
        assert heading not in article.body


# -- normalisation ------------------------------------------------------------


def test_normalize_keeps_a_real_published_date(outlet, fixture_text):
    """Unlike Annapurna Post, nothing here should fall back to fetch time."""
    source, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    item = RawItem(
        url=STORY_URL,
        title="बङ्गलादेशका फोटो पत्रकारहरुकाे भेटघाट पोखरामा",
        published=article.published,
        body=article.body,
    )
    normalised = normalize(item, source)

    assert normalised.published_estimated is False
    assert normalised.published_at.isoformat() == STORY_PUBLISHED
    assert normalised.lang == "ne"
    assert normalised.url == "https://pokharahotline.com/post-16683"
