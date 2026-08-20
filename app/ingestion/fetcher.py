"""The one place in the app that makes HTTP requests.

Owns politeness so no scraper has to think about it: robots.txt, per-host rate
limiting, retries with backoff, and conditional GETs. Scrapers receive a
Fetcher and must never build their own client -- that is how rate limits get
bypassed by accident.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.settings import Settings

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class FetchError(Exception):
    """A request failed after exhausting retries, or was blocked by robots.txt."""


@dataclass
class FetchResult:
    url: str           # final URL after redirects
    status: int
    text: str
    content: bytes
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


class Fetcher:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.Client(
            follow_redirects=True,          # annapurnapost.com/rss 301s to /rss/
            timeout=settings.request_timeout,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "ne,en;q=0.8",
            },
        )
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Fetcher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- politeness -----------------------------------------------------------

    def _wait_for_host(self, host: str, delay: float) -> None:
        """Serialise requests to one host. Different hosts never block each other."""
        while True:
            with self._lock:
                now = time.monotonic()
                earliest = self._last_request.get(host, 0.0) + delay
                if now >= earliest:
                    self._last_request[host] = now
                    return
                sleep_for = earliest - now
            time.sleep(sleep_for)

    def _allowed_by_robots(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._lock:
            known = origin in self._robots
            parser = self._robots.get(origin)
        if not known:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(f"{origin}/robots.txt")
            try:
                response = self._client.get(f"{origin}/robots.txt", timeout=10)
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser = None  # no usable robots.txt -> treat as allow-all
            except httpx.HTTPError:
                parser = None
            with self._lock:
                self._robots[origin] = parser
        return True if parser is None else parser.can_fetch(self.settings.user_agent, url)

    # -- requests -------------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        rate_limit: float | None = None,
    ) -> FetchResult:
        if not self._allowed_by_robots(url):
            raise FetchError(f"blocked by robots.txt: {url}")

        host = urlparse(url).netloc
        delay = rate_limit if rate_limit is not None else self.settings.per_host_delay

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            self._wait_for_host(host, delay)
            try:
                response = self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning("request failed (attempt %s): %s", attempt, exc, extra={"url": url})
                time.sleep(min(2 ** attempt, 30))
                continue

            if response.status_code == 304:
                return FetchResult(
                    url=str(response.url), status=304, text="", content=b"", not_modified=True
                )

            if response.status_code in RETRY_STATUSES:
                wait = _retry_after(response) or min(2 ** attempt, 60)
                last_error = FetchError(f"HTTP {response.status_code} from {url}")
                log.warning(
                    "HTTP %s, backing off %ss (attempt %s)",
                    response.status_code, wait, attempt, extra={"url": url},
                )
                time.sleep(wait)
                continue

            if response.status_code >= 400:
                raise FetchError(f"HTTP {response.status_code} from {url}")

            return FetchResult(
                url=str(response.url),
                status=response.status_code,
                text=response.text,
                content=response.content,
                etag=response.headers.get("etag"),
                last_modified=response.headers.get("last-modified"),
            )

        raise FetchError(
            f"giving up on {url} after {self.settings.max_retries} attempts: {last_error}"
        )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return min(float(raw), 120.0)
    except ValueError:
        return None
