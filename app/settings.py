"""Environment -> typed settings. Every env var the app reads is declared here."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

# Nepal is UTC+05:45. Never assume +05:30.
NPT = ZoneInfo("Asia/Kathmandu")

ROOT = Path(__file__).resolve().parent.parent

# PyMySQL is pure Python: 0.15 MB, against 14 MB for a driver that bundles a
# client library.
DEFAULT_DB_DRIVER = "pymysql"
# Devanagari needs real 4-byte UTF-8; MySQL's "utf8" is only three.
DB_CHARSET = "utf8mb4"


def database_url() -> str:
    """DATABASE_URL if set; otherwise built from MYSQL_* parts.

    Each part is URL-escaped, so a password containing @, /, : or # works as
    typed in .env -- pasted raw into a URL, those characters break it.

    TLS is not in the URL: PyMySQL takes it as connect arguments, built in
    app/storage/db.py from MYSQL_SSLMODE.
    """
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    driver = os.getenv("MYSQL_DRIVER", DEFAULT_DB_DRIVER)
    user = quote(os.getenv("MYSQL_USER", "news"), safe="")
    password = quote(os.getenv("MYSQL_PASSWORD", "news"), safe="")
    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")
    name = quote(os.getenv("MYSQL_DB", "news_sentiment"), safe="")
    return f"mysql+{driver}://{user}:{password}@{host}:{port}/{name}?charset={DB_CHARSET}"


def _csv_set(name: str) -> frozenset[str]:
    return frozenset(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


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
    criticality_file: Path
    heartbeat_file: Path
    health_max_age: int
    # disable | require | verify-ca | verify-full; None means no TLS requested.
    database_sslmode: str | None
    database_ssl_root_cert: str | None
    # Keys accepted in the API's X-API-Key header. Empty means the API is open.
    api_keys: frozenset[str]
    api_max_limit: int
    api_cors_origins: tuple[str, ...]
    api_bind: str
    api_port: int


def load_settings() -> Settings:
    return Settings(
        database_url=database_url(),
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
        # Terms, weights and A-F thresholds for criticality grading.
        criticality_file=Path(
            os.getenv("CRITICALITY_FILE", str(ROOT / "config" / "criticality.yaml"))
        ),
        # Touched by the worker after successful work; read by `health`.
        heartbeat_file=Path(
            os.getenv("HEARTBEAT_FILE", str(Path(tempfile.gettempdir()) / "news-worker.heartbeat"))
        ),
        # Generous: one Ratopati-heavy cycle can legitimately take ~10 minutes.
        health_max_age=int(os.getenv("HEALTH_MAX_AGE", "1800")),
        database_sslmode=os.getenv("MYSQL_SSLMODE") or None,
        database_ssl_root_cert=os.getenv("MYSQL_SSLROOTCERT") or None,
        api_keys=_csv_set("API_KEYS"),
        api_max_limit=int(os.getenv("API_MAX_LIMIT", "200")),
        api_cors_origins=tuple(sorted(_csv_set("API_CORS_ORIGINS"))),
        api_bind=os.getenv("API_BIND", "127.0.0.1"),
        api_port=int(os.getenv("API_PORT", "8000")),
    )
