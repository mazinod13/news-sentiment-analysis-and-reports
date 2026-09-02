"""Environment -> typed settings. Every env var the app reads is declared here."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

# Nepal is UTC+05:45. Never assume +05:30.
NPT = ZoneInfo("Asia/Kathmandu")

ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str
    user_agent: str
    request_timeout: float
    per_host_delay: float
    max_retries: int
    respect_robots: bool
    fetch_bodies: bool
    max_bodies_per_run: int
    ingest_concurrency: int
    log_level: str
    sources_dir: Path
    selectors_dir: Path
    ca_certs_dir: Path


def load_settings() -> Settings:
    return Settings(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://news:news@localhost:5433/news_sentiment",
        ),
        user_agent=os.getenv("USER_AGENT", "NepalNewsSentiment/0.1"),
        request_timeout=float(os.getenv("REQUEST_TIMEOUT", "20")),
        per_host_delay=float(os.getenv("PER_HOST_DELAY", "1.0")),
        max_retries=int(os.getenv("MAX_RETRIES", "3")),
        respect_robots=_bool("RESPECT_ROBOTS", True),
        fetch_bodies=_bool("FETCH_BODIES", True),
        max_bodies_per_run=int(os.getenv("MAX_BODIES_PER_RUN", "25")),
        ingest_concurrency=int(os.getenv("INGEST_CONCURRENCY", "4")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        sources_dir=Path(os.getenv("SOURCES_DIR", str(ROOT / "config" / "sources"))),
        selectors_dir=Path(os.getenv("SELECTORS_DIR", str(ROOT / "config" / "selectors"))),
        # Extra CA intermediates for sites that serve an incomplete chain.
        # See certs/README.md -- this augments verification, never skips it.
        ca_certs_dir=Path(os.getenv("CA_CERTS_DIR", str(ROOT / "certs"))),
    )
