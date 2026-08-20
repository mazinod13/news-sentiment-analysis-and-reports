"""Article-page extraction driven by a selector pack.

Selector syntax is plain CSS with an optional attribute suffix:
    "div.news__details p"                -> text of every match, joined
    "meta[property='og:image']@content"  -> that attribute on the first match

Keeping this in YAML rather than Python means a site redesign is a config
change, not a code change and not a deploy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from selectolax.parser import HTMLParser

from app.parsing.clean import clean
from app.parsing.dates import DateParseError, parse_datetime

log = logging.getLogger(__name__)


@dataclass
class ExtractedArticle:
    body: str = ""
    author: str | None = None
    published: datetime | None = None
    image: str | None = None


def _split_selector(selector: str) -> tuple[str, str | None]:
    if "@" in selector:
        css, _, attr = selector.rpartition("@")
        return css.strip(), attr.strip()
    return selector.strip(), None


def select_one(tree: HTMLParser, selector: str) -> str | None:
    css, attr = _split_selector(selector)
    node = tree.css_first(css)
    if node is None:
        return None
    value = node.attributes.get(attr) if attr else node.text()
    return clean(value) or None


def select_all(tree: HTMLParser, selector: str) -> list[str]:
    css, attr = _split_selector(selector)
    out = []
    for node in tree.css(css):
        value = node.attributes.get(attr) if attr else node.text()
        text = clean(value)
        if text:
            out.append(text)
    return out


def extract(html: str, selectors: dict, *, source_id: str = "") -> ExtractedArticle:
    """Apply a selector pack to an article page.

    Missing fields yield None rather than raising: one absent author must not
    cost us the article. A missing body is reported by the caller.
    """
    tree = HTMLParser(html)
    article = selectors.get("article", {})

    for unwanted in selectors.get("exclude", []):
        for node in tree.css(unwanted):
            node.decompose()

    result = ExtractedArticle()

    if body_selector := article.get("body"):
        result.body = "\n\n".join(select_all(tree, body_selector))

    if author_selector := article.get("author"):
        result.author = select_one(tree, author_selector)

    if image_selector := article.get("image"):
        result.image = select_one(tree, image_selector)

    if published_selector := article.get("published"):
        raw = select_one(tree, published_selector)
        if raw:
            fmt = article.get("published_format", "auto")
            try:
                result.published = parse_datetime(raw, fmt=fmt)
            except DateParseError:
                log.warning(
                    "unparseable publish date %r (format=%s)", raw, fmt,
                    extra={"source_id": source_id},
                )

    return result
