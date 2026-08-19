"""method string -> scraper class.

This is the only shared file an outlet ever needs, and only when it introduces
a genuinely new *method*. Adding an outlet that uses an existing method touches
nothing here.
"""

from __future__ import annotations

from app.ingestion.base import BaseScraper
from app.ingestion.fetcher import Fetcher
from app.ingestion.html import HTMLScraper
from app.ingestion.rss import RSSScraper
from app.sources import Source, load_selectors

SCRAPERS: dict[str, type[BaseScraper]] = {
    RSSScraper.method: RSSScraper,
    HTMLScraper.method: HTMLScraper,
}


def build_scraper(source: Source, fetcher: Fetcher, selectors_dir) -> BaseScraper:
    try:
        scraper_class = SCRAPERS[source.method]
    except KeyError:
        raise ValueError(
            f"{source.id}: unknown method {source.method!r}; known: {sorted(SCRAPERS)}"
        ) from None

    selectors = load_selectors(selectors_dir, source.selectors) if source.selectors else {}
    return scraper_class(source, fetcher, selectors)
