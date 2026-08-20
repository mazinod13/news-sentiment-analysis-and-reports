#!/usr/bin/env python
"""Regenerate the news-outlet tables in DATA_SOURCES.md from config/sources/.

Because outlets live one-per-file, this is what gives you the single combined
view a monolithic sources.yaml would have -- without the merge conflicts.

    python scripts/gen_sources.py            # rewrite DATA_SOURCES.md
    python scripts/gen_sources.py --check    # fail if it is stale (for CI)

Only the two generated blocks are touched; everything else in the file is left
exactly as written. The blocks are delimited by HTML comments, so if they are
missing the script tells you where to put them instead of guessing.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.settings import ROOT  # noqa: E402
from app.sources import load_sources  # noqa: E402

DOC = ROOT / "DATA_SOURCES.md"

ACTIVE_BEGIN = "<!-- BEGIN GENERATED: news-outlets -->"
ACTIVE_END = "<!-- END GENERATED: news-outlets -->"
INACTIVE_BEGIN = "<!-- BEGIN GENERATED: inactive-outlets -->"
INACTIVE_END = "<!-- END GENERATED: inactive-outlets -->"


def active_table(sources) -> str:
    rows = sorted(
        (s for s in sources if s.active),
        key=lambda s: (s.priority, s.name.lower()),
    )
    lines = [
        f"_{len(rows)} outlet(s) wired up. Priority drives poll frequency "
        f"(1 = every ~15 min). Generated from `config/sources/` — do not edit by hand._",
        "",
        "| # | Source | Link | Lang | Cat | Prio | Method |",
        "|---|--------|------|:----:|:---:|:----:|:------:|",
    ]
    for index, source in enumerate(rows, start=1):
        lines.append(
            f"| {index} | {source.name} | <{source.url}> | {source.lang} | "
            f"{source.category} | {source.priority} | {source.method} |"
        )
    return "\n".join(lines)


def inactive_table(sources) -> str:
    rows = sorted((s for s in sources if not s.active), key=lambda s: s.name.lower())
    if not rows:
        return "_No outlets are currently disabled._"
    lines = [
        "Configured but disabled (upstream RSS removed or site down):",
        "",
        "| Source | Link | Reason |",
        "|--------|------|--------|",
    ]
    for source in rows:
        lines.append(f"| {source.name} | <{source.url}> | {source.inactive_reason} |")
    return "\n".join(lines)


def replace_block(text: str, begin: str, end: str, body: str) -> str:
    start = text.find(begin)
    stop = text.find(end)
    if start == -1 or stop == -1:
        raise SystemExit(
            f"DATA_SOURCES.md is missing the {begin!r} / {end!r} markers.\n"
            "Add them around the block this script should own, then re-run."
        )
    return f"{text[:start]}{begin}\n\n{body}\n\n{text[stop:]}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the doc is stale")
    args = parser.parse_args()

    sources = list(load_sources(ROOT / "config" / "sources").values())
    original = DOC.read_text(encoding="utf-8")

    updated = replace_block(original, ACTIVE_BEGIN, ACTIVE_END, active_table(sources))
    updated = replace_block(updated, INACTIVE_BEGIN, INACTIVE_END, inactive_table(sources))

    if args.check:
        if updated != original:
            print("DATA_SOURCES.md is stale -- run: python scripts/gen_sources.py",
                  file=sys.stderr)
            return 1
        print("DATA_SOURCES.md is up to date")
        return 0

    if updated == original:
        print("DATA_SOURCES.md already up to date")
        return 0

    DOC.write_text(updated, encoding="utf-8")
    active = sum(1 for s in sources if s.active)
    print(f"DATA_SOURCES.md updated: {active} active, {len(sources) - active} inactive")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
