"""Per-outlet test for eKantipur Gandaki. This file is yours alone -- no one else edits it.

Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "ekantipur-gandaki"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


def test_config_is_valid(outlet):
    source, _ = outlet
    assert source.id == SOURCE_ID
    assert source.method == "html"
    assert source.lang == "ne"


def test_feed_parses(fixture_text):
    feed = feedparser.parse(fixture_text(f"{SOURCE_ID}_feed.xml"))
    assert feed.entries, "feed produced no items"
    assert feed.entries[0].title
    assert feed.entries[0].link.startswith("http")


@pytest.mark.skip(reason="TODO: save ekantipur-gandaki_story.html and fill in the selector pack")
def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{SOURCE_ID}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 300
    assert article.published is not None
