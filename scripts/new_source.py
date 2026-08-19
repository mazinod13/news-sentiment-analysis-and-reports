#!/usr/bin/env python
"""Scaffold a new outlet: config + selector pack + test + saved fixture.

    python scripts/new_source.py --id gorkhapatra --name "Gorkhapatra" \
        --url https://gorkhapatraonline.com/rss --lang ne --priority 2

Creates only files named after the outlet, so two people scaffolding two
outlets never touch the same file and their branches merge cleanly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "config" / "sources"
SELECTORS = ROOT / "config" / "selectors"
TESTS = ROOT / "tests" / "sources"
FIXTURES = ROOT / "tests" / "fixtures"

SOURCE_TEMPLATE = """\
id: {id}
name: {name}
url: {url}
homepage: {homepage}
method: {method}
lang: {lang}
category: {category}
priority: {priority}
active: true

selectors: {id}

# Record every quirk you hit while wiring this up. The next person will hit
# them too, and these notes are why the selector pack looks the way it does.
notes: |
  - TODO
"""

SELECTOR_TEMPLATE = """\
# Selector pack for {name} article pages.
# Syntax: a CSS selector, optionally suffixed with @attribute.
#   "p.date span"                        -> text of the first match
#   "meta[property='og:image']@content"  -> that attribute
# Verify against tests/fixtures/{id}_story.html before committing.

article:
  body: "TODO"                # container holding the article paragraphs
  author: "TODO"
  published: "TODO"
  # bs = Bikram Sambat in Devanagari, iso = machine-readable, auto = try both
  published_format: auto
  image: "meta[property='og:image']@content"

exclude:
  - "script"
  - "style"
  - "aside"
  # - ads, related-story rails, poll widgets, share bars...
"""

TEST_TEMPLATE = '''\
"""Per-outlet test for {name}. This file is yours alone -- no one else edits it.

Everything runs off saved fixtures; no network.
"""

from __future__ import annotations

import feedparser
import pytest

from app.parsing.article import extract

SOURCE_ID = "{id}"


@pytest.fixture
def outlet(load_outlet):
    return load_outlet(SOURCE_ID)


def test_config_is_valid(outlet):
    source, _ = outlet
    assert source.id == SOURCE_ID
    assert source.method == "{method}"
    assert source.lang == "{lang}"


def test_feed_parses(fixture_text):
    feed = feedparser.parse(fixture_text(f"{{SOURCE_ID}}_feed.xml"))
    assert feed.entries, "feed produced no items"
    assert feed.entries[0].title
    assert feed.entries[0].link.startswith("http")


@pytest.mark.skip(reason="TODO: save {id}_story.html and fill in the selector pack")
def test_article_extraction(outlet, fixture_text):
    _, selectors = outlet
    article = extract(fixture_text(f"{{SOURCE_ID}}_story.html"), selectors, source_id=SOURCE_ID)

    assert len(article.body) > 300
    assert article.published is not None
'''


def write(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        print(f"  exists, skipped  {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  wrote            {path.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--id", required=True, help="kebab-case slug, e.g. gorkhapatra")
    parser.add_argument("--name", required=True)
    parser.add_argument("--url", required=True, help="feed or listing URL")
    parser.add_argument("--method", default="rss", choices=["rss", "html"])
    parser.add_argument("--lang", default="ne", choices=["ne", "en"])
    parser.add_argument("--category", default="news")
    parser.add_argument("--priority", type=int, default=2, choices=[1, 2, 3, 4])
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    parser.add_argument("--no-fetch", action="store_true", help="skip downloading the fixture")
    args = parser.parse_args()

    if args.id != args.id.lower() or " " in args.id:
        print("--id must be lower-case with no spaces", file=sys.stderr)
        return 1

    fields = {
        "id": args.id,
        "name": args.name,
        "url": args.url,
        "homepage": args.url.split("/rss")[0].rstrip("/"),
        "method": args.method,
        "lang": args.lang,
        "category": args.category,
        "priority": args.priority,
    }

    print(f"Scaffolding {args.name} ({args.id}):")
    write(SOURCES / f"{args.id}.yaml", SOURCE_TEMPLATE.format(**fields), args.force)
    write(SELECTORS / f"{args.id}.yaml", SELECTOR_TEMPLATE.format(**fields), args.force)
    test_name = f"test_{args.id.replace('-', '_')}.py"
    write(TESTS / test_name, TEST_TEMPLATE.format(**fields), args.force)

    if not args.no_fetch:
        suffix = "feed.xml" if args.method == "rss" else "listing.html"
        try:
            response = httpx.get(
                args.url,
                follow_redirects=True,
                timeout=30,
                headers={"User-Agent": "NepalNewsSentiment/0.1 (+scaffold)"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"  fixture fetch failed: {exc}", file=sys.stderr)
        else:
            if str(response.url) != args.url:
                print(f"  NOTE: redirected to {response.url} -- put that URL in the config")
            write(FIXTURES / f"{args.id}_{suffix}", response.text, args.force)

    print(
        "\nNext:\n"
        f"  1. python -m app.main probe {args.id}\n"
        f"  2. fill in config/selectors/{args.id}.yaml from a real article page\n"
        f"  3. save tests/fixtures/{args.id}_story.html and un-skip the extraction test\n"
        f"  4. pytest tests/sources/test_{args.id.replace('-', '_')}.py\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
