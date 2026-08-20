# Workspace Map

## Entry Points & Run/Test Commands
- CLI Entrypoint: `python -m app.main`
  - List & validate sources: `python -m app.main sources`
  - Probe a source (fetch, parse, print): `python -m app.main probe <source_id>`
  - Run ingestion: `python -m app.main ingest [--source <id> | --priority <1-4> | --due]`
  - Run worker (scheduler): `python -m app.main worker`
  - Database migration/setup: `python -m app.main db upgrade`
- Test suite: `pytest`
- Linter: `ruff check`

## Required Environment Variables
- `DATABASE_URL`: Connection string for PostgreSQL database. Default: `postgresql+psycopg://news:news@localhost:5433/news_sentiment`
- `USER_AGENT`: User agent string for HTTP requests. Default: `NepalNewsSentiment/0.1`
- `REQUEST_TIMEOUT`: Timeout in seconds for HTTP requests. Default: `20`
- `PER_HOST_DELAY`: Delay in seconds between requests to the same host. Default: `1.0`
- `MAX_RETRIES`: Max retries for requests. Default: `3`
- `RESPECT_ROBOTS`: Respect robots.txt. Default: `True`
- `FETCH_BODIES`: Fetch full article bodies. Default: `True`
- `MAX_BODIES_PER_RUN`: Max bodies fetched per source run. Default: `25`
- `INGEST_CONCURRENCY`: Ingest concurrency. Default: `4`
- `LOG_LEVEL`: Logging level. Default: `INFO`
- `SOURCES_DIR`: Custom path to sources configs.
- `SELECTORS_DIR`: Custom path to selectors configs.

## Directory Tree & File Directory
### `app/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/__init__.py)**: Python module
- **[`main.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/main.py)**: CLI entrypoint.
  - `def _build_parser() -> argparse.ArgumentParser`
  - `def cmd_sources(settings) -> int`
  - `def cmd_probe(settings, args) -> int`
  - `def cmd_ingest(settings, args) -> int`
  - `def main(argv) -> int`
- **[`settings.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/settings.py)**: Environment -> typed settings. Every env var the app reads is declared here.
  - `def _bool(name, default) -> bool`
  - `class Settings`
  - `def load_settings() -> Settings`
- **[`sources.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/sources.py)**: Loads and validates config/sources/*.yaml -- one file per outlet.
  - `class SourceConfigError`
  - `class Source`
    - `def poll_interval_minutes(self) -> int`
  - `def parse_source(data, path) -> Source`
  - `def load_source(path) -> Source`
  - `def load_sources(directory) -> dict[str, Source]`
  - `def load_selectors(directory, name) -> dict`

### `app/ingestion/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/ingestion/__init__.py)**: Python module
- **[`base.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/ingestion/base.py)**: The contract every scraper implements.
  - `class RawItem`
  - `class ScrapeError`
    - `def __init__(self, source_id, message) -> None`
  - `class BaseScraper`
    - `def __init__(self, source, fetcher, selectors) -> None`
    - `def fetch(self) -> list[RawItem]`
    - `def fetch_body(self, item) -> None`
    - `def state(self) -> dict`
- **[`fetcher.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/ingestion/fetcher.py)**: The one place in the app that makes HTTP requests.
  - `class FetchError`
  - `class FetchResult`
  - `class Fetcher`
    - `def __init__(self, settings, client) -> None`
    - `def close(self) -> None`
    - `def get(self, url) -> FetchResult`
  - `def _retry_after(response) -> float | None`
- **[`html.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/ingestion/html.py)**: Listing-page scraper for outlets with no usable RSS (Ratopati, eKantipur...).
  - `class HTMLScraper`
    - `def fetch(self) -> list[RawItem]`
    - `def fetch_body(self, item) -> None`
- **[`registry.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/ingestion/registry.py)**: method string -> scraper class.
  - `def build_scraper(source, fetcher, selectors_dir) -> BaseScraper`
- **[`rss.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/ingestion/rss.py)**: Generic RSS/Atom scraper. Handles most outlets without a line of new code.
  - `class RSSScraper`
    - `def fetch(self) -> list[RawItem]`
    - `def fetch_body(self, item) -> None`

### `app/parsing/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/parsing/__init__.py)**: Python module
- **[`article.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/parsing/article.py)**: Article-page extraction driven by a selector pack.
  - `class ExtractedArticle`
  - `def _split_selector(selector) -> tuple[str, str | None]`
  - `def select_one(tree, selector) -> str | None`
  - `def select_all(tree, selector) -> list[str]`
  - `def extract(html, selectors) -> ExtractedArticle`
- **[`clean.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/parsing/clean.py)**: Text cleaning. Pure functions -- no network, no config, trivially testable.
  - `def strip_tags(value) -> str`
  - `def clean(value) -> str`
  - `def to_ascii_digits(value) -> str`
  - `def looks_devanagari(value, threshold) -> bool`
- **[`dates.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/parsing/dates.py)**: Date handling: Bikram Sambat -> Gregorian, feed dates, meta-tag dates.
  - `class DateParseError`
  - `def parse_bs_datetime(text) -> datetime`
  - `def parse_datetime(text) -> datetime`
  - `def parse_feed_datetime(entry) -> datetime | None`
  - `def is_implausible(when) -> bool`

### `app/pipeline/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/pipeline/__init__.py)**: Python module
- **[`dedupe.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/pipeline/dedupe.py)**: Duplicate detection.
  - `def tokenize(text) -> list[str]`
  - `def shingles(tokens, size) -> list[str]`
  - `def simhash(text) -> int`
  - `def hamming(a, b) -> int`
  - `def to_signed64(value) -> int`
  - `def from_signed64(value) -> int`
  - `def is_near_duplicate(candidate, known) -> bool`
  - `def dedupe_batch(items, key) -> list`
- **[`normalize.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/pipeline/normalize.py)**: RawItem -> Article: the canonical shape everything downstream reads.
  - `def canonical_url(url) -> str`
  - `def url_hash(url) -> str`
  - `class Article`
    - `def text(self) -> str`
  - `def normalize(item, source) -> Article`
- **[`run.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/pipeline/run.py)**: Orchestrates one source, end to end.
  - `class RunReport`
  - `def run_source(source, settings, fetcher) -> RunReport`
  - `def _persist_state(settings, source, state_after, started, error) -> None`
  - `def _record_failure(settings, source, started, error) -> None`

### `app/scheduler/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/scheduler/__init__.py)**: Python module
- **[`worker.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/scheduler/worker.py)**: Priority-driven polling loop.
  - `def due_sources(settings, now) -> list[Source]`
  - `def run_once(settings, sources) -> list[RunReport]`
  - `def run_forever(settings) -> None`

### `app/storage/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/storage/__init__.py)**: Python module
- **[`db.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/storage/db.py)**: Engine and session lifecycle.
  - `def get_engine(settings) -> Engine`
  - `def session_scope(settings) -> Iterator[Session]`
  - `def create_all(settings) -> None`
- **[`models.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/storage/models.py)**: Database schema.
  - `class Base`
  - `class SourceState`
  - `class Article`
  - `class FetchLog`
- **[`repositories.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/storage/repositories.py)**: Every query lives here. The pipeline never writes SQL.
  - `def existing_url_hashes(session, hashes) -> set[str]`
  - `def recent_simhashes(session) -> list[int]`
  - `def upsert_article(session, article) -> bool`
  - `def get_state(session, source_id) -> SourceState`
  - `def save_state(session, source_id) -> None`
  - `def not_due_source_ids(session, now) -> set[str]`
  - `def log_fetch(session) -> None`

### `app/utils/`
- **[`__init__.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/utils/__init__.py)**: Python module
- **[`logging.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/app/utils/logging.py)**: Logging setup. Every ingestion log line carries source_id, method and url --
  - `class _ContextFormatter`
    - `def format(self, record) -> str`
  - `def setup_logging(level) -> None`

### `config/`

### `config/selectors/`
- **[`annapurna-post.yaml`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/config/selectors/annapurna-post.yaml)**: Configuration or data/documentation file.

### `config/sources/`
- **[`annapurna-post.yaml`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/config/sources/annapurna-post.yaml)**: Configuration or data/documentation file.

### `scripts/`
- **[`gen_sources.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/scripts/gen_sources.py)**: Regenerate the news-outlet tables in DATA_SOURCES.md from config/sources/.
  - `def active_table(sources) -> str`
  - `def inactive_table(sources) -> str`
  - `def replace_block(text, begin, end, body) -> str`
  - `def main() -> int`
- **[`new_source.py`](file:///D:/Nepal_Osint/news-sentiment-analysis-and-reports/scripts/new_source.py)**: Scaffold a new outlet: config + selector pack + test + saved fixture.
  - `def write(path, content, force) -> None`
  - `def main() -> int`
