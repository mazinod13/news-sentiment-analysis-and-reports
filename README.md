# Nepal News Sentiment

Scraping, normalisation, sentiment analysis and reporting over Nepali and English news, government portals and social media.

The pipeline ingests **60 news feeds (52 active)**, **87 Nitter + 8 X accounts**, **3 subreddits** and **31 government / financial / geospatial portals** on a schedule, normalises everything into one article schema in Postgres, scores it for sentiment and entities, and renders periodic reports. See [DATA_SOURCES.md](DATA_SOURCES.md) for the full source inventory.

```
fetch → parse → normalise → dedupe → enrich (NLP) → store → report
```

---

## Contents

- [Quick start](#quick-start)
- [Folder structure](#folder-structure)
- [How the pipeline works](#how-the-pipeline-works)
- [Configuring sources](#configuring-sources)
- [Guide: adding a new source](#guide-adding-a-new-source)
- [Guide: writing a scraper](#guide-writing-a-scraper)
- [Scheduling and polling](#scheduling-and-polling)
- [Data model](#data-model)
- [Deduplication](#deduplication)
- [NLP: sentiment, entities, topics](#nlp-sentiment-entities-topics)
- [Reports](#reports)
- [Nepali-specific handling](#nepali-specific-handling)
- [Scraping etiquette and hard rules](#scraping-etiquette-and-hard-rules)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Conventions](#conventions)

---

## Quick start

```bash
# 1. Environment
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on POSIX
pip install -r requirements-dev.txt
pip install -e . --no-deps      # register the `app` package

# 2. Config
cp .env.example .env             # fill DATABASE_URL and any API keys

# 3. Database
docker compose up -d postgres
python -m app.main db upgrade

# 4. Run one source end-to-end (parses and prints, stores nothing)
python -m app.main probe annapurna-post

# 5. Ingest for real
python -m app.main ingest --source annapurna-post
python -m app.main ingest --priority 1      # a whole tier
python -m app.main ingest --due             # everything past next_run_at

# 6. Run the worker (continuous, priority-driven)
python -m app.main worker
```

Everything runs in Docker too — see **[GUIDE.md](GUIDE.md)**, which is the
walkthrough for adding an outlet and for working on this with someone else.

`probe` and `pytest` need no database, so you can develop an entire outlet
before Postgres is even up. Nothing outside `data/` and Postgres is written to.

---

## Folder structure

This is the **target** layout. Phase 1 (news outlets, RSS + HTML) is built;
`nitter.py`, `x_api.py`, `reddit.py`, `portals/`, `nlp/` and `reports/` are the
shape they will take, not files that exist today. [GUIDE.md](GUIDE.md) tracks
what is actually working.

```
NEWS-SENTIMENT/
├── README.md                     # this file
├── GUIDE.md                      # ← start here to add an outlet
├── DATA_SOURCES.md               # generated inventory of every external source
├── pyproject.toml                # deps + tooling config (ruff, pytest) — source of truth
├── requirements.txt              # runtime deps, mirrors pyproject (drift is test-enforced)
├── requirements-dev.txt          # + pytest, ruff
├── Dockerfile
├── docker-compose.yml            # postgres + app + opt-in worker
├── .env.example                  # every env var the app reads, documented
│
├── config/
│   ├── sources/                  # ← the source registry: ONE FILE PER OUTLET.
│   │   ├── annapurna-post.yaml   #   Never a shared list -- see GUIDE.md for why.
│   │   ├── kathmandu-post.yaml
│   │   └── ...
│   ├── selectors/                # per-site CSS packs for article/listing pages
│   │   ├── annapurna-post.yaml
│   │   ├── ekantipur.yaml
│   │   └── ...
│   ├── keywords.yaml             # entity + topic lexicon (Devanagari & romanised)
│   └── logging.yaml
│
├── app/
│   ├── main.py                   # CLI: ingest | worker | report | db | sources
│   ├── settings.py               # env → typed settings
│   │
│   ├── ingestion/                # ── everything that touches the network
│   │   ├── base.py               # BaseScraper: the contract every scraper implements
│   │   ├── fetcher.py            # shared httpx client: retries, backoff, ETag cache,
│   │   │                         #   per-host rate limits, robots.txt, User-Agent
│   │   ├── rss.py                # feedparser-based scraper (method: rss)
│   │   ├── html.py               # selector-pack listing scraper (method: html)
│   │   ├── nitter.py             # Nitter instances with failover (method: nitter)
│   │   ├── x_api.py              # X/Twitter API v2 (method: x)
│   │   ├── reddit.py             # Reddit JSON endpoints (method: reddit)
│   │   ├── portals/              # bespoke government / financial scrapers
│   │   │   ├── bipad.py          #   disaster incidents (NDRRMA)
│   │   │   ├── nrb.py            #   forex + monetary indicators
│   │   │   ├── nepse.py          #   market index & prices
│   │   │   ├── noc.py            #   fuel prices
│   │   │   └── ...
│   │   └── registry.py           # method string → scraper class
│   │
│   ├── parsing/                  # ── pure functions, no network, easy to test
│   │   ├── article.py            # full-text extraction from an article page
│   │   ├── dates.py              # Bikram Sambat ⇄ Gregorian, tz normalisation
│   │   ├── language.py           # ne / en detection, script detection
│   │   └── clean.py              # boilerplate strip, whitespace, entity decode
│   │
│   ├── pipeline/
│   │   ├── run.py                # orchestrates fetch → … → store for one source
│   │   ├── normalize.py          # raw item → canonical Article
│   │   ├── dedupe.py             # URL canonicalisation + near-duplicate detection
│   │   └── enrich.py             # calls into app/nlp
│   │
│   ├── nlp/
│   │   ├── sentiment.py          # multilingual sentiment scoring (-1 … +1)
│   │   ├── entities.py           # people, parties, places, orgs
│   │   ├── topics.py             # topic/category assignment
│   │   └── embeddings.py         # vectors for clustering & similarity
│   │
│   ├── storage/
│   │   ├── db.py                 # engine + session lifecycle
│   │   ├── models.py             # SQLAlchemy models
│   │   ├── repositories.py       # all queries live here, not in the pipeline
│   │   └── migrations/           # alembic
│   │
│   ├── reports/
│   │   ├── daily.py              # daily sentiment digest
│   │   ├── weekly.py             # trends, movers, entity leaderboards
│   │   ├── templates/            # jinja2 → markdown / html
│   │   └── export.py             # csv / json / pdf writers
│   │
│   ├── scheduler/
│   │   └── worker.py             # priority-driven loop, per-source next_run_at
│   │
│   └── utils/                    # logging, hashing, retry, text helpers
│
├── scripts/
│   ├── new_source.py             # scaffolds all four files for a new outlet
│   ├── gen_sources.py            # regenerates DATA_SOURCES.md from config/sources/
│   ├── health_check.py           # flags feeds returning 0 items / 4xx / 5xx
│   └── backfill.py               # historical re-ingest over a date range
│
├── tests/
│   ├── conftest.py               # fixture loaders shared by every test
│   ├── fixtures/                 # saved XML/HTML payloads — never hit the network
│   │   ├── annapurna-post_feed.xml
│   │   └── annapurna-post_story.html
│   ├── sources/                  # ONE TEST FILE PER OUTLET, owned by its author
│   │   ├── test_annapurna_post.py
│   │   └── ...
│   ├── test_dates.py             # shared: BS conversion, feed dates
│   ├── test_pipeline.py          # shared: canonical URLs, dedupe, config validation
│   └── test_packaging.py         # shared: requirements.txt vs pyproject.toml
│
├── data/                         # gitignored
│   ├── raw/                      # optional raw payload archive
│   ├── cache/                    # ETag / Last-Modified cache
│   └── exports/                  # generated reports
│
└── docs/
    ├── ARCHITECTURE.md
    └── API_REFERENCE.md
```

**Rule of thumb for where code goes:** if it touches the network it belongs in `app/ingestion/`. If it transforms text it belongs in `app/parsing/`. If it writes SQL it belongs in `app/storage/repositories.py`. `app/pipeline/` only wires those together.

---

## How the pipeline works

| Stage | Module | What happens |
|---|---|---|
| 1. Fetch | `ingestion/fetcher.py` | Conditional GET with stored ETag/Last-Modified, per-host rate limit, exponential backoff on 429/5xx. A 304 short-circuits the whole cycle for that source. |
| 2. Parse | `ingestion/{rss,html,nitter,…}.py` | Method-specific extraction into a loose `RawItem` (url, title, summary, published, author). |
| 3. Fetch article body | `parsing/article.py` | Only for items not already stored. RSS summaries are usually truncated, so the article page is fetched for full text. |
| 4. Normalise | `pipeline/normalize.py` | Canonical URL, timezone-aware timestamp (Asia/Kathmandu, +05:45), language tag, source id, cleaned body. |
| 5. Dedupe | `pipeline/dedupe.py` | Drop exact and near-duplicates — syndicated copy across outlets is the norm here. |
| 6. Enrich | `pipeline/enrich.py` → `nlp/` | Sentiment score, entities, topics, embedding. |
| 7. Store | `storage/repositories.py` | Upsert on `url_hash`. Ingest is idempotent — re-running a cycle changes nothing. |
| 8. Report | `reports/` | Scheduled aggregation over stored articles. |

Each stage is independently runnable, so a parser fix can be replayed over stored raw payloads without re-scraping.

---

## Configuring sources

Every outlet is **one file** in `config/sources/`, named after its `id`.
**Never hardcode a URL in a scraper.**

There is deliberately no single `sources.yaml`. A shared list is a file every
concurrent branch appends to, so it conflicts on every merge; one file per
outlet means two people adding two outlets never touch the same file. The
single view a combined file would have given you is available two other ways:
`python -m app.main sources` and the generated [DATA_SOURCES.md](DATA_SOURCES.md).

`config/sources/kathmandu-post.yaml`:

```yaml
id: kathmandu-post                # must equal the filename; the DB foreign key
name: The Kathmandu Post
url: https://kathmandupost.com/rss
method: rss                       # rss | html   (more methods land later)
lang: en                          # en | ne -- overrides whatever the feed claims
category: news                    # news | economic | govt | disaster | social | ...
priority: 1                       # 1-4, drives poll frequency
active: true
```

`config/sources/ekantipur.yaml` — a listing-page outlet with no usable feed:

```yaml
id: ekantipur
name: eKantipur
url: https://ekantipur.com/
method: html
lang: ne
category: news
priority: 1
active: true
selectors: ekantipur              # → config/selectors/ekantipur.yaml
```

`config/sources/naya-patrika.yaml` — documented, but not scheduled:

```yaml
id: naya-patrika
name: Naya Patrika
url: https://www.nayapatrikadaily.com/feed
method: rss
lang: ne
category: news
priority: 2
active: false
inactive_reason: RSS removed / site down    # required when active: false
```

| Field | Required | Notes |
|---|---|---|
| `id` | yes | Must equal the filename. Immutable — changing it orphans stored articles. |
| `method` | yes | Resolved through `ingestion/registry.py`. |
| `priority` | yes | 1 = every ~15 min … 4 = every ~6 h. |
| `active` | yes | `false` keeps the outlet documented but unscheduled. |
| `inactive_reason` | when inactive | Validation fails without it. Surfaces in DATA_SOURCES.md. |
| `selectors` | html; optional for rss | Names a file in `config/selectors/`. |
| `homepage` | no | Site root. |
| `rate_limit` | no | Seconds between requests; overrides the per-host default. |
| `notes` | no | Quirks you hit while wiring it up. Write them down. |

Configs are validated on load — `python -m app.main sources` fails with the
offending filename and reason. After adding an outlet, regenerate the inventory:

```bash
python scripts/gen_sources.py        # rewrites DATA_SOURCES.md
```

---

## Guide: adding a new source

**[GUIDE.md](GUIDE.md) is the full walkthrough** — step by step, with Annapurna
Post worked end to end. The short version:

### An RSS feed

1. **Verify the feed is real.** Many Nepali outlets advertise `/feed` but return HTML, an empty channel, or a 404 page with a 200 status:
   ```bash
   curl -sIL https://example.com.np/feed         # expect 200 + xml content-type
   curl -sL  https://example.com.np/feed | head -40
   ```
   Note any redirect — the config must hold the *final* URL.
2. **Scaffold it.** Creates the four outlet-named files and saves the feed as a fixture:
   ```bash
   python scripts/new_source.py --id example-np --name "Example" \
       --url https://example.com.np/feed --lang ne --priority 2
   ```
3. **Probe it** — parses and prints, writes nothing:
   ```bash
   python -m app.main probe example-np
   ```
   Check that titles are not truncated, links are absolute, and `published` is a real date rather than `[ESTIMATED]`.
4. **Write the selector pack** in `config/selectors/example-np.yaml`, save an article page as a fixture, and assert the result in `tests/sources/test_example_np.py`.
5. **Regenerate docs** and commit the outlet's four files plus `DATA_SOURCES.md` together.

### An HTML listing page

Same as above with `method: html`, plus a `listing:` block in the selector pack
describing the repeated card on the index page:

```yaml
listing:
  item: "article.news-item"          # repeated container on the index page
  link: "h3 a@href"
  title: "h3 a"
  summary: "p.excerpt"
  published: "time@datetime"

article:
  body: "div.article-content p"      # joined in document order
  author: "span.author"
  published: "meta[property='article:published_time']@content"

exclude:                             # dropped before body assembly
  - "div.related-posts"
  - "div.advertisement"
  - "aside"
```

Selectors live in YAML, not Python, so a site redesign is a config change — not a code change and not a deploy.

### A government or financial portal

These are bespoke: they paginate oddly, hide data behind POST forms, or serve PDFs. Add a module under `app/ingestion/portals/`, register it in `registry.py` under `method: portal` with a `handler:` key, and keep the parsing half in `app/parsing/` so it stays unit-testable.

---

## Guide: writing a scraper

Every scraper implements one contract:

```python
# app/ingestion/base.py
class BaseScraper(ABC):
    method: ClassVar[str]

    def __init__(self, source: Source, fetcher: Fetcher, selectors: dict | None = None): ...

    @abstractmethod
    def fetch(self, *, etag=None, last_modified=None) -> list[RawItem]:
        """Return items from the listing/feed. Do NOT fetch article bodies here."""

    def fetch_body(self, item: RawItem) -> None:
        """Optional. Called only for items the pipeline decided are new."""
```

Scrapers are **synchronous**. Concurrency lives one level up: the worker runs
sources in a thread pool, and the `Fetcher` serialises requests per host. Since
politeness caps us at ~1 request/sec/host regardless, the only concurrency
worth having is *across* hosts — and threads buy that while keeping every
scraper ordinary, steppable code.

Rules that keep the pipeline predictable:

- **Return, don't store.** A scraper never touches the database.
- **Never construct your own HTTP client.** Use the injected `Fetcher` — it carries the rate limiter, retry policy, cache and User-Agent.
- **Fail loudly, per source.** Raise `ScrapeError` with the source id; the worker isolates the failure, marks the source unhealthy, and continues with the others.
- **No sleeps inside a scraper.** Pacing is the fetcher's job.
- **Absolute URLs only.** Resolve against the listing URL before returning.
- **Timezone-aware datetimes only.** Naive datetimes are rejected at normalisation.

Minimal example — see [app/ingestion/rss.py](app/ingestion/rss.py) for the real one:

```python
class RSSScraper(BaseScraper):
    method = "rss"

    def fetch(self, *, etag=None, last_modified=None) -> list[RawItem]:
        response = self.fetcher.get(self.source.url, etag=etag, last_modified=last_modified)
        if response.not_modified:
            return []
        # Parse from bytes so feedparser honours the XML declaration rather
        # than whatever encoding the HTTP layer guessed.
        feed = feedparser.parse(response.content)
        return [
            RawItem(
                url=urljoin(response.url, e.link),
                title=clean(e.title),
                summary=clean(getattr(e, "summary", "")),
                published=parse_feed_datetime(e),   # None is normal, not an error
            )
            for e in feed.entries
            if getattr(e, "link", None)
        ]
```

---

## Scheduling and polling

The worker keeps a `next_run_at` per source and wakes for whatever is due.

| Priority | Interval | Typical sources |
|:--:|---|---|
| 1 | ~15 min | Kathmandu Post, OnlineKhabar, Setopati, BBC Nepali, eKantipur |
| 2 | ~30 min | AP1, Gorkhapatra, Khabarhub, Nepali Times, MeroLagani |
| 3 | ~60 min | Provincial editions, regional outlets |
| 4 | ~6 h | Low-volume local sites |

Backoff on failure: interval × 2 per consecutive failure, capped at 6 h. Five consecutive failures flags the source in `health_check.py` output; it is never auto-disabled, since transient Nepali-host downtime is common.

```bash
python -m app.main worker                          # continuous
python -m app.main ingest --once                   # one cycle of everything due
python -m app.main ingest --source ekantipur       # force one source now
python -m app.main ingest --priority 1             # force a whole tier
python scripts/health_check.py --since 24h         # what is failing
```

---

## Data model

Core table (`app/storage/models.py`):

| Column | Type | Notes |
|---|---|---|
| `id` | bigint PK | |
| `source_id` | text | the outlet's `id` from `config/sources/<id>.yaml` |
| `url` | text | canonical, tracking params stripped |
| `url_hash` | char(64) | sha256 of canonical url — **unique**, the upsert key |
| `title` | text | |
| `body` | text | full extracted article text |
| `summary` | text | feed summary or first paragraphs |
| `author` | text | nullable |
| `lang` | char(2) | `ne` / `en` |
| `category` | text | from source, refined by `nlp/topics.py` |
| `published_at` | timestamptz | source time, normalised to UTC |
| `fetched_at` | timestamptz | |
| `sentiment` | real | −1.0 … +1.0 |
| `sentiment_label` | text | negative / neutral / positive |
| `entities` | jsonb | `[{type, name, salience}]` |
| `topics` | text[] | |
| `embedding` | vector(768) | pgvector, for clustering |
| `simhash` | bigint | near-duplicate detection |
| `cluster_id` | bigint | nullable, FK → story_clusters |

Supporting tables: `sources` (runtime health/state, mirrored from YAML), `story_clusters` (deduplicated events across outlets), `reports`, `fetch_log`.

---

## Deduplication

Nepali outlets syndicate heavily — one agency story can appear across a dozen sites within an hour. Three layers:

1. **Exact:** `url_hash` unique constraint after canonicalisation (strip `utm_*`, `fbclid`, fragments, trailing slash; force https; lowercase host).
2. **Near-duplicate:** 64-bit simhash over the normalised body. Hamming distance ≤ 3 → same article, keep the earliest `published_at`.
3. **Story clustering:** embedding cosine similarity within a rolling 48 h window groups different articles about the same event into a `story_cluster`. Reports aggregate at cluster level so one event does not count twelve times.

---

## NLP: sentiment, entities, topics

- **Sentiment** runs on title + body. Nepali and English take different paths — do not run an English-only model over Devanagari text and trust the output. Scores are stored as a float plus a bucketed label; the raw float is what reports trend on.
- **Entities** are matched against `config/keywords.yaml`, which carries both Devanagari and romanised spellings for every politician, party, ministry and district. Nepali names romanise inconsistently (Prachanda / Pushpa Kamal Dahal / पुष्पकमल दाहाल), so aliases matter more than the model.
- **Topics** start from the source category and are refined per-article.
- LLM-backed enrichment is **feature-flagged and optional** (`ENABLE_LLM_ENRICH=false` by default). The pipeline must produce complete output with every external API disabled.

---

## Reports

```bash
python -m app.main report daily --date 2026-08-19
python -m app.main report weekly --end 2026-08-19 --format html
python -m app.main report entity --name "Nepali Congress" --since 30d
```

Output lands in `data/exports/`. Daily covers volume by source, sentiment distribution, top story clusters, entity movers. Weekly adds trend deltas and week-over-week comparisons.

---

## Nepali-specific handling

Things that break naive scrapers here, all handled in `app/parsing/`:

- **Bikram Sambat dates.** Government portals and some outlets publish in BS (२०८२). `parsing/dates.py` converts BS → Gregorian and parses Devanagari numerals.
- **Timezone.** Nepal is **UTC+05:45**. Store UTC, render in `Asia/Kathmandu`. Never assume +05:30.
- **Devanagari numerals.** `०१२३४५६७८९` must be transliterated before any numeric parsing.
- **Mixed-script articles.** English quotes inside Nepali bodies are common — script detection is per-paragraph, not per-document.
- **Legacy encodings.** A few older sites serve non-UTF-8, or ship Preeti-font text as ASCII; detect and either transliterate or skip rather than storing mojibake.
- **Unreliable feed dates.** Many feeds emit today's date for every item. When `published_at` looks implausible, fall back to the article page's `<meta>` tags before trusting the feed.

---

## Scraping etiquette and hard rules

- Honour `robots.txt`. `fetcher.py` checks it and caches the result; a disallowed path is skipped, not worked around.
- Identify honestly in the User-Agent, with a contact URL.
- Default ≤ 1 request/sec per host, and never parallelise requests within a single host.
- Always send conditional headers. A 304 costs the publisher nothing.
- Back off on 429/503 — exponentially, and respect `Retry-After`.
- Poll public feeds and pages only. No authentication walls, no paywall circumvention, no credential use.
- Store canonical source URLs and attribute every article to its outlet. This corpus is for analysis, not republication.

---

## Testing

Run tests with **`pytest`**, never `python tests/...`. A test file has no
`__main__`; running it directly executes the imports and stops, and the
fixtures pytest injects (`outlet`, `fixture_text`) never get created.

```bash
pytest                                          # everything
pytest tests/sources/test_pokhara_hotline.py    # one outlet
pytest tests/sources/test_pokhara_hotline.py -v # per-test names
pytest -k "date"                                # only tests matching a substring
pytest -x                                       # stop at the first failure
pytest --lf                                     # re-run just last run's failures
ruff check .
```

In Docker: `docker compose run --rm app pytest`.

Tests never hit the network. Every parser test loads a saved payload from `tests/fixtures/`. When a site changes and a scraper breaks, the fix is: refresh the fixture, update the selector pack, confirm the test fails before the fix and passes after.

---

## Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Feed returns 0 items | Site dropped RSS and serves an HTML error page | `curl -sL <url> | head -40`; consider switching to `method: html` |
| Every article has today's date | Feed emits `pubDate = now` | `parsing/dates.py` meta-tag fallback |
| Body is nav/footer boilerplate | Selector pack stale after redesign | `config/selectors/<site>.yaml`, `exclude` list |
| Mojibake / boxes in body | Encoding not UTF-8, or Preeti font text | `parsing/clean.py` encoding detection |
| Same story stored 10× | Canonicalisation missing a param, or simhash threshold too tight | `pipeline/dedupe.py` |
| 429s from one host | Rate limit override too aggressive | `rate_limit` in the outlet's config |
| Nitter returns nothing | Instance down — normal, they rotate constantly | `ingestion/nitter.py` instance failover list |
| Sentiment always neutral on Nepali | English-only model on Devanagari input | `nlp/sentiment.py` language routing |

---

## Conventions

- **Python 3.11+**, synchronous scrapers with a thread pool at the worker level, `ruff` for lint and format, type hints on every public function.
- **Config over code.** New outlet = one YAML file plus a selector pack. Shared code changes only for a genuinely new *method* or capability.
- **One file per outlet.** Nothing an outlet needs lives in a file another outlet also edits — that is what keeps concurrent branches merging cleanly.
- **Idempotent ingest.** Running the same cycle twice must produce the same database.
- **One source's failure never stops a cycle.** Errors are caught per source, logged with the source id, and recorded in `fetch_log`.
- **Structured logging.** Always include `source_id`, `method` and `url`; logs are the only forensics available when a remote site changes silently.
- **Secrets in `.env` only** — never in a source YAML, never committed. `.env.example` documents every variable the app reads.
- **`DATA_SOURCES.md` status block is generated.** Add the outlet under `config/sources/`, then run `scripts/gen_sources.py` (`--check` in CI). Never hand-edit between the `BEGIN/END GENERATED` markers.
