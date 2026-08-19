"""CLI entrypoint.

    python -m app.main sources                    list + validate every outlet
    python -m app.main probe annapurna-post       fetch, parse, print, store nothing
    python -m app.main ingest --source annapurna-post
    python -m app.main ingest --priority 1
    python -m app.main worker
    python -m app.main db upgrade
"""

from __future__ import annotations

import argparse
import sys

from app.settings import load_settings
from app.sources import SourceConfigError, load_sources
from app.utils.logging import setup_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.main", description="Nepal news scraper")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sources", help="list and validate configured outlets")

    probe = sub.add_parser("probe", help="fetch and parse one source without storing")
    probe.add_argument("source")
    probe.add_argument("--limit", type=int, default=3, help="items to fetch bodies for")
    probe.add_argument("--full", action="store_true", help="print full article bodies")

    ingest = sub.add_parser("ingest", help="run sources and store the results")
    group = ingest.add_mutually_exclusive_group()
    group.add_argument("--source", help="one source id")
    group.add_argument("--priority", type=int, choices=[1, 2, 3, 4])
    group.add_argument("--due", action="store_true", help="only sources past next_run_at")

    sub.add_parser("worker", help="run the scheduler continuously")

    db = sub.add_parser("db", help="database management")
    db.add_argument("action", choices=["upgrade"])

    return parser


def cmd_sources(settings) -> int:
    try:
        sources = load_sources(settings.sources_dir)
    except SourceConfigError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    if not sources:
        print(f"No sources found in {settings.sources_dir}")
        return 1
    print(f"{'id':<24} {'method':<6} {'lang':<4} {'prio':<4} {'every':<7} name")
    for source in sources.values():
        flag = "" if source.active else "  (inactive)"
        print(
            f"{source.id:<24} {source.method:<6} {source.lang:<4} {source.priority:<4} "
            f"{str(source.poll_interval_minutes) + 'm':<7} {source.name}{flag}"
        )
    active = sum(1 for s in sources.values() if s.active)
    print(f"\n{len(sources)} configured, {active} active")
    return 0


def cmd_probe(settings, args) -> int:
    from app.ingestion.fetcher import Fetcher
    from app.pipeline.run import run_source

    sources = load_sources(settings.sources_dir)
    source = sources.get(args.source)
    if source is None:
        print(f"unknown source {args.source!r}; try: python -m app.main sources", file=sys.stderr)
        return 1

    with Fetcher(settings) as fetcher:
        report = run_source(source, settings, fetcher, dry_run=True, limit=args.limit)

    if not report.ok:
        print(f"FAILED: {report.error}", file=sys.stderr)
        return 1

    print(f"\n{source.name} ({source.id}) -- {report.seen} item(s) in the feed\n")
    for article in report.articles:
        estimated = "  [ESTIMATED]" if article.published_estimated else ""
        print(f"  title      {article.title}")
        print(f"  url        {article.url}")
        print(f"  published  {article.published_at.isoformat()}{estimated}")
        print(f"  author     {article.author or '-'}")
        print(f"  lang       {article.lang}")
        print(f"  body       {len(article.body)} chars")
        preview = article.body or article.summary
        if args.full:
            print(f"\n{preview}\n")
        elif preview:
            print(f"  preview    {preview[:160].replace(chr(10), ' ')}...")
        print()

    estimated_count = sum(1 for a in report.articles if a.published_estimated)
    if estimated_count:
        print(f"note: {estimated_count}/{len(report.articles)} items have no real publish "
              f"date and fell back to fetch time")
    return 0


def cmd_ingest(settings, args) -> int:
    from app.scheduler.worker import due_sources, run_once

    if args.source:
        sources_map = load_sources(settings.sources_dir)
        if args.source not in sources_map:
            print(f"unknown source {args.source!r}", file=sys.stderr)
            return 1
        selected = [sources_map[args.source]]
    elif args.due:
        from datetime import datetime

        from app.settings import NPT
        selected = due_sources(settings, datetime.now(NPT))
    else:
        active = load_sources(settings.sources_dir, active_only=True).values()
        selected = [s for s in active if args.priority is None or s.priority == args.priority]

    reports = run_once(settings, selected)
    for report in reports:
        print(report)
    return 0 if all(r.ok for r in reports) else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = load_settings()
    setup_logging(settings.log_level)

    if args.command == "sources":
        return cmd_sources(settings)
    if args.command == "probe":
        return cmd_probe(settings, args)
    if args.command == "ingest":
        return cmd_ingest(settings, args)
    if args.command == "worker":
        from app.scheduler.worker import run_forever

        run_forever(settings)
        return 0
    if args.command == "db":
        from app.storage.db import create_all

        create_all(settings)
        print("schema up to date")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
