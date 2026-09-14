# Nepal News Sentiment

Scrapes Nepali and English news outlets and government portals on a schedule,
normalises every item into one article schema, deduplicates syndicated copy,
and stores the corpus in Postgres for sentiment analysis and reporting.

```
fetch → parse → fetch body → normalise → dedupe → store
```

**59 sources are configured** — RSS feeds and HTML listing pages, national,
provincial and government. [DATA_SOURCES.md](DATA_SOURCES.md) has the
generated inventory and the wider catalogue.

---

## Contents

- [Quick start (Docker)](#quick-start-docker)
- [Running without Docker](#running-without-docker)
- [CLI](#cli)
- [Project layout](#project-layout)
- [How one ingest works](#how-one-ingest-works)
- [Configuring sources](#configuring-sources)
- [Adding a source](#adding-a-source)
- [Configuration (environment)](#configuration-environment)
- [Nepali-specific handling](#nepali-specific-handling)
- [Scraping rules](#scraping-rules)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Quick start (Docker)

```bash
cp .env.example .env        # optional; every value has a default
docker compose up -d        # postgres → schema migration → worker
docker compose logs -f worker
```

That is the whole deployment. `up` starts three services:

| Service | Role |
|---|---|
| `postgres` | Postgres 16, data in the `pgdata` volume, port bound to `127.0.0.1` only |
| `migrate` | runs `db upgrade` once and exits |
| `worker` | the continuous scheduler; starts only after `migrate` succeeds, restarts on failure |

`config/` is mounted read-only into the containers, so an added or corrected
outlet takes effect on the worker's next cycle with no rebuild. Rebuild only
when code or dependencies change:

```bash
docker compose up -d --build
```

One-off commands go through the `app` service, which `up` never starts:

```bash
docker compose run --rm app python -m app.main sources
docker compose run --rm app python -m app.main probe annapurna-post
docker compose run --rm app python -m app.main ingest --priority 1
docker compose run --rm app python -m app.main bipad --since 2026-08-01 --out data/incidents.csv
```

Files written under `data/` land in `./data` on the host.

Tests run in their own image and need no database:

```bash
docker compose run --rm test
```

Inspect the database:

```bash
docker compose exec postgres psql -U news -d news_sentiment -c \
  "select source_id, count(*), max(published_at) from articles group by 1;"
```

Stop everything (data is kept in the volume): `docker compose down`.

---

## Running without Docker

Requires Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows;  source .venv/bin/activate elsewhere
pip install -r requirements-dev.txt

python -m app.main probe annapurna-post
pytest
```

`sources`, `probe`, `bipad` and `pytest` need no database. `ingest`, `worker`
and `db upgrade` need Postgres — start just that with
`docker compose up -d postgres` and set `DATABASE_URL` (see `.env.example`).

---

## CLI

| Command | Needs Postgres | What it does |
|---|:---:|---|
| `python -m app.main sources` | no | list and validate every configured source |
| `python -m app.main probe <id>` | no | fetch and parse one source, print results, store nothing |
| `python -m app.main probe <id> --full --limit 1` | no | same, with the full article body |
| `python -m app.main ingest --source <id>` | yes | ingest one source |
| `python -m app.main ingest --priority <1-4>` | yes | ingest a whole priority tier |
| `python -m app.main ingest --due` | yes | ingest only sources past their `next_run_at` |
| `python -m app.main worker` | yes | continuous scheduler |
| `python -m app.main db upgrade` | yes | create missing tables |
| `python -m app.main bipad --since YYYY-MM-DD [--until] [--province] [--out file.csv\|.jsonl]` | no | pull BIPAD Portal disaster incidents |
| `python scripts/new_source.py --id ...` | no | scaffold config, selector pack, test and fixture for a new source |
| `python scripts/gen_sources.py [--check]` | no | regenerate (or verify) the tables in DATA_SOURCES.md |

---

## Project layout

```
NEWS-SENTIMENT/
├── app/
│   ├── main.py              CLI entrypoint
│   ├── settings.py          environment → typed settings
│   ├── sources.py           load + validate config/sources/*.yaml
│   ├── ingestion/           everything that touches the network
│   │   ├── fetcher.py       shared HTTP client: robots.txt, per-host rate limit,
│   │   │                    retries, conditional GET, extra CA intermediates
│   │   ├── base.py          BaseScraper contract
│   │   ├── rss.py           method: rss
│   │   ├── html.py          method: html (selector-driven listing pages)
│   │   ├── registry.py      method → scraper class
│   │   └── bipad.py         BIPAD Portal disaster-incident client
│   ├── parsing/             pure functions, no network
│   │   ├── article.py       article extraction from a selector pack
│   │   ├── dates.py         Bikram Sambat / Devanagari / feed dates
│   │   └── clean.py         text cleanup
│   ├── pipeline/
│   │   ├── run.py           one source, end to end
│   │   ├── normalize.py     raw item → Article, URL canonicalisation
│   │   └── dedupe.py        url_hash + simhash
│   ├── storage/             SQLAlchemy models, session, all queries
│   ├── scheduler/worker.py  priority-driven polling loop
│   └── utils/logging.py
├── config/
│   ├── sources/             one YAML per source, named after its id
│   └── selectors/           CSS selector packs for article / listing pages
├── certs/                   public CA intermediates for sites with broken chains
├── scripts/                 new_source.py, gen_sources.py
├── tests/                   offline tests; fixtures/ holds saved payloads
├── data/                    gitignored export target
├── Dockerfile               base → test → runtime (default)
├── docker-compose.yml       postgres, migrate, worker, app (cli), test
├── .env.example             every environment variable, documented
├── pyproject.toml           dependencies + ruff/pytest config (source of truth)
├── requirements.txt         runtime deps, mirrors pyproject (test-enforced)
├── requirements-dev.txt     + pytest, ruff
└── DATA_SOURCES.md          source inventory
```

Where code goes: network access in `app/ingestion/`, text transformation in
`app/parsing/`, SQL in `app/storage/repositories.py`. `app/pipeline/` only wires
them together.

---

## How one ingest works

The orchestrator is [app/pipeline/run.py](app/pipeline/run.py).

1. **Fetch the feed or listing** with `If-None-Match` / `If-Modified-Since`. A
   `304` stops the run with zero further requests.
2. **Dedupe within the batch** — the same story listed twice keeps one entry.
3. **Drop what is already stored** — one query by `url_hash`.
4. **Fetch article bodies** for the survivors only, and apply the selector pack.
5. **Normalise** — canonical URL and `url_hash`, resolved `published_at`,
   language from config, simhash.
6. **Near-duplicate check** against recent simhashes, which catches the same
   agency copy on another outlet.
7. **Upsert** — `INSERT … ON CONFLICT (url_hash) DO NOTHING`.
8. **Record state** — new ETag, `next_run_at`, and a `fetch_log` row. On
   failure the interval doubles per consecutive failure, capped at 6 h.

Steps 2–3 run before step 4 on purpose: a 15-minute poll usually finds one or
two new stories out of twenty, so filtering first keeps the load on publishers
low. Steps 1–6 need no database, which is exactly what `probe` runs.

Ingest is idempotent: running the same source twice stores nothing the second
time. One source failing never stops a cycle — the error is logged with the
source id and recorded in `fetch_log`.

### Stored tables

| Table | Holds |
|---|---|
| `articles` | `source_id`, canonical `url`, unique `url_hash`, `title`, `body`, `summary`, `author`, `lang`, `category`, `published_at`, `published_estimated`, `fetched_at`, `image_url`, `simhash` |
| `source_state` | per-source ETag / Last-Modified, `last_run_at`, `next_run_at`, consecutive failures, last error |
| `fetch_log` | one row per run: items seen / new / duplicate, not-modified, ok, error |

---

## Configuring sources

Every source is one file in `config/sources/`, named after its `id`.

```yaml
id: annapurna-post
name: Annapurna Post
url: https://annapurnapost.com/rss/     # the post-redirect URL
homepage: https://annapurnapost.com
method: rss                             # rss | html
lang: ne                                # overrides whatever the feed claims
category: news
priority: 1
active: true
selectors: annapurna-post               # → config/selectors/annapurna-post.yaml
notes: |
  - quirks hit while wiring this up
```

| Field | Required | Notes |
|---|:---:|---|
| `id` | yes | must equal the filename; changing it orphans stored articles |
| `name` | yes | display name |
| `url` | yes | absolute, post-redirect |
| `method` | yes | `rss` or `html` |
| `lang` | yes | `ne` or `en` |
| `category` | yes | `news`, `economic`, `govt`, `disaster`, `social`, `sports`, `entertainment` |
| `priority` | yes | 1 = 15 min, 2 = 30 min, 3 = 60 min, 4 = 6 h |
| `active` | yes | `false` keeps it documented but unscheduled |
| `inactive_reason` | if inactive | validation fails without it |
| `selectors` | html; optional for rss | names a file in `config/selectors/` |
| `homepage` | no | site root |
| `rate_limit` | no | seconds between requests; overrides `PER_HOST_DELAY` |
| `notes` | no | quirks worth keeping |

### Selector packs

```yaml
listing:                                # method: html only
  item: "article.news-item"
  link: "h3 a@href"
  title: "h3 a"
  summary: "p.excerpt"
  published: "time@datetime"

article:
  body: "div.news__details p"           # all matches joined in document order
  author: "p.author__name a"
  published: "p.date span"
  published_format: bs                  # bs | iso | auto
  image: "meta[property='og:image']@content"

exclude:                                # removed before the body is assembled
  - "div.ap__pollSection"
  - "div.adalyticsblock"
```

Syntax is a CSS selector, optionally suffixed with `@attribute`. A site redesign
is a YAML change, not a code change.

`python -m app.main sources` validates every file and names the offending one.

---

## Adding a source

1. **Check the feed is real** — many `/feed` URLs return HTML or an empty
   channel with a `200`:
   ```bash
   curl -sIL https://example.com.np/feed
   curl -sL  https://example.com.np/feed | head -40
   ```
   Use the final URL after any redirect. No usable feed → `method: html`.
2. **Scaffold** the config, selector pack, test file and a saved fixture:
   ```bash
   python scripts/new_source.py --id example-np --name "Example" \
       --url https://example.com.np/feed --lang ne --priority 2
   ```
3. **Fill in the selector pack** from a real article page, saving it as
   `tests/fixtures/example-np_story.html`.
4. **Iterate** until the body is clean text and the date is real rather than
   `[ESTIMATED]`:
   ```bash
   python -m app.main probe example-np --full --limit 1
   ```
5. **Pin it with tests** in `tests/sources/test_example_np.py` against the
   saved fixtures, then `pytest tests/sources/test_example_np.py`.
6. **Ingest twice** — the second run must store `0 new`, otherwise a URL
   parameter is defeating dedupe.
7. **Regenerate the inventory**: `python scripts/gen_sources.py`.

---

## Configuration (environment)

All variables are optional; see [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://news:news@localhost:5433/news_sentiment` | set automatically in Docker |
| `USER_AGENT` | `NepalNewsSentiment/0.1` | identify honestly, with a contact URL |
| `REQUEST_TIMEOUT` | `20` | seconds |
| `PER_HOST_DELAY` | `1.0` | seconds between requests to one host |
| `MAX_RETRIES` | `3` | retries on 429 / 5xx |
| `RESPECT_ROBOTS` | `true` | honour robots.txt |
| `FETCH_BODIES` | `true` | fetch full article pages |
| `MAX_BODIES_PER_RUN` | `25` | body fetches per source per cycle |
| `INGEST_CONCURRENCY` | `4` | sources polled in parallel |
| `LOG_LEVEL` | `INFO` | |
| `SOURCES_DIR` / `SELECTORS_DIR` / `CA_CERTS_DIR` | `config/sources`, `config/selectors`, `certs` | path overrides |

Compose-only: `COMPOSE_PROJECT_NAME`, `POSTGRES_PORT`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`. Change the password outside a local machine.

---

## Nepali-specific handling

- **Bikram Sambat dates** in Devanagari (`भदौ ३, २०८३ बुधबार १४:२९:५३`) are
  converted to Gregorian in [app/parsing/dates.py](app/parsing/dates.py).
- **Timezone** is `Asia/Kathmandu`, **UTC+05:45** — never +05:30.
- **Unreliable feed dates.** `published_at` resolves in order: the feed date if
  plausible (not future-dated, not before 2000) → the article page date → fetch
  time with `published_estimated = true`. Exclude estimated rows from any
  time-series that needs precision.
- **Feed language tags lie** — `lang` in config always wins.
- **Incomplete TLS chains** on some government sites are completed with public
  intermediates from [certs/](certs/README.md); verification is never disabled.
- **Client-rendered articles** — eKantipur provincial pages only serve the lede
  server-side, so their `body` is the lede, not the full article.

---

## Scraping rules

- Honour robots.txt; a disallowed path is skipped, not worked around.
- Identify honestly in the User-Agent.
- At most ~1 request/sec per host; never parallel requests to one host.
- Always send conditional headers; back off on 429/503.
- Public pages only — no login walls, no paywall circumvention.
- Scrapers return items and never touch the database; they use the injected
  `Fetcher`, never their own HTTP client, and never `sleep()`.
- Secrets live in `.env` only, never in a source YAML.

---

## Testing

```bash
pytest                                        # everything, offline
pytest tests/sources/test_annapurna_post.py   # one source
pytest -k date -v
ruff check .
python scripts/gen_sources.py --check         # DATA_SOURCES.md is current
```

Use `pytest`, not `python tests/...`. Tests never hit the network — every parser
test runs against a saved payload in `tests/fixtures/`. When a site changes:
refresh the fixture, update the selector pack, and confirm the test fails before
the fix and passes after.

`tests/test_packaging.py` fails if `requirements*.txt` and `pyproject.toml`
drift, and `tests/test_fetcher_tls.py` fails 30 days before a bundled
certificate expires.

---

## Troubleshooting

| Symptom | Likely cause | Look at |
|---|---|---|
| `probe` shows 0 items | the "feed" is an HTML page | `curl -sL <url> \| head -40`; maybe `method: html` |
| Everything `[ESTIMATED]` | no feed date and no `published` selector | the article page's date element |
| Date is the wrong day | BS read as Gregorian or vice versa | `published_format` |
| Body contains ads / related links | those blocks sit inside the body container | grow `exclude` |
| Body is empty | wrong `body` selector, or JS-rendered page | search the fixture for a body phrase |
| Second `ingest` still reports new | a varying URL parameter | `TRACKING_PARAMS` in `app/pipeline/normalize.py` |
| `unable to get local issuer certificate` | server omits its intermediate | [certs/README.md](certs/README.md) |
| `429` from a host | polling too hard | raise `rate_limit` in the source config |
| `sources` fails | malformed YAML | the error names the file |
| `worker` exits immediately in Docker | `migrate` failed | `docker compose logs migrate` |
