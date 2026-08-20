"""Shared tests for JSON-LD extraction (`jsonld:` selectors).

Many news sites publish richer data in a schema.org block than in their DOM.
eKantipur is the first user: its page shows a date-only Bikram Sambat string
but ships a full timestamp in JSON-LD.
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from app.parsing.article import extract, jsonld_value, load_jsonld


def page(*blocks: str, body: str = "<p>text</p>") -> str:
    scripts = "".join(
        f'<script type="application/ld+json">{b}</script>' for b in blocks
    )
    return f"<html><head>{scripts}</head><body>{body}</body></html>"


class TestLoading:
    def test_reads_a_newsarticle_block(self):
        objects = load_jsonld(HTMLParser(page('{"@type":"NewsArticle","headline":"x"}')))
        assert objects and objects[0]["headline"] == "x"

    def test_flattens_a_graph_wrapper(self):
        html = page('{"@graph":[{"@type":"NewsArticle","headline":"inside graph"}]}')
        assert jsonld_value(load_jsonld(HTMLParser(html)), "headline") == "inside graph"

    def test_flattens_a_top_level_array(self):
        html = page(
            '[{"@type":"Organization","name":"pub"},'
            '{"@type":"NewsArticle","headline":"h"}]'
        )
        assert jsonld_value(load_jsonld(HTMLParser(html)), "headline") == "h"

    def test_malformed_block_is_skipped_not_fatal(self):
        """Broken JSON-LD is common. One bad block must not cost us the article."""
        html = page("{not valid json,,,}", '{"@type":"NewsArticle","headline":"survived"}')
        assert jsonld_value(load_jsonld(HTMLParser(html)), "headline") == "survived"

    def test_no_blocks_yields_nothing(self):
        assert load_jsonld(HTMLParser("<html><body>hi</body></html>")) == []


class TestPathResolution:
    def test_dotted_path(self):
        html = page('{"@type":"NewsArticle","author":{"@type":"Person","name":"भवानी भट्ट"}}')
        assert jsonld_value(load_jsonld(HTMLParser(html)), "author.name") == "भवानी भट्ट"

    def test_list_valued_field_takes_the_first(self):
        html = page('{"@type":"NewsArticle","image":["a.jpg","b.jpg"]}')
        assert jsonld_value(load_jsonld(HTMLParser(html)), "image") == "a.jpg"

    def test_missing_path_is_none(self):
        html = page('{"@type":"NewsArticle","headline":"x"}')
        assert jsonld_value(load_jsonld(HTMLParser(html)), "author.name") is None

    def test_article_types_outrank_the_publisher(self):
        """A page carrying both must not return the Organization's name for
        `author.name` just because it appeared first in the document."""
        html = page(
            '{"@type":"Organization","name":"Kantipur","author":{"name":"WRONG"}}',
            '{"@type":"NewsArticle","author":{"name":"RIGHT"}}',
        )
        assert jsonld_value(load_jsonld(HTMLParser(html)), "author.name") == "RIGHT"

    def test_entities_are_unescaped(self):
        html = page('{"@type":"NewsArticle","headline":"a&nbsp;b"}')
        assert jsonld_value(load_jsonld(HTMLParser(html)), "headline") == "a b"


class TestExtractIntegration:
    SELECTORS = {
        "article": {
            "body": "div.c p",
            "author": "jsonld:author.name",
            "published": "jsonld:datePublished",
            "published_format": "iso",
            "image": "jsonld:image",
        },
        "exclude": ["script"],
    }

    def _html(self) -> str:
        return page(
            '{"@type":"NewsArticle","author":{"name":"लेखक"},'
            '"datePublished":"2026-08-19 20:09:16","image":["https://x.test/i.jpg"]}',
            body='<div class="c"><p>मुख्य पाठ</p></div>',
        )

    def test_fields_come_from_jsonld(self):
        article = extract(self._html(), self.SELECTORS)

        assert article.author == "लेखक"
        assert article.image == "https://x.test/i.jpg"
        assert article.body == "मुख्य पाठ"

    def test_naive_timestamp_gets_nepal_time(self):
        """JSON-LD dates are routinely naive. Nepal sites mean local time."""
        article = extract(self._html(), self.SELECTORS)

        assert article.published.isoformat() == "2026-08-19T20:09:16+05:45"

    def test_survives_script_being_excluded(self):
        """The ordering trap: `exclude: script` decomposes the ld+json block, so
        JSON-LD must be read before exclusions are applied."""
        assert "script" in self.SELECTORS["exclude"]

        article = extract(self._html(), self.SELECTORS)

        assert article.author == "लेखक"
        assert article.published is not None

    def test_css_selectors_still_work_alongside(self):
        selectors = {"article": {"body": "div.c p", "author": "span.by"}}
        html = page(
            '{"@type":"NewsArticle","author":{"name":"IGNORED"}}',
            body='<div class="c"><p>पाठ</p></div><span class="by">DOM लेखक</span>',
        )

        assert extract(html, selectors).author == "DOM लेखक"

    def test_missing_jsonld_leaves_fields_empty_not_crashing(self):
        html = '<html><body><div class="c"><p>पाठ</p></div></body></html>'

        article = extract(html, self.SELECTORS)

        assert article.body == "पाठ"
        assert article.author is None
        assert article.published is None
