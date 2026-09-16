# Nepal News Sentiment — API

A read-only HTTP API over a corpus of Nepali and English news: the articles
themselves, a criticality grade for each, and the outlets they came from.
Everything is `GET`, and every response is JSON.

- **Base URL** — `http://<host>:8000`
- **Times** — UTC, ISO-8601 (`2026-09-15T09:41:11+00:00`). Nepal is **+05:45**;
  convert on your side. The API never guesses a zone.
- **Authentication** — usually none. If the deployment sets API keys, send
  `X-API-Key: <key>` on every path except `/healthz`; without it you get `401`.

```bash
curl "http://<host>:8000/api/v1/articles?grade=A,B&limit=5"
```

## Contents

- [Endpoints](#endpoints)
- [GET /api/v1/articles](#get-apiv1articles)
- [GET /api/v1/articles/{id}](#get-apiv1articlesid)
- [GET /api/v1/stories](#get-apiv1stories)
- [GET /api/v1/sources](#get-apiv1sources)
- [GET /api/v1/stats](#get-apiv1stats)
- [GET /healthz](#get-healthz)
- [Paging](#paging)
- [Errors](#errors)
- [What the values mean](#what-the-values-mean)
- [Things worth knowing](#things-worth-knowing)

## Endpoints

| Endpoint | Returns |
|---|---|
| `GET /api/v1/articles` | articles, newest first, filterable and paged |
| `GET /api/v1/articles/{id}` | one article, always with its full text |
| `GET /api/v1/stories` | one row per event, however many outlets covered it |
| `GET /api/v1/sources` | the configured outlets |
| `GET /api/v1/stats` | counts by grade and outlet over a window |
| `GET /healthz` | service and database health (never needs a key) |

---

## GET /api/v1/articles

| Parameter | Default | Meaning |
|---|---|---|
| `grade` | all | one or more of `A`–`F`, comma-separated (`grade=A,B`). Case-insensitive |
| `source` | all | outlet ids, comma-separated. See [`/sources`](#get-apiv1sources) |
| `lang` | all | `ne`, `en`, or both |
| `category` | all | `news`, `economic`, `govt`, `disaster`, `social`, `sports`, `entertainment` |
| `since` | — | only articles published at or after this. ISO date or timestamp |
| `until` | — | only articles published **before** this |
| `limit` | `50` | 1–200 |
| `before` | — | paging cursor; see [Paging](#paging) |
| `body` | `false` | `true` includes the full article text |

A bare date such as `since=2026-09-15` means midnight UTC. `Z` is accepted:
`since=2026-09-15T06:00:00Z`.

```bash
curl "http://<host>:8000/api/v1/articles?category=economic&grade=A,B&since=2026-09-15&limit=20"
```

```json
{
  "count": 1,
  "next_cursor": null,
  "items": [
    {
      "id": 4412,
      "source_id": "kathmandupost-economy",
      "url": "https://kathmandupost.com/money/2026/09/15/government-rolls-out-...",
      "title": "Government rolls out sweeping capital market reform plan",
      "summary": "The 21-point package seeks to ease IPO rules ...",
      "author": "Yagya Banjade",
      "lang": "en",
      "category": "economic",
      "image_url": "https://assets-api.kathmandupost.com/thumb.php?src=...",
      "published_at": "2026-09-15T09:41:11+00:00",
      "published_estimated": false,
      "fetched_at": "2026-09-15T10:05:03+00:00",
      "criticality": {
        "grade": "B",
        "score": 9.5,
        "dampened": false,
        "keywords": [{"text": "capital market", "score": 6.0, "count": 4}],
        "matched_terms": [
          {"term": "flood*", "tier": "critical", "count": 2, "points": 10.0}
        ]
      },
      "story_id": 87
    }
  ]
}
```

### Article fields

| Field | Type | Notes |
|---|---|---|
| `id` | integer | stable; also the paging cursor |
| `source_id` | string | the outlet, as in `/sources` |
| `url` | string | canonical article URL, tracking parameters stripped |
| `title`, `summary` | string | `summary` may be empty |
| `body` | string | only with `?body=true`, and always on the single-article endpoint |
| `author` | string or null | often the outlet's own name |
| `lang` | string | `ne` or `en` |
| `category` | string | see the `category` filter above |
| `image_url` | string or null | lead image |
| `published_at` | timestamp | when the outlet published it |
| `published_estimated` | boolean | `true` means the outlet gave no usable date and this is when it was first seen — exclude these from precise time series |
| `fetched_at` | timestamp | when it was collected |
| `criticality` | object or null | `null` until the article has been graded |
| `story_id` | integer or null | groups articles about one event; `null` until grouped |

### `criticality`

| Field | Type | Notes |
|---|---|---|
| `grade` | string | `A` (most critical) to `F` (routine) |
| `score` | number | the weighted score behind the grade |
| `dampened` | boolean | `true` when the story is about prevention, awareness or training, which scores lower deliberately — a suicide-prevention agreement is not a suicide |
| `keywords` | array | `{text, score, count}`, best first |
| `matched_terms` | array | `{term, tier, count, points}` — the terms that produced the grade |

---

## GET /api/v1/articles/{id}

One article, always including `body`. `404` if the id does not exist.

```bash
curl "http://<host>:8000/api/v1/articles/4412"
```

---

## GET /api/v1/stories

The same event reported by several outlets is grouped into one **story**, so it
can be counted once. Most-covered first.

| Parameter | Default | Meaning |
|---|---|---|
| `hours` | `24` | look-back window, 1–2160 |
| `since` | — | explicit start instead of `hours` |
| `min_sources` | `1` | only stories covered by at least this many outlets |
| `limit` | `20` | 1–200 |

```bash
curl "http://<host>:8000/api/v1/stories?hours=48&min_sources=3"
```

```json
{
  "count": 1,
  "since": "2026-09-14T09:00:00+00:00",
  "items": [
    {
      "id": 87,
      "title": "लगानीकर्ता उत्साहित, ४८.५७ अंकले बढ्यो नेप्से",
      "lang": "ne",
      "article_count": 6,
      "source_count": 6,
      "sources": ["annapurna-post-economy", "himalpress-economy", "setopati-economy"],
      "grade": "C",
      "first_published_at": "2026-09-15T09:20:00+00:00",
      "last_published_at": "2026-09-15T11:45:00+00:00"
    }
  ]
}
```

`title` is the headline of the first article in the story, used as a label.
`grade` is the most critical grade among its articles, and may be `null` if none
has been graded. Stories never mix languages: Nepali and English coverage of one
event are separate stories.

---

## GET /api/v1/sources

Every configured outlet. Served from configuration, so it answers even when the
corpus is empty.

```json
{
  "count": 82,
  "items": [
    {
      "id": "kathmandupost-economy",
      "name": "The Kathmandu Post (Money)",
      "url": "https://kathmandupost.com/money",
      "homepage": "https://kathmandupost.com/money",
      "method": "html",
      "lang": "en",
      "category": "economic",
      "priority": 2,
      "poll_interval_minutes": 30,
      "active": true,
      "section_of": "kathmandupost"
    }
  ]
}
```

| Field | Notes |
|---|---|
| `method` | `rss` (a feed) or `html` (a listing page) |
| `priority` | 1–4, driving how often it is polled |
| `poll_interval_minutes` | 15, 30, 60 or 360 |
| `active` | `false` means configured but not being collected |
| `section_of` | set when this is a section of another outlet, e.g. its economy desk |

---

## GET /api/v1/stats

| Parameter | Default | Meaning |
|---|---|---|
| `hours` | `24` | window, 1–2160 |

```json
{
  "window_hours": 24,
  "since": "2026-09-15T09:00:00+00:00",
  "articles": 312,
  "stories": 274,
  "by_grade": {"A": 4, "B": 11, "C": 39, "D": 62, "E": 70, "F": 126},
  "by_source": [{"source_id": "setopati", "articles": 28}]
}
```

`by_source` is ordered by article count, busiest first.

---

## GET /healthz

```json
{"status": "ok", "database": "ok"}
```

`503` with `{"status": "unavailable", "database": "unreachable"}` when the
database cannot be reached. Never requires a key.

---

## Paging

Responses from `/articles` carry `next_cursor`. Pass it back as `before` for the
next page; `null` means you have reached the end.

```bash
curl "http://<host>:8000/api/v1/articles?limit=100"               # → next_cursor: 4313
curl "http://<host>:8000/api/v1/articles?limit=100&before=4313"   # → next page
```

Paging is by article id, not by offset, so a cursor stays correct while new
articles are being collected: you never re-read or skip a row.

## Errors

| Status | Body | When |
|---|---|---|
| `400` | `{"error": "limit must be between 1 and 200"}` | a parameter is out of range, unparseable, or an unknown grade |
| `401` | `{"error": "missing or invalid X-API-Key"}` | keys are configured and yours was missing or wrong |
| `404` | `{"error": "no article with id 99999"}` | unknown article id |
| `503` | `{"status": "unavailable", "database": "unreachable"}` | `/healthz` only |

## What the values mean

**Grades** run from A to F, from a weighted lexicon of critical terms in Nepali
and English. Title mentions count double, and repeats of a term are capped.

| Grade | Typically |
|:--:|---|
| **A** | deaths, disasters, explosions, curfews |
| **B** | injuries, arrests, violent protests, serious accidents |
| **C** | disputes, shortages, resignations, or several lesser signals |
| **D** | a single warning, dispute or legal action |
| **E** | routine governance: decisions, budgets, elections |
| **F** | nothing critical |

This is weighted keyword matching, not language understanding: "no casualties"
still matches *casualties*, and a term missing from the lexicon scores nothing.
Treat grades as triage, not as a verdict.

## Things worth knowing

- **One row per URL.** Exact duplicates and near-identical syndicated copies are
  dropped as they are collected, so an agency story appears once, attributed to
  whichever outlet was read first.
- **`criticality` and `story_id` are `null` for a short while** after an article
  arrives, until grading and grouping run.
- **eKantipur provincial and business pages** render their text in the browser,
  so their `body` is the lede only — roughly 120 characters, not the full
  article.
- **Estimated dates.** Some outlets publish no usable date; those articles carry
  `published_estimated: true` and a `published_at` of when they were first seen.
- **Filters combine with AND**, and values inside one filter with OR: for
  example `grade=A,B&lang=ne` means "A or B, in Nepali".
