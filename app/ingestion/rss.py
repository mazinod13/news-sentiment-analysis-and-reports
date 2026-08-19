"""Generic RSS/Atom scraper. Handles most outlets without a line of new code.

Annapurna Post is the reference case and exercises the awkward parts: no
<pubDate>, http:// links, a lying <language>, and truncated descriptions.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

import feedparser

from app.ingestion.base import BaseScraper, RawItem, ScrapeError
from app.ingestion.fetcher import FetchError
from app.parsing.article import extract
from app.parsing.clean import clean
from app.parsing.dates import is_implausible, parse_feed_datetime

log = logging.getLogger(__name__)


class RSSScraper(BaseScraper):
    method = "rss"

    def fetch(self, *, etag: str | None = None, last_modified: str | None = None) -> list[RawItem]:
        try:
            response = self.fetcher.get(
                self.source.url,
                etag=etag,
                last_modified=last_modified,
                rate_limit=self.source.rate_limit,
            )
        except FetchError as exc:
            raise ScrapeError(self.source.id, str(exc)) from exc

        self._state = {
            "etag": response.etag,
            "last_modified": response.last_modified,
            "not_modified": response.not_modified,
        }
        if response.not_modified:
            log.info("feed unchanged (304)", extra=self._log_extra(self.source.url))
            return []

        # Parse from bytes: feedparser then honours the XML declaration rather
        # than whatever encoding httpx guessed.
        feed = feedparser.parse(response.content)
        if feed.bozo and not feed.entries:
            raise ScrapeError(self.source.id, f"unparseable feed: {feed.bozo_exception}")

        items: list[RawItem] = []
        for entry in feed.entries:
            link = getattr(entry, "link", None)
            title = clean(getattr(entry, "title", ""))
            if not link or not title:
                continue

            published = parse_feed_datetime(entry)
            if published and is_implausible(published):
                log.debug("discarding implausible feed date %s", published,
                          extra=self._log_extra(link))
                published = None

            items.append(
                RawItem(
                    url=urljoin(response.url, link),
                    title=title,
                    summary=clean(getattr(entry, "summary", "")),
                    published=published,
                    author=clean(getattr(entry, "author", "")) or None,
                    guid=getattr(entry, "id", None) or link,
                )
            )

        log.info("parsed %d items", len(items), extra=self._log_extra(self.source.url))
        return items

    def fetch_body(self, item: RawItem) -> None:
        """Fetch the article page and apply the selector pack.

        Skipped entirely when the source declares no selector pack -- then the
        feed summary is all we store.
        """
        if not self.selectors:
            return
        try:
            response = self.fetcher.get(item.url, rate_limit=self.source.rate_limit)
        except FetchError as exc:
            log.warning("body fetch failed: %s", exc, extra=self._log_extra(item.url))
            return

        article = extract(response.text, self.selectors, source_id=self.source.id)
        if article.body:
            item.body = article.body
        if article.author and not item.author:
            item.author = article.author
        if article.image:
            item.image = article.image
        # The feed's date wins when it has one; the page fills the gap when not.
        if article.published and not item.published:
            item.published = article.published

    def _log_extra(self, url: str) -> dict:
        return {"source_id": self.source.id, "method": self.method, "url": url}
