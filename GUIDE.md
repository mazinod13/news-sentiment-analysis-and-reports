# Developer Guide — News Outlet Scraping

Scope for now: **news outlets (RSS / HTML) only**. Social, government portals and
NLP come later; the folders for them in [README.md](README.md) are the target
layout, not what exists today.

This guide covers what to build first, how one article travels through the
pipeline, and how two people add outlets in parallel without stepping on each
other.

---

## Contents

- [The one rule that makes parallel work possible](#the-one-rule-that-makes-parallel-work-possible)
- [What exists today](#what-exists-today)
- [Docker setup](#docker-setup)
- [The process flow, step by step](#the-process-flow-step-by-step)
- [Worked example: Annapurna Post](#worked-example-annapurna-post)
- [Adding your own outlet](#adding-your-own-outlet)
- [Working in parallel and merging](#working-in-parallel-and-merging)
- [Order of work](#order-of-work)
- [Reference](#reference)

---

## The one rule that makes parallel work possible

**Everything specific to an outlet lives in files named after that outlet.**

Adding an outlet creates exactly four files, all carrying its id:

```
config/sources/<id>.yaml           what it is, where it lives, how often to poll
config/selectors/<id>.yaml         how to read its article pages
tests/sources/test_<id>.py         its tests
tests/fixtures/<id>_*.xml|html     saved payloads its tests run against
```

It **modifies nothing**. No shared registry, no central `sources.yaml`, no
edit to `app/`. Two people can add ten outlets each and git will merge every
branch without a single conflict, because no two branches ever touch the same
file.

That is why sources are one-file-per-outlet instead of one big `sources.yaml`
— a shared list is a guaranteed conflict on every concurrent branch.

You only touch shared code when an outlet needs a capability that does not
exist yet (a new date format, a new `method`). Those are rare, and
[Working in parallel](#working-in-parallel-and-merging) covers how to handle
them.

---

## What exists today

Working, tested, end to end:

| Piece | Where | State |
|---|---|---|
| Source config + validation | [app/sources.py](app/sources.py) | done |
| HTTP fetcher (robots, rate limit, retries, conditional GET) | [app/ingestion/fetcher.py](app/ingestion/fetcher.py) | done |
| RSS scraper | [app/ingestion/rss.py](app/ingestion/rss.py) | done |
| HTML listing scraper | [app/ingestion/html.py](app/ingestion/html.py) | written, no outlet uses it yet |
| Article extraction from selector packs | [app/parsing/article.py](app/parsing/article.py) | done |
| Bikram Sambat / Devanagari dates | [app/parsing/dates.py](app/parsing/dates.py) | done |
| Normalisation + URL canonicalisation | [app/pipeline/normalize.py](app/pipeline/normalize.py) | done |
| Dedupe (url_hash + simhash) | [app/pipeline/dedupe.py](app/pipeline/dedupe.py) | done |
| Postgres storage | [app/storage/](app/storage/) | written; schema compiles, needs a live run |
| Scheduler | [app/scheduler/worker.py](app/scheduler/worker.py) | basic loop |
| Sentiment / entities / reports | — | not started |

One outlet is fully wired: **Annapurna Post**.

---

## Docker setup

Docker gives both of you the same Python, the same Postgres and the same
timezone, so "works on my machine" stops being a category of bug.

### First run

```bash
cp .env.example .env          # then read it -- see the isolation note below
docker compose build
docker compose up -d postgres
docker compose run --rm app python -m app.main db upgrade
```

### Everyday commands

```bash
# List and validate every configured outlet
docker compose run --rm app python -m app.main sources

# Fetch + parse one outlet, print what it found, store nothing
docker compose run --rm app python -m app.main probe annapurna-post

# Same, with full article bodies
docker compose run --rm app python -m app.main probe annapurna-post --full --limit 1

# Actually ingest into Postgres
docker compose run --rm app python -m app.main ingest --source annapurna-post

# Everything at priority 1
docker compose run --rm app python -m app.main ingest --priority 1

# Tests
docker compose run --rm app pytest

# Continuous scheduler (opt-in profile so `up` never starts scraping by surprise)
docker compose --profile worker up -d worker
docker compose logs -f worker
```

Source is bind-mounted, so **an edit on your host applies to the next `run`
with no rebuild**. Rebuild only when a dependency changes:

```bash
docker compose build app
```

### Two developers, one machine or two

`.env` is gitignored, so each of you sets your own:

```bash
COMPOSE_PROJECT_NAME=news-sentiment-mazin    # namespaces containers + volumes
POSTGRES_PORT=5433                            # your host port for psql
```

Different `COMPOSE_PROJECT_NAME` values give you independent stacks and
independent databases. Nothing is shared, so neither of you can corrupt the
other's data while testing.

### Inspecting the database

```bash
docker compose exec postgres psql -U news -d news_sentiment -c \
  "select source_id, count(*), max(published_at) from articles group by 1;"
```

### Without Docker

The scraping half needs no database:

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e . --no-deps
python -m app.main probe annapurna-post
pytest
```

`probe` and `pytest` work fully offline-of-Postgres. Only `ingest`, `worker`
and `db upgrade` need it.

---

## The process flow, step by step

One `ingest` run of one outlet, in order. The orchestrator is
[app/pipeline/run.py](app/pipeline/run.py) — read it alongside this.

```
config/sources/annapurna-post.yaml
        │
        │  load + validate                                    app/sources.py
        ▼
  ┌───────────┐   registry picks the scraper for `method`     app/ingestion/registry.py
  │  Source   │──────────────────────────────────────────────▶ RSSScraper
  └───────────┘
        │
   ①  scraper.fetch()                                         app/ingestion/rss.py
        │    GET the feed, with If-None-Match / If-Modified-Since
        │    ├── 304 Not Modified ──▶ stop here. Zero further requests.
        │    └── 200 ──▶ feedparser ──▶ list[RawItem]
        ▼
   ②  dedupe within this batch                                app/pipeline/dedupe.py
        │    same story listed twice in one feed → keep one
        ▼
   ③  drop what is already stored                             app/storage/repositories.py
        │    one query: SELECT url_hash WHERE url_hash IN (...)
        │    typically kills 18 of 20 items on a 15-minute poll
        ▼
   ④  scraper.fetch_body() for the survivors only             app/ingestion/rss.py
        │    GET each article page, apply the selector pack    app/parsing/article.py
        │    → full body, author, image, publish date
        ▼
   ⑤  normalize()                                             app/pipeline/normalize.py
        │    canonical URL → url_hash
        │    resolve published_at (see the ladder below)
        │    force lang from config, compute simhash
        ▼
   ⑥  near-duplicate check                                    app/pipeline/dedupe.py
        │    simhash vs everything from the last 48h
        │    catches the same agency copy on another outlet
        ▼
   ⑦  upsert                                                  app/storage/repositories.py
        │    INSERT ... ON CONFLICT (url_hash) DO NOTHING
        ▼
   ⑧  record state + fetch log
             new etag, next_run_at = now + interval
             on failure: interval × 2^failures, capped at 6h
```

**Why the order matters.** Steps ② and ③ come *before* step ④ on purpose. A
priority-1 outlet is polled every 15 minutes and usually has 1–2 new stories
out of 20. Filtering first means 2 article fetches per cycle instead of 20 —
a 10× reduction in load on someone else's server, which is the difference
between being a good citizen and getting blocked.

Steps ①–⑥ need no database at all. That is exactly what `probe` runs, which is
why you can develop an entire outlet before Postgres is even up.

### How `published_at` is resolved

Feed dates are the single most unreliable field in Nepali news scraping. The
ladder, first hit wins:

1. the feed's `<pubDate>`, **unless** it fails the plausibility check
   (future-dated, or before 2000 — feeds that stamp every item with `now` are
   common and must not be trusted)
2. the article page, via the selector pack's `published` — ISO, or Bikram
   Sambat in Devanagari
3. `fetched_at`, with **`published_estimated = true`** stored alongside

Step 3 is a fallback, not a lie: the flag is a column so reports can exclude
estimated timestamps from any time-series that needs real precision. Never
invent a date and leave it unflagged.

---

## Worked example: Annapurna Post

This is the reference outlet. It is worth understanding because almost every
awkward thing you will hit elsewhere shows up here.

### What the feed actually gives you

```bash
curl -sL https://annapurnapost.com/rss | head -20
```

```xml
<rss version="2.0"><channel>
  <title>Annapurna Post</title>
  <language>en-us</language>
  <item>
    <title>टाटा कार्निभलको चौथो संस्करण सुरु हुँदै, यस्ता छन् आर्कषक अफरहरु</title>
    <link>http://annapurnapost.com/story/505752</link>
    <guid>http://annapurnapost.com/story/505752</guid>
    <description>काठमाडौं : नेपालका लागी टाटा मोटर्सको ... (cut at ~500 chars)</description>
  </item>
```

Five problems in one feed:

| # | What you see | Why it matters | Handled by |
|---|---|---|---|
| 1 | `/rss` returns `301` to `/rss/` | a client that does not follow redirects gets nothing | `follow_redirects=True`, and the config points at `/rss/` directly |
| 2 | **no `<pubDate>` on any item** | every article would be undated | body fetch + BS date parse, see below |
| 3 | `<language>en-us</language>` but the content is Nepali | an English sentiment model on Devanagari returns garbage | `lang: ne` in the config always wins over the feed |
| 4 | `<link>` is `http://`, the site serves `https://` | the same story stored twice | `canonical_url()` upgrades the scheme |
| 5 | `<description>` truncated at ~500 chars | not enough text to analyse | fetch the article page for the full body |

### Where the date really lives

There is no `article:published_time` meta tag and no JSON-LD. The only publish
time on the page is rendered for humans, in Bikram Sambat, in Devanagari
numerals:

```html
<p class="date">
  <span>भदौ ३, २०८३ बुधबार १४:२९:५३</span>
</p>
```

That is Bhadau 3, 2083 BS → **2026-08-19**, 14:29:53 Nepal time. Getting there
takes three conversions, all in [app/parsing/dates.py](app/parsing/dates.py):

```
भदौ ३, २०८३ ... १४:२९:५३
   │
   ├── Devanagari numerals → ASCII        "भदौ 3, 2083 ... 14:29:53"
   ├── month name → number                 भदौ → 5
   ├── BS → Gregorian                      2083-05-03 → 2026-08-19
   └── attach Asia/Kathmandu (+05:45)      2026-08-19T14:29:53+05:45
```

Nepal is **UTC+05:45**, not +05:30. Getting this wrong shifts every article by
15 minutes and silently corrupts any hourly trend.

### The config that encodes all of it

[config/sources/annapurna-post.yaml](config/sources/annapurna-post.yaml) — what
the outlet is:

```yaml
id: annapurna-post
url: https://annapurnapost.com/rss/     # trailing slash: /rss 301s here
method: rss
lang: ne                                # not what the feed claims
priority: 1
selectors: annapurna-post
```

[config/selectors/annapurna-post.yaml](config/selectors/annapurna-post.yaml) —
how to read an article page:

```yaml
article:
  body: "div.news__details p"
  author: "p.author__name a"
  published: "p.date span"
  published_format: bs          # ← this is what triggers BS parsing
  image: "meta[property='og:image']@content"

exclude:
  - "div.ap__pollSection"       # "what do you think?" widget
  - "div.adalyticsblock"        # inline ads sitting between paragraphs
  - "[campaign]"
```

The `exclude` list is not cosmetic. Those blocks sit *inside* the body
container, so without them the ad copy and poll text end up in the article text
and then in the sentiment score.

### Seeing it work

```bash
python -m app.main probe annapurna-post --limit 3
```

```
parsed 20 items source=annapurna-post method=rss url=https://annapurnapost.com/rss/

  title      टाटा कार्निभलको चौथो संस्करण सुरु हुँदै, यस्ता छन् आर्कषक अफरहरु
  url        https://annapurnapost.com/story/505752      ← https, no trailing slash
  published  2026-08-19T14:29:53+05:45                   ← from the BS string
  author     अन्नपूर्ण
  lang       ne                                          ← config, not the feed
  body       1563 chars                                  ← vs ~500 in the feed
```

`probe` prints `[ESTIMATED]` next to any timestamp that fell back to fetch
time. On a healthy outlet you should see none.

### And the tests that pin it

[tests/sources/test_annapurna_post.py](tests/sources/test_annapurna_post.py)
asserts each quirk explicitly, including this one:

```python
def test_feed_has_no_publish_dates(fixture_text):
    """The defining quirk: not one item carries a date. If this ever starts
    failing, Annapurna Post fixed their feed and the body fetch can be relaxed."""
```

A test that fails when the *upstream site improves* is a feature. It tells you
a workaround is no longer needed instead of leaving it in place forever.

---

## Adding your own outlet

Pick one from [DATA_SOURCES.md](DATA_SOURCES.md). Roughly 30 minutes for a
well-behaved RSS feed.

### Step 1 — check the feed is really a feed

Half the `/feed` URLs in Nepali news return HTML, an empty channel, or a 404
page with a 200 status.

```bash
curl -sIL https://gorkhapatraonline.com/rss | grep -iE "^(HTTP|location|content-type)"
curl -sL  https://gorkhapatraonline.com/rss | head -40
```

You want `200` and an XML content type, with real `<item>` elements. Note any
redirect — put the *final* URL in your config.

If there is no usable feed, the outlet is `method: html` and you write a
`listing:` block instead. Do an RSS outlet first.

### Step 2 — scaffold

```bash
python scripts/new_source.py \
  --id gorkhapatra --name "Gorkhapatra" \
  --url https://gorkhapatraonline.com/rss --lang ne --priority 2
```

Creates the four outlet-named files and saves the live feed as your fixture. It
tells you if the URL redirected.

### Step 3 — first probe

```bash
python -m app.main probe gorkhapatra
```

At this point the selector pack is still `TODO`, so you get titles, links and
feed summaries but no bodies. Confirm titles are not truncated, links are
absolute, and check whether `published` came through or shows `[ESTIMATED]`.

### Step 4 — write the selector pack

Open a real article page in your browser, Inspect, and find the container that
holds the article paragraphs.

```bash
# save one for reference and as the test fixture
curl -sL https://gorkhapatraonline.com/some-story > tests/fixtures/gorkhapatra_story.html
```

Fill in `config/selectors/gorkhapatra.yaml`:

```yaml
article:
  body: "div.article-content p"
  author: "span.author a"
  published: "span.published-date"
  published_format: auto      # auto tries Bikram Sambat, then ISO
  image: "meta[property='og:image']@content"

exclude:
  - "div.related-news"
  - "div.advertisement"
```

Selector syntax is plain CSS, with `@attr` to read an attribute instead of
text. `body` joins every match in document order.

**Always check `exclude`.** Print the body with `probe --full` and read the end
of it — related-story rails and ad copy love to hide inside the body container.

### Step 5 — iterate

```bash
python -m app.main probe gorkhapatra --full --limit 1
```

Loop until the body is clean article text, the author is right, and the date is
real rather than estimated.

### Step 6 — pin it with tests

Un-skip the extraction test in `tests/sources/test_gorkhapatra.py` and assert
what is actually true for *your* outlet — the real author string, a phrase from
the body, the exact date the fixture should produce, and that the excluded
junk is gone.

```bash
pytest tests/sources/test_gorkhapatra.py -v
```

Always `pytest`, never `python tests/...` -- a test file has no `__main__`, so
running it directly just executes the imports and exits without creating any of
the fixtures pytest injects.

Add a case to `tests/test_dates.py` if your outlet showed you a date format the
parser had not seen.

### Step 7 — ingest for real

```bash
docker compose run --rm app python -m app.main ingest --source gorkhapatra
docker compose run --rm app python -m app.main ingest --source gorkhapatra   # again
```

The second run must report `0 new`. If it does not, dedupe is broken for your
outlet — usually a URL that carries a session or tracking parameter that
`canonical_url()` does not strip yet.

### Step 8 — commit

```bash
git checkout -b source/gorkhapatra
git add config/sources/gorkhapatra.yaml config/selectors/gorkhapatra.yaml \
        tests/sources/test_gorkhapatra.py tests/fixtures/gorkhapatra_*
git commit -m "feat: add Gorkhapatra RSS source"
```

Check your diff: if `git status` shows anything under `app/`, read
[the next section](#working-in-parallel-and-merging) before pushing.

---

## Working in parallel and merging

### Split the work by outlet, never by layer

Agree on who takes which outlets from [DATA_SOURCES.md](DATA_SOURCES.md) and
write it down. Splitting by outlet means your files never overlap. Splitting by
layer ("you do parsing, I do storage") means you are both in `app/` all day and
every merge hurts.

A reasonable first split, taking the priority-1 outlets:

| Person A | Person B |
|---|---|
| Kathmandu Post (en, rss) | Setopati (ne, rss) |
| OnlineKhabar EN + NE (rss) | Nagarik News (ne, rss) |
| BBC Nepali (ne, rss) | Gorkhapatra (ne, rss) |
| eKantipur (ne, **html**) | Ratopati (ne, **html**) |

Branch per outlet: `source/kathmandu-post`. Small PRs, quick reviews, no
long-lived branches.

### Why the merges stay clean

Your outlet lives in four files that only you created. Git resolves that with
no conflict, every time. Verify before you push:

```bash
git diff --stat main...HEAD
```

Expected — only outlet-named files:

```
 config/selectors/gorkhapatra.yaml   |  18 ++
 config/sources/gorkhapatra.yaml     |  15 ++
 tests/fixtures/gorkhapatra_feed.xml | 210 ++++
 tests/sources/test_gorkhapatra.py   |  38 ++
```

### When you do have to touch shared code

Sometimes an outlet needs something that does not exist: a date format, a
cleaning rule, a new `method`. Then:

1. **Say so before you write it.** A message costs a minute; discovering you
   both added a date parser costs an afternoon.
2. **Split it into its own commit**, separate from the outlet — ideally its own
   PR, merged first.
3. **Add, never rewrite.** Add a pattern to `NEPALI_MONTHS`, add a branch to
   the ladder. Do not restructure a function someone else's outlet depends on.
4. **Cover it with a shared test** in `tests/test_dates.py` or
   `tests/test_pipeline.py`, so the next person cannot silently break it.

The genuinely conflict-prone files are the dependency lists (both adding a
package) and `app/ingestion/registry.py` (both adding a method). Both are tiny
and easy to resolve by hand — just expect it and re-run `pytest` after.

A dependency goes in **three** places, and they must agree:
`pyproject.toml`, `requirements.txt` (or `requirements-dev.txt` for tooling),
and then `docker compose build app`. `tests/test_packaging.py` fails if the
first two drift, so you cannot forget one silently.

### Before every push

```bash
docker compose run --rm app pytest                        # includes the dependency drift check
docker compose run --rm app python -m app.main sources    # validates all configs
python scripts/gen_sources.py --check                     # DATA_SOURCES.md is current
ruff check .
```

`sources` catches malformed config with a message naming the file, which is
worth more than any amount of YAML staring.

### Reviewing each other's outlet PRs

- Does `probe` show real dates, or is everything `[ESTIMATED]`?
- Does the body end cleanly, or does it trail into related-story links?
- Is `priority` honest? Not every outlet deserves a 15-minute poll.
- Are the fixtures saved, so the test runs offline?
- Does the second `ingest` run report `0 new`?

---

## Order of work

**Phase 1 — get one outlet perfect.** Done: Annapurna Post. Read it end to end
before writing your own; every pattern you need is in it.

**Phase 2 — breadth across RSS outlets.** The 20-odd priority 1–2 feeds in
[DATA_SOURCES.md](DATA_SOURCES.md), split between you. Mostly config, no new
code. This is where parallel work pays off.

**Phase 3 — the HTML outlets.** eKantipur, Ratopati, Nepali Times, The
Himalayan Times, Kantipur TV, My Republica. Each needs a `listing:` block as
well as an `article:` block, so they are slower — one person should do the
first one and set the pattern.

**Phase 4 — operations.** `scripts/health_check.py` to flag feeds returning
zero items, alembic migrations, backfill.

**Phase 5 — NLP and reports.** Sentiment, entities, topics, story clustering,
daily digests. Do not start this until the corpus is steady, and treat the
`published_estimated` flag as load-bearing when you do.

Deliberately not started yet: social media, government portals, embeddings.
They are in [README.md](README.md) as the target shape, nothing more.

---

## Reference

### Commands

| Command | Needs Postgres | What it does |
|---|:---:|---|
| `python -m app.main sources` | no | list + validate every outlet |
| `python -m app.main probe <id>` | no | fetch, parse, print, store nothing |
| `python -m app.main probe <id> --full --limit 1` | no | same, with full body text |
| `python -m app.main ingest --source <id>` | yes | one outlet into the database |
| `python -m app.main ingest --priority 1` | yes | a whole tier |
| `python -m app.main ingest --due` | yes | only what is past `next_run_at` |
| `python -m app.main worker` | yes | continuous scheduler |
| `python -m app.main db upgrade` | yes | create missing tables |
| `python scripts/new_source.py --id ...` | no | scaffold a new outlet |
| `python scripts/gen_sources.py` | no | refresh the DATA_SOURCES.md status block |
| `python scripts/gen_sources.py --check` | no | fail if that block is stale (CI) |
| `pytest` | no | everything, offline |

### Source config fields

| Field | Required | Notes |
|---|:---:|---|
| `id` | yes | must equal the filename; immutable once articles exist |
| `name` | yes | display name |
| `url` | yes | absolute, and the **post-redirect** URL |
| `method` | yes | `rss` or `html` |
| `lang` | yes | `ne` or `en` — overrides whatever the feed claims |
| `category` | yes | `news`, `economic`, `govt`, `disaster`, `social`, `sports`, `entertainment` |
| `priority` | yes | 1=15min, 2=30min, 3=60min, 4=6h |
| `active` | yes | `false` documents a dead feed without scheduling it |
| `inactive_reason` | if inactive | validation fails without it |
| `selectors` | html; rss optional | names a file in `config/selectors/` |
| `homepage` | no | site root |
| `rate_limit` | no | seconds between requests, overrides the default |
| `notes` | no | quirks. Write them down. |

### Selector pack fields

| Key | Notes |
|---|---|
| `article.body` | container for the paragraphs; all matches joined in order |
| `article.author` | first match |
| `article.published` | first match |
| `article.published_format` | `bs`, `iso` or `auto` |
| `article.image` | usually `meta[property='og:image']@content` |
| `exclude` | removed *before* the body is assembled — ads, polls, related rails |
| `listing.*` | `method: html` only: `item`, `link`, `title`, `summary`, `published` |

Syntax: CSS selector, optionally `selector@attribute`.

### Rules that keep the pipeline predictable

- Scrapers **return**, they never store. No database access in `app/ingestion/`.
- Never build your own HTTP client. Use the injected `Fetcher` — it owns
  robots.txt, rate limiting and retries, and bypassing it makes us a bad
  citizen on someone else's server.
- No `sleep()` in a scraper. Pacing is the fetcher's job.
- Absolute URLs and timezone-aware datetimes only.
- One outlet failing must never stop a cycle. Errors are caught per source,
  logged with the source id, and recorded in `fetch_log`.
- Ingest is idempotent. Run a cycle twice; the second changes nothing.
- Tests never hit the network — always a fixture.
- Secrets in `.env` only, never in a source YAML.

### Troubleshooting

| Symptom | Likely cause | Look at |
|---|---|---|
| `probe` shows 0 items | the "feed" is an HTML page | `curl -sL <url> \| head -40`; maybe `method: html` |
| Everything `[ESTIMATED]` | no feed date and no `published` selector | the article page's date element |
| Date parses to the wrong day | BS read as Gregorian, or vice versa | `published_format`; is it really Bikram Sambat? |
| Body includes ads / related links | those blocks live inside the body container | grow `exclude` |
| Body is empty | `body` selector wrong, or the page is JS-rendered | search the saved fixture for a body phrase |
| Garbled Devanagari | encoding guessed wrong | feeds are parsed from bytes, not text — check the fixture |
| Second `ingest` still reports new | a varying URL parameter | add it to `TRACKING_PARAMS` in `normalize.py` |
| `429` from a host | polling too hard | raise `rate_limit` in the source config |
| `sources` command fails | malformed YAML | the error names the file and the problem |
