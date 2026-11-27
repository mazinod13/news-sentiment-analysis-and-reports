"""Every CSS selector in every pack must compile under Lexbor.

Modest silently accepted malformed selectors such as `.td_module_*`; Lexbor
raises SelectolaxError on them, which fails the whole source at fetch time.
Per-outlet extraction tests are skipped until a story fixture is saved, so
this is the only check that covers every pack.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

from app.parsing.article import JSONLD_PREFIX, _split_selector
from app.settings import ROOT

PACKS = sorted((ROOT / "config" / "selectors").glob("*.yaml"))


def pack_selectors(path: Path) -> list[tuple[str, str]]:
    pack = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    found = [("exclude", css) for css in pack.get("exclude") or []]
    for section in ("listing", "article"):
        for key, value in (pack.get(section) or {}).items():
            if not isinstance(value, str) or key.endswith("_format"):
                continue
            if value.startswith(JSONLD_PREFIX):
                continue
            found.append((f"{section}.{key}", _split_selector(value)[0]))
    return found


def test_packs_found():
    assert PACKS, "no selector packs found"


@pytest.mark.parametrize("path", PACKS, ids=lambda p: p.stem)
def test_selectors_compile(path):
    tree = LexborHTMLParser("<html><body></body></html>")
    invalid = []
    for where, css in pack_selectors(path):
        try:
            tree.css(css)
        except SelectolaxError as exc:
            invalid.append(f"{where}: {css!r} ({exc})")
    assert not invalid, "invalid CSS selector(s):\n  " + "\n  ".join(invalid)
