"""Article-page extraction driven by a selector pack.

Selector syntax is plain CSS with an optional attribute suffix:
    "div.news__details p"                -> text of every match, joined
    "meta[property='og:image']@content"  -> that attribute on the first match

A selector may instead read schema.org JSON-LD, using a dotted path:
    "jsonld:datePublished"               -> from the <script> ld+json block
    "jsonld:author.name"

JSON-LD is worth reaching for when a site publishes richer data there than in
its DOM. eKantipur, for instance, renders only a date-only Bikram Sambat string
on the page but ships a full timestamp in JSON-LD.

Keeping this in YAML rather than Python means a site redesign is a config
change, not a code change and not a deploy.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from selectolax.parser import HTMLParser

from app.parsing.clean import clean
from app.parsing.dates import DateParseError, parse_datetime

log = logging.getLogger(__name__)

JSONLD_PREFIX = "jsonld:"

# schema.org @type values that describe the article itself, best first.
_ARTICLE_TYPES = ("NewsArticle", "Article", "BlogPosting", "ReportageNewsArticle")


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


def load_jsonld(tree: HTMLParser) -> list[dict]:
    """Every JSON-LD object on the page, flattened out of @graph wrappers.

    A malformed block is skipped rather than fatal -- sites ship broken JSON-LD
    surprisingly often, and one bad block must not cost us the article.
    """
    objects: list[dict] = []
    for node in tree.css('script[type="application/ld+json"]'):
        raw = node.text()
        if not raw or not raw.strip():
            continue
        try:
            objects.extend(_flatten_jsonld(json.loads(raw)))
        except (json.JSONDecodeError, ValueError):
            continue
    return objects


def _flatten_jsonld(data) -> list[dict]:
    if isinstance(data, list):
        return [obj for item in data for obj in _flatten_jsonld(item)]
    if isinstance(data, dict):
        out = [data]
        if "@graph" in data:
            out.extend(_flatten_jsonld(data["@graph"]))
        return out
    return []


def _type_names(obj: dict) -> list[str]:
    raw = obj.get("@type") or []
    return [raw] if isinstance(raw, str) else [t for t in raw if isinstance(t, str)]


def jsonld_value(objects: list[dict], path: str) -> str | None:
    """Resolve a dotted path such as `author.name` across the page's JSON-LD.

    Article-ish objects are consulted first so that, on a page carrying both a
    NewsArticle and an Organization, `author.name` does not return the
    publisher's name.
    """
    ranked = sorted(
        objects,
        key=lambda obj: min(
            (_ARTICLE_TYPES.index(t) for t in _type_names(obj) if t in _ARTICLE_TYPES),
            default=len(_ARTICLE_TYPES),
        ),
    )
    for obj in ranked:
        value = _resolve_path(obj, path)
        if value is not None:
            return clean(str(value)) or None
    return None


def _resolve_path(obj, path: str):
    current = obj
    for part in path.split("."):
        if isinstance(current, list):
            current = current[0] if current else None
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    if isinstance(current, list):
        current = current[0] if current else None
    return current if isinstance(current, (str, int, float)) else None


def _uses_jsonld(article: dict) -> bool:
    return any(
        isinstance(v, str) and v.startswith(JSONLD_PREFIX) for v in article.values()
    )


def extract(html: str, selectors: dict, *, source_id: str = "") -> ExtractedArticle:
    """Apply a selector pack to an article page.

    Missing fields yield None rather than raising: one absent author must not
    cost us the article. A missing body is reported by the caller.
    """
    tree = HTMLParser(html)
    article = selectors.get("article", {})

    # Read JSON-LD BEFORE exclusions: nearly every pack excludes "script", which
    # would decompose the ld+json block and silently empty any jsonld: field.
    jsonld = load_jsonld(tree) if _uses_jsonld(article) else []

    for unwanted in selectors.get("exclude", []):
        for node in tree.css(unwanted):
            node.decompose()

    def value(selector: str) -> str | None:
        if selector.startswith(JSONLD_PREFIX):
            return jsonld_value(jsonld, selector[len(JSONLD_PREFIX):])
        return select_one(tree, selector)

    result = ExtractedArticle()

    if body_selector := article.get("body"):
        if body_selector.startswith(JSONLD_PREFIX):
            result.body = value(body_selector) or ""
        else:
            result.body = "\n\n".join(select_all(tree, body_selector))

    if author_selector := article.get("author"):
        result.author = value(author_selector)

    if image_selector := article.get("image"):
        result.image = value(image_selector)

    if published_selector := article.get("published"):
        raw = value(published_selector)
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
