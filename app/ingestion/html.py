"""Listing-page scraper for outlets with no usable RSS (Ratopati, eKantipur...).

Same selector-pack idea as the article extractor, one level up: a `listing`
block describes the repeated card on the index page.

    listing:
      item: "article.news-item"
      link: "h3 a@href"
      title: "h3 a"
      summary: "p.excerpt"
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from app.ingestion.base import BaseScraper, RawItem, ScrapeError
from app.ingestion.fetcher import FetchError
from app.parsing.article import extract, select_one
from app.parsing.dates import DateParseError, parse_datetime

log = logging.getLogger(__name__)


class HTMLScraper(BaseScraper):
    method = "html"

    def fetch(self, *, etag: str | None = None, last_modified: str | None = None) -> list[RawItem]:
        listing = self.selectors.get("listing")
        if not listing:
            raise ScrapeError(self.source.id, "selector pack has no `listing` block")

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
            return []

        tree = HTMLParser(response.text)
        items: list[RawItem] = []
        seen: set[str] = set()

        for card in tree.css(listing["item"]):
            href = select_one(card, listing["link"])
            title = select_one(card, listing["title"])
            if not href or not title:
                continue
            url = urljoin(response.url, href)
            if url in seen:          # index pages repeat the same story in several rails
                continue
            seen.add(url)

            published = None
            if published_selector := listing.get("published"):
                raw = select_one(card, published_selector)
                if raw:
                    try:
                        published = parse_datetime(raw, fmt=listing.get("published_format", "auto"))
                    except DateParseError:
                        pass

            summary = ""
            if summary_selector := listing.get("summary"):
                summary = select_one(card, summary_selector) or ""

            items.append(RawItem(url=url, title=title, summary=summary, published=published))

        log.info("parsed %d items", len(items),
                 extra={"source_id": self.source.id, "method": self.method, "url": self.source.url})
        return items

    def fetch_body(self, item: RawItem) -> None:
        try:
            response = self.fetcher.get(item.url, rate_limit=self.source.rate_limit)
        except FetchError as exc:
            log.warning("body fetch failed: %s", exc,
                        extra={"source_id": self.source.id, "url": item.url})
            return

        article = extract(response.text, self.selectors, source_id=self.source.id)
        if article.body:
            item.body = article.body
        if article.author and not item.author:
            item.author = article.author
        if article.image:
            item.image = article.image
        if article.published and not item.published:
            item.published = article.published
