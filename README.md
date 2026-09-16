# Nepal News Sentiment

Scrapes Nepali and English news outlets and government portals on a schedule,
normalises every item into one article schema, deduplicates syndicated copy,
stores the corpus in MySQL, and gives every article keywords and an A–F
criticality grade.

```
fetch → parse → fetch body → normalise → dedupe → store → keywords + grade → stories
```

**82 sources are configured** — RSS feeds and HTML listing pages: national,
provincial and government outlets, plus 21 dedicated economy and business
sections (see [Section sources](#section-sources)). [DATA_SOURCES.md](DATA_SOURCES.md) has the
generated inventory and the wider catalogue.

---

## Contents

- [Quick start (Docker)](#quick-start-docker)
- [Running without Docker](#running-without-docker)
- [CLI](#cli)
- [Project layout](#project-layout)
- [How one ingest works](#how-one-ingest-works)
- [Keywords and criticality grades](#keywords-and-criticality-grades)
- [Story clusters](#story-clusters)
- [HTTP API](#http-api)
- [Configuring sources](#configuring-sources)
- [Adding a source](#adding-a-source)
- [Configuration (environment)](#configuration-environment)
- [Nepali-specific handling](#nepali-specific-handling)
- [Scraping rules](#scraping-rules)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)

---

## Quick start (Docker)

```bash
cp .env.example .env        # your MySQL credentials go in here
docker compose up -d        # one container; nothing else to run
docker compose logs -f worker
```

That is the whole deployment: two small containers against **your own MySQL 8
server** — `worker`, which fills the corpus, and `api`, which serves it
(see [HTTP API](#http-api)). The worker, on start,

1. waits for the database, retrying while it is unreachable rather than dying;
2. creates any missing tables, counting terms for articles already stored;
3. grades and groups whatever is pending;
4. polls sources on schedule, repeating step 3 after each cycle.

So there is no migration step and nothing to run by hand after a deploy or an
upgrade. There is no database container either — the server is yours, given by
`MYSQL_*` in `.env`. Create the database once (the app creates its own tables):

```sql
CREATE DATABASE news_sentiment CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

For development, an opt-in throwaway server is available:
`docker compose --profile local-db up -d mysql`.

| Want to change | Set in `.env` |
|---|---|
| Database credentials | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB` |
| TLS to the database | `MYSQL_SSLMODE=require`, or `verify-full` (+ `MYSQL_SSLROOTCERT` for a private CA) |
| Memory / CPU ceiling | `WORKER_MEMORY` (512m), `WORKER_CPUS` (1.0), `API_MEMORY`, `API_CPUS` |
| Health tolerance | `HEALTH_MAX_AGE` seconds (1800) |
| API access | `API_BIND` (127.0.0.1), `API_PORT` (8000), `API_KEYS` (optional) |

**In Portainer.** The container reports health from a heartbeat the worker
writes *only after work succeeds*, so one that cannot reach the database turns
red instead of looking idle. Logs are capped at 10 MB × 3 files, memory and CPU
are limited, and `docker stop` gets two minutes to let a cycle finish.

**Image.** Alpine, with the dependencies in a virtualenv copied out of a build
stage — no pip, no build cache, no compiler in what runs. The MySQL driver is
pure Python (PyMySQL, 0.15 MB), and `cryptography` is left out (~16 MB with its
dependencies): MySQL 8's `caching_sha2_password` needs it only on an
**unencrypted** connection, so connect with `MYSQL_SSLMODE=require` or better.

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
docker compose run --rm app python -m app.main analyse --missing
docker compose run --rm app python -m app.main stories --hours 24
```

Files written under `data/` land in `./data` on the host.

Tests run in their own image and need no database:

```bash
docker compose run --rm test
```

Inspect the corpus on your own server:

```bash
mysql -h db.example.internal -u news -p news_sentiment -e \
  "select source_id, count(*), max(published_at) from articles group by 1;"
```

Stop the worker: `docker compose down`. Nothing is lost — everything lives on
your MySQL server.

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

`sources`, `probe`, `bipad`, `analyse --text`/`--file` and `pytest` need no
database. `ingest`, `worker`, `db upgrade`, `cluster`, `stories` and
`analyse --missing`/`--all` need MySQL: set `MYSQL_*` (or `DATABASE_URL`) as in
`.env.example`, pointing either at your server or at a local one started with
`docker compose --profile local-db up -d mysql`.

---

## CLI

| Command | Needs MySQL | What it does |
|---|:---:|---|
| `python -m app.main sources` | no | list and validate every configured source |
| `python -m app.main probe <id>` | no | fetch and parse one source, print results, store nothing |
| `python -m app.main probe <id> --full --limit 1` | no | same, with the full article body |
| `python -m app.main ingest --source <id>` | yes | ingest one source |
| `python -m app.main ingest --priority <1-4>` | yes | ingest a whole priority tier |
| `python -m app.main ingest --due` | yes | ingest only sources past their `next_run_at` |
| `python -m app.main worker` | yes | the service: wait for the database, create tables, catch up, then poll on schedule |
| `python -m app.main health` | no | exit 0 if the worker's heartbeat is recent (the container healthcheck) |
| `python -m app.main api [--host H] [--port P]` | yes | serve the read-only HTTP API |
| `python -m app.main db upgrade` | yes | create missing tables (the worker does this itself) |
| `python -m app.main bipad --since YYYY-MM-DD [--until] [--province] [--out file.csv\|.jsonl]` | no | pull BIPAD Portal disaster incidents |
| `python -m app.main analyse --file story.txt` (or `--text "..."`) | no | keywords and A–F grade for any text; the first line is the title |
| `python -m app.main analyse --missing [--limit N]` | yes | grade stored articles that have no grade yet |
| `python -m app.main analyse --all` | yes | re-grade every stored article, e.g. after editing the lexicon |
| `python -m app.main stories [--hours 24] [--min-sources N]` | yes | recent stories, one row per event, most outlets first |
| `python -m app.main cluster [--limit N]` | yes | group articles not yet in a story (the worker does this after each cycle) |
| `python -m app.main cluster --rebuild-terms` | yes | recount term statistics over every stored article first |
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
│   ├── api/                 read-only HTTP API (Starlette + uvicorn)
│   ├── nlp/                 keywords + criticality grade; pure Python, offline
│   │   ├── nepali.py        rule-based Nepali tagging and case-marker stemming
│   │   ├── english.py       English stopwords
│   │   ├── tokens.py        mixed-script tokenizer
│   │   ├── keywords.py      noun-phrase keywords
│   │   ├── criticality.py   lexicon-weighted A–F grade
│   │   ├── analysis.py      both, for one article
│   │   └── stories.py       group articles about one event (TF-IDF cosine)
│   ├── pipeline/
│   │   ├── run.py           one source, end to end
│   │   ├── normalize.py     raw item → Article, URL canonicalisation
│   │   ├── dedupe.py        url_hash + simhash
│   │   ├── cluster.py       post-ingest story grouping
│   │   └── grading.py       backfill: keywords + grades for ungraded articles
│   ├── storage/             SQLAlchemy models, session, all queries
│   ├── scheduler/
│   │   ├── worker.py        the service: wait, upgrade, catch up, poll
│   │   └── health.py        heartbeat behind the container healthcheck
│   └── utils/logging.py
├── config/
│   ├── sources/             one YAML per source, named after its id
│   ├── selectors/           CSS selector packs for article / listing pages
│   └── criticality.yaml     terms, weights and A–F thresholds
├── certs/                   public CA intermediates for sites with broken chains
├── scripts/                 new_source.py, gen_sources.py
├── tests/                   offline tests; fixtures/ holds saved payloads
├── data/                    gitignored export target
├── Dockerfile               Alpine; build → test → runtime (default)
├── docker-compose.yml       worker; app (cli), test and local-db profiles
├── .env.example             the few settings you must provide
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
7. **Upsert, then analyse** — `INSERT … ON CONFLICT (url_hash) DO NOTHING`;
   each newly inserted article gets keywords and a criticality grade.
8. **Record state** — new ETag, `next_run_at`, and a `fetch_log` row. On
   failure the interval doubles per consecutive failure, capped at 6 h.

Steps 2–3 run before step 4 on purpose: a 15-minute poll usually finds one or
two new stories out of twenty, so filtering first keeps the load on publishers
low. Steps 1–6 need no database, which is exactly what `probe` runs.

Ingest is idempotent: running the same source twice stores nothing the second
time. One source failing never stops a cycle — the error is logged with the
source id and recorded in `fetch_log`.

After a cycle stores new articles, the worker groups them into stories, so one
event reported by several outlets counts once — see
[Story clusters](#story-clusters).

### Stored tables

| Table | Holds |
|---|---|
| `articles` | `source_id`, canonical `url`, unique `url_hash`, `title`, `body`, `summary`, `author`, `lang`, `category`, `published_at`, `published_estimated`, `fetched_at`, `image_url`, `simhash` |
| `source_state` | per-source ETag / Last-Modified, `last_run_at`, `next_run_at`, consecutive failures, last error |
| `fetch_log` | one row per run: items seen / new / duplicate, not-modified, ok, error |
| `article_analysis` | per article: `grade` (A–F), `score`, `dampened`, `keywords` and matched terms (JSON), `analysed_at` |
| `story_clusters` | one row per event: `title` (first headline), `article_count`, `source_count`, `sources`, first/last published |
| `article_clusters` | which story each article is in, its `similarity` to its best match, and the `terms` it matched on |
| `article_terms` | one row per (article, term): the indexed lookup behind story candidates |
| `term_stats` | document frequency per term over every stored article, for IDF |

---

## Keywords and criticality grades

Every article that `ingest` or the worker stores gets keywords and a
criticality grade in `article_analysis`. It is plain Python — no model
download, no API, no network.

### Keywords

The method follows
[Hindi POS tagging and keyword extraction](https://github.com/pemagrg1/Hindi-POS-Tagging-and-Keyword-Extraction):
tag each word, then take runs of nouns (`NP:{<NN.*>}`) as keywords. Adapted
for Nepali:

| Hindi approach | Here |
|---|---|
| NLTK TnT tagger trained on the tagged `hindi.pos` corpus | Rule-based tagging, since NLTK has no tagged Nepali corpus: closed lists of function words and verb forms, plus verb endings ([app/nlp/nepali.py](app/nlp/nepali.py)) |
| Postpositions are separate words (के, ने) | Nepali attaches them (नेपालका, मन्त्रालयमा), so they are stripped to a stem — and a stripped marker ends the phrase |
| Google Translate for unknown words | Not used; unknown words count as noun-like |

```
गृह मन्त्रालय र काठमाडौं विश्वविद्यालयको मानसिक स्वास्थ्य विभागबीच ...
→ गृह मन्त्रालय · काठमाडौं विश्वविद्यालय · मानसिक स्वास्थ्य विभाग
```

Phrases are capped at four words and ranked by count × length, doubled when
the phrase is in the title. Language is decided per word, so English articles
and English names inside Nepali stories work too.

### Grades

| Grade | Score ≥ | Typically |
|:--:|--:|---|
| **A** | 15 | deaths, disasters, explosions, curfews |
| **B** | 9 | injuries, arrests, violent protests, serious accidents |
| **C** | 5 | disputes, shortages, resignations, several lesser signals |
| **D** | 2.5 | a single warning, dispute or legal action |
| **E** | 1 | routine governance: decisions, budgets, elections |
| **F** | 0 | nothing critical |

The score comes from [config/criticality.yaml](config/criticality.yaml), which
sorts terms into four tiers (critical 5, high 3, medium 1.5, low 0.5). Each
matched term scores weight × mentions, capped at 3 mentions, with a title
mention counting double. A story about prevention, awareness or training
(न्यूनीकरण, सचेतना, तालिम, MoU …) has its score halved and its grade capped at C,
so a suicide-prevention agreement does not grade like a suicide.

A term matches a whole word or its stem (`मृत्यु` matches मृत्युको), and a
trailing `*` makes it a prefix (`बाढी*` matches बाढीपीडित). `बम` never fires on
बमोजिम, and each word counts once, for its most severe term. Lexicon edits apply
on the worker's next cycle; re-grade what is already stored with
`analyse --all`.

```bash
python -m app.main analyse --file story.txt     # first line is the title
python -m app.main analyse --missing            # stored articles with no grade yet
```

```sql
select a.published_at, a.title, x.grade, x.score, x.keywords
from articles a join article_analysis x on x.article_id = a.id
where x.grade in ('A', 'B')
order by a.published_at desc
limit 20;
```

**Limits.** This is weighted keyword matching, not language understanding:
"no casualties" still matches *casualties*, and a word missing from the lexicon
scores nothing. eKantipur provincial pages only serve a lede, so their grades
rest on less text. `probe` prints each item's grade and keywords, which is the
quickest way to tune the lexicon against a live outlet.

---

## Story clusters

Deduplication stops the same *text* being stored twice: `url_hash` for the same
URL, simhash for near-copies. It cannot see the same *event* written up by
several outlets in their own words — five outlets on one NEPSE jump measured
22–36 simhash bits apart, far past its threshold of 8 — so each was stored and
counted as a separate event. After every worker cycle (and after `ingest`), new
articles are therefore grouped into **stories**:

1. **Terms** — the noun-like word stems of title and body, title counted twice,
   from the same Nepali/English tagger as keywords.
2. **Weights** — TF-IDF. Filler every finance story shares (प्रतिशत, करोड,
   कारोबार) counts for little; words that identify the event (नेप्से, घरजग्गा,
   कार्ययोजना) count for a lot.
3. **Join** — an article joins the story of its most similar article published
   within 48 hours either side, in the same language, when their cosine
   similarity is **at least 0.25**. Otherwise it starts a new story.

Term statistics (`term_stats`) cover **every stored article** and are counted
when an article is stored, not when it is clustered. Counted at clustering
time, the first stories after a deploy would see IDF from a handful of articles,
unable to tell filler from event words. `db upgrade` counts the articles already
in an existing database when it creates the table.

Only articles sharing one of the new article's 12 most distinctive terms are
compared — through the indexed `article_terms` table, since MySQL cannot index
inside a JSON column — so each assignment stays cheap as the corpus grows. A
MySQL named lock (`GET_LOCK`) keeps it to one clusterer at a time, so two
outlets' versions of an event arriving together cannot each found a story.

**Measured on the 34 saved article pages** (`tests/test_stories.py`), this
forms exactly the three real multi-outlet stories — six Nepali outlets on the
15 September NEPSE jump, two English outlets on the same jump, and two outlets
on the same central-bank real-estate figures — and nothing else. Joins scored
0.30–0.48. The closest articles that did not join scored 0.23 (a capital-gains
tax cut against the market's reaction — both from the same reform plan, but
different stories), 0.20 (the plan's announcement against that reaction) and
0.18. Plain keyword overlap could not separate the two groups.

```bash
python -m app.main stories --hours 24                  # one row per event, most outlets first
python -m app.main stories --hours 24 --min-sources 3  # only widely covered events
python -m app.main cluster                             # group anything left over
```

```sql
select title, source_count, article_count, sources
from story_clusters
where last_published_at > now() - interval '24 hours'
order by source_count desc
limit 20;
```

**Upgrading an existing database:** nothing to run. On start the worker creates
the tables, counts the terms of articles already stored, and groups them before
its first cycle.

**Limits.**
- The margin is narrow: the closest non-join sits 0.02 below 0.25 and the
  weakest join 0.05 above it, on a few dozen pages.
  Revisit `JOIN_THRESHOLD` in `app/nlp/stories.py` once real volume builds up;
  `tests/test_stories.py` shows what a change does.
- An article joins through its single best match, so a long-running story can
  drift across 48-hour steps.
- Nepali and English coverage of the same event are separate stories.
- Copies dropped by simhash at ingest are not stored, so `source_count` counts
  outlets whose version was kept.
- eKantipur's lede-only bodies give it fewer terms to match on.

---

## HTTP API

A read-only API over the stored corpus, so other systems can consume the
articles, their criticality grades and their sources without touching the
database. It runs as its own container (`api`) from the same image.

```bash
curl "http://127.0.0.1:8000/api/v1/articles?grade=A,B&limit=20"
# with API_KEYS set:
curl -H "X-API-Key: $API_KEY" "http://127.0.0.1:8000/api/v1/articles?grade=A,B"
```

| Endpoint | Returns |
|---|---|
| `GET /api/v1/articles` | newest first, cursor-paged; filter by `grade`, `source`, `lang`, `category`, `since`, `until`; `?body=true` includes full text |
| `GET /api/v1/articles/{id}` | one article, with full text, keywords and grade |
| `GET /api/v1/stories` | one row per event: `?hours=`, `?min_sources=`; outlets that covered it, and its most critical grade |
| `GET /api/v1/sources` | the configured outlets, straight from the YAML — no database needed |
| `GET /api/v1/stats` | counts by grade and by outlet for a window |
| `GET /healthz` | liveness and database reachability; no key, used by the container healthcheck |

Each article carries its grade inline:

```json
{
  "id": 4412,
  "source_id": "kathmandupost-economy",
  "url": "https://kathmandupost.com/money/2026/09/15/...",
  "published_at": "2026-09-15T09:41:11+00:00",
  "criticality": {
    "grade": "B",
    "score": 9.5,
    "dampened": false,
    "keywords": [{"text": "capital market", "score": 6.0, "count": 4}],
    "matched_terms": [{"term": "flood*", "tier": "critical", "count": 2, "points": 10.0}]
  },
  "story_id": 87
}
```

**Authentication is opt-in.** With `API_KEYS` empty — the default — the API is
open to anyone who can reach the port, which is the intent on a trusted
network: the corpus is public reporting and every endpoint is read-only. Set
one or more comma-separated keys to require an `X-API-Key` header on every path
except `/healthz`, which the container healthcheck calls and which is never
keyed.

**Paging.** Responses carry `next_cursor`; pass it back as `?before=` for the
next page. `null` means the last page. Paging by id rather than offset keeps a
cursor correct while new articles are being written.

**Times** are UTC ISO-8601 both ways. Nepal is +05:45, so convert on your side;
the API never guesses a zone. `published_estimated: true` marks articles whose
publish time is a fallback — exclude those from precise time series.

**Exposure.** `API_BIND` chooses the host interface the port is published on:
`127.0.0.1` (default) keeps it on the machine, and `0.0.0.0` publishes it on
every interface, so anything on the LAN reaches it at `http://<host-ip>:8000`.

With no keys configured, reaching the port is the only requirement, so:

- keep the port off the public internet (firewall, and no router port-forward) —
  that boundary is what stands in for authentication;
- set `API_KEYS` before exposing it any wider, giving each consumer its own key
  so one can be rotated out without downtime;
- put a reverse proxy with TLS in front if traffic leaves the trusted network;
- list browser origins in `API_CORS_ORIGINS`; server-to-server callers need
  nothing.

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
| `section_of` | no | id of the outlet whose site this is a section of, e.g. its economy page |

### Section sources

An outlet's economy or business page is configured as its own source, with its
own category and `section_of` pointing at the main outlet:

```yaml
id: onlinekhabar_english-economy
url: https://english.onlinekhabar.com/category/economy/feed
category: economic
section_of: onlinekhabar_english
```

The same finance story usually appears in the main feed too, and articles are
stored once per URL. So when a section source lists a story that is already
stored as `news`, it relabels it with its own category — whichever feed runs
first, finance stories end up `economic`. Only `news` rows move; a `govt` or
`disaster` label is never overwritten.

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

Only the MySQL settings are required — [.env.example](.env.example) holds those
and nothing else. Everything below has a working default, and this table is the
full list.

| Variable | Default | Purpose |
|---|---|---|
| `MYSQL_HOST` / `MYSQL_PORT` | `localhost`, `3306` | your MySQL server |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB` | `news`, `news`, `news_sentiment` | credentials; each is URL-escaped, so `@ / : #` in a password are safe |
| `MYSQL_SSLMODE` | unset (no TLS) | `disable`, `require` (encrypt only), `verify-ca`, `verify-full`. Needed for password login unless you add `cryptography` |
| `MYSQL_SSLROOTCERT` | certifi's roots | CA bundle for the `verify-*` modes |
| `MYSQL_DRIVER` | `pymysql` | another SQLAlchemy MySQL driver, if you add it to the image |
| `DATABASE_URL` | built from the parts above | a complete URL, escaped by you; overrides them |
| `HEARTBEAT_FILE` | system temp dir | file the worker touches after work succeeds |
| `HEALTH_MAX_AGE` | `1800` | heartbeat age, in seconds, at which `health` reports unhealthy |
| `API_KEYS` | — | comma-separated keys for the `X-API-Key` header; empty means no key is needed |
| `API_BIND` / `API_PORT` | `127.0.0.1`, `8000` | where the API port is published |
| `API_MAX_LIMIT` | `200` | largest page a caller may request |
| `API_CORS_ORIGINS` | — | browser origins allowed to call the API; empty means server-to-server only |
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
| `CRITICALITY_FILE` | `config/criticality.yaml` | criticality lexicon |

Compose-only: `COMPOSE_PROJECT_NAME`, `WORKER_MEMORY` and `WORKER_CPUS` (the
container's ceilings). `MYSQL_PORT` also publishes the opt-in `local-db`
database on `127.0.0.1`.

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
| `worker` shows unhealthy in Portainer | the database is unreachable, or every cycle is failing | `docker compose logs worker`; the heartbeat only advances after work succeeds |
| `database not reachable` repeating in the logs | wrong `MYSQL_*` values, or the server is unreachable | fix `.env`; check the server allows the container's IP |
| `cryptography is required for sha256_password or caching_sha2_password` | MySQL 8 password login over an unencrypted connection | set `MYSQL_SSLMODE=require`, or add `cryptography` to `requirements.txt` and rebuild |
| TLS handshake error connecting to MySQL | the server has TLS turned off (MySQL 8 enables it by default) | turn it on, or set `MYSQL_SSLMODE=disable` and add `cryptography` to `requirements.txt` |
| API unreachable from another machine | `API_BIND` is `127.0.0.1`, or the host firewall blocks the port | set `API_BIND=0.0.0.0`, `docker compose up -d api`, then allow the port |
| Devanagari stored as `?` or mojibake | the database or a column is not utf8mb4 | `CREATE DATABASE … CHARACTER SET utf8mb4`; the app's own tables are utf8mb4 already |
| A story's grade looks wrong | a term missing, too broad, or in the wrong tier | `analyse --text` on it, edit `config/criticality.yaml`, then `analyse --all` |
| Stored articles have no grade | the lexicon failed to load (logged as an error) | fix the file; the worker catches up within 30 min, or run `analyse --missing` |
| One event shows up as several stories | worded too differently (below 0.25), or published more than 48 h apart | `JOIN_THRESHOLD` / `WINDOW` in `app/nlp/stories.py`; check against `tests/test_stories.py` |
| Unrelated articles share a story | threshold too low, or term statistics missing for older articles | `cluster --rebuild-terms`, then review `JOIN_THRESHOLD` |

---

## Credits

This project builds on two open-source repositories:

- **[nlethetech/nepal-osint-skeleton](https://github.com/nlethetech/nepal-osint-skeleton)** —
  NepalOSINT, an open-source intelligence dashboard for Nepal. The source
  catalogue in [DATA_SOURCES.md](DATA_SOURCES.md) — news feeds, government and
  financial portals, and social accounts — is drawn from its
  `backend-v5/config/sources.yaml` and backend scrapers.
- **[pemagrg1/Hindi-POS-Tagging-and-Keyword-Extraction](https://github.com/pemagrg1/Hindi-POS-Tagging-and-Keyword-Extraction)** —
  Hindi POS tagging with NLTK's TnT tagger and noun-phrase keyword extraction.
  The keyword method in [app/nlp/](app/nlp/) adapts its tag-then-chunk approach
  to Nepali; see [Keywords](#keywords).
