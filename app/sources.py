"""Loads and validates config/sources/*.yaml -- one file per outlet.

The per-file layout is deliberate: two people adding two outlets touch two
different files, so their branches merge without conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

METHODS = {"rss", "html"}
LANGS = {"ne", "en"}
CATEGORIES = {"news", "economic", "govt", "disaster", "social", "sports", "entertainment"}
PRIORITIES = {1, 2, 3, 4}

# priority -> how often the worker polls it
POLL_INTERVAL_MINUTES = {1: 15, 2: 30, 3: 60, 4: 360}


class SourceConfigError(ValueError):
    """A source YAML file is malformed. Raised with the offending path."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    method: str
    lang: str
    category: str
    priority: int
    active: bool
    homepage: str | None = None
    selectors: str | None = None
    inactive_reason: str | None = None
    rate_limit: float | None = None
    notes: str | None = None
    path: Path | None = field(default=None, compare=False)

    @property
    def poll_interval_minutes(self) -> int:
        return POLL_INTERVAL_MINUTES[self.priority]


_ALLOWED_KEYS = {
    "id", "name", "url", "method", "lang", "category", "priority", "active",
    "homepage", "selectors", "inactive_reason", "rate_limit", "notes",
}
_REQUIRED_KEYS = {"id", "name", "url", "method", "lang", "category", "priority", "active"}


def parse_source(data: dict, path: Path) -> Source:
    """Validate one parsed YAML document into a Source, or raise SourceConfigError."""

    def fail(msg: str) -> None:
        raise SourceConfigError(f"{path.name}: {msg}")

    if not isinstance(data, dict):
        fail("file must contain a YAML mapping")

    unknown = set(data) - _ALLOWED_KEYS
    if unknown:
        fail(f"unknown key(s): {', '.join(sorted(unknown))}")

    missing = _REQUIRED_KEYS - set(data)
    if missing:
        fail(f"missing required key(s): {', '.join(sorted(missing))}")

    if data["id"] != path.stem:
        fail(f"id {data['id']!r} must match the filename ({path.stem!r})")
    if data["method"] not in METHODS:
        fail(f"method {data['method']!r} must be one of {sorted(METHODS)}")
    if data["lang"] not in LANGS:
        fail(f"lang {data['lang']!r} must be one of {sorted(LANGS)}")
    if data["category"] not in CATEGORIES:
        fail(f"category {data['category']!r} must be one of {sorted(CATEGORIES)}")
    if data["priority"] not in PRIORITIES:
        fail(f"priority {data['priority']!r} must be one of {sorted(PRIORITIES)}")
    if not str(data["url"]).startswith(("http://", "https://")):
        fail("url must be absolute")
    if not data["active"] and not data.get("inactive_reason"):
        fail("inactive_reason is required when active: false")
    if data["method"] == "html" and not data.get("selectors"):
        fail("html sources need a `selectors` pack")

    return Source(path=path, **data)


def load_source(path: Path) -> Source:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SourceConfigError(f"{path.name}: invalid YAML: {exc}") from exc
    return parse_source(data, path)


def load_sources(directory: Path, *, active_only: bool = False) -> dict[str, Source]:
    """Load every outlet in `directory`, keyed by id. Raises on the first bad file."""
    sources: dict[str, Source] = {}
    for path in sorted(directory.glob("*.yaml")):
        source = load_source(path)
        sources[source.id] = source
    if active_only:
        return {k: v for k, v in sources.items() if v.active}
    return sources


def load_selectors(directory: Path, name: str) -> dict:
    path = directory / f"{name}.yaml"
    if not path.exists():
        raise SourceConfigError(f"selector pack {name!r} not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SourceConfigError(f"{path.name}: selector pack must be a YAML mapping")
    return data
