"""RawItem -> Article: the canonical shape everything downstream reads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.ingestion.base import RawItem
from app.parsing.clean import looks_devanagari
from app.pipeline.dedupe import simhash
from app.settings import NPT
from app.sources import Source

# Tracking parameters that change the URL without changing the article.
TRACKING_PREFIXES = ("utm_", "pk_", "mc_")
TRACKING_PARAMS = {"fbclid", "gclid", "igshid", "ref", "ref_src", "share", "amp"}


def canonical_url(url: str) -> str:
    """Strip everything that varies without changing which article it is.

    Annapurna Post publishes http:// links in its feed but serves https://, so
    without the scheme upgrade the same story would be stored twice.
    """
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(TRACKING_PREFIXES) and k.lower() not in TRACKING_PARAMS
    ]

    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


@dataclass
class Article:
    source_id: str
    url: str
    url_hash: str
    title: str
    body: str
    summary: str
    author: str | None
    lang: str
    category: str
    published_at: datetime
    published_estimated: bool
    fetched_at: datetime
    image_url: str | None
    simhash: int

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.body or self.summary}".strip()


def normalize(item: RawItem, source: Source, *, fetched_at: datetime | None = None) -> Article:
    fetched_at = fetched_at or datetime.now(NPT)

    published = item.published
    estimated = item.published_estimated
    if published is None:
        # Annapurna Post's normal case when the article page yields nothing:
        # better an honest "first seen at" than a fabricated date.
        published = fetched_at
        estimated = True
    elif published.tzinfo is None:
        published = published.replace(tzinfo=NPT)

    # The feed's <language> is routinely wrong; the source config is the
    # authority, and the script check is the tiebreaker for mixed outlets.
    lang = source.lang
    if lang == "en" and looks_devanagari(item.title):
        lang = "ne"

    canonical = canonical_url(item.url)
    body = item.body or ""

    return Article(
        source_id=source.id,
        url=canonical,
        url_hash=url_hash(canonical),
        title=item.title,
        body=body,
        summary=item.summary,
        author=item.author,
        lang=lang,
        category=source.category,
        published_at=published,
        published_estimated=estimated,
        fetched_at=fetched_at,
        image_url=item.image,
        simhash=simhash(f"{item.title} {body or item.summary}"),
    )
