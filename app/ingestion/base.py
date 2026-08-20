"""The contract every scraper implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from app.ingestion.fetcher import Fetcher
from app.sources import Source


@dataclass
class RawItem:
    """One item as the source presented it, before normalisation."""

    url: str
    title: str
    summary: str = ""
    published: datetime | None = None
    author: str | None = None
    guid: str | None = None
    body: str = ""
    image: str | None = None
    published_estimated: bool = False
    extra: dict = field(default_factory=dict)


class ScrapeError(Exception):
    """A source failed. Carries the source id so the worker can isolate it."""

    def __init__(self, source_id: str, message: str) -> None:
        super().__init__(f"[{source_id}] {message}")
        self.source_id = source_id


class BaseScraper(ABC):
    """Scrapers fetch and parse. They never touch the database.

    Rules:
      * return, don't store
      * never build your own HTTP client -- use self.fetcher
      * no sleeps; pacing belongs to the Fetcher
      * absolute URLs only
      * timezone-aware datetimes only, or None
    """

    method: ClassVar[str]

    def __init__(self, source: Source, fetcher: Fetcher, selectors: dict | None = None) -> None:
        self.source = source
        self.fetcher = fetcher
        self.selectors = selectors or {}

    @abstractmethod
    def fetch(self, *, etag: str | None = None, last_modified: str | None = None) -> list[RawItem]:
        """Return items from the feed or listing page.

        Do NOT fetch article bodies here -- the pipeline decides which items are
        new and only then calls fetch_body, so an unchanged feed costs one
        request instead of twenty.
        """

    def fetch_body(self, item: RawItem) -> None:
        """Fill in body/author/published from the article page. Mutates `item`."""
        raise NotImplementedError

    @property
    def state(self) -> dict:
        """Conditional-GET state observed during the last fetch()."""
        return getattr(self, "_state", {})
