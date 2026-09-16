"""The HTTP API: stored articles with their criticality grade, and their sources.

    GET /api/v1/articles          newest first, filterable, cursor-paged
    GET /api/v1/articles/{id}     one article, with its keywords and grade
    GET /api/v1/stories           events, one row per story, most outlets first
    GET /api/v1/sources           the configured outlets
    GET /api/v1/stats             counts by grade and source for a window
    GET /healthz                  liveness + database reachability (never keyed)

Every timestamp in and out is UTC ISO-8601. Nepal is +05:45, so a caller that
wants local time converts; the API never guesses a zone.

Handlers are ordinary `def`, not `async def`: the database driver is
synchronous, so Starlette runs them in a threadpool instead of blocking the
event loop.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.nlp.criticality import GRADES
from app.settings import Settings, load_settings
from app.sources import load_sources
from app.storage import repositories as repo
from app.storage.db import session_scope

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
OPEN_PATHS = {"/healthz"}


class ApiError(Exception):
    """A bad request, reported as JSON rather than a stack trace."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


# --- parameter parsing --------------------------------------------------------


def _int(request: Request, name: str, default: int, *, low: int, high: int) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise ApiError(f"{name} must be between {low} and {high}")
    return value


def _when(request: Request, name: str) -> datetime | None:
    """An ISO-8601 date or timestamp, read as UTC when it carries no offset."""
    raw = request.query_params.get(name)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(
            f"{name} must be ISO-8601, e.g. 2026-09-15 or 2026-09-15T10:00:00Z"
        ) from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _csv(request: Request, name: str) -> list[str] | None:
    raw = request.query_params.get(name)
    if not raw:
        return None
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or None


def _grades(request: Request) -> list[str] | None:
    values = _csv(request, "grade")
    if values is None:
        return None
    unknown = [v for v in values if v.upper() not in GRADES]
    if unknown:
        raise ApiError(f"unknown grade(s): {', '.join(unknown)}; use {', '.join(GRADES)}")
    return [v.upper() for v in values]


def _flag(request: Request, name: str) -> bool:
    return request.query_params.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# --- serialisation ------------------------------------------------------------


def _moment(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def article_json(row, *, include_body: bool) -> dict:
    payload = {
        "id": row.id,
        "source_id": row.source_id,
        "url": row.url,
        "title": row.title,
        "summary": row.summary,
        "author": row.author,
        "lang": row.lang,
        "category": row.category,
        "image_url": row.image_url,
        "published_at": _moment(row.published_at),
        # True when the publish time is a fallback: exclude these from any
        # time series that needs real precision.
        "published_estimated": row.published_estimated,
        "fetched_at": _moment(row.fetched_at),
        "criticality": {
            "grade": row.grade,
            "score": row.score,
            "dampened": row.dampened,
            "keywords": row.keywords or [],
            "matched_terms": row.matches or [],
        } if row.grade else None,
        "story_id": row.cluster_id,
    }
    if include_body:
        payload["body"] = row.body
    return payload


def story_json(row) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "lang": row.lang,
        "article_count": row.article_count,
        "source_count": row.source_count,
        "sources": row.sources or [],
        # The most critical grade among the articles in the story.
        "grade": row.grade,
        "first_published_at": _moment(row.first_published_at),
        "last_published_at": _moment(row.last_published_at),
    }


# --- endpoints ----------------------------------------------------------------


def list_articles(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    limit = _int(request, "limit", 50, low=1, high=settings.api_max_limit)
    include_body = _flag(request, "body")
    with session_scope(settings) as session:
        rows = repo.api_articles(
            session,
            since=_when(request, "since"),
            until=_when(request, "until"),
            grades=_grades(request),
            sources=_csv(request, "source"),
            langs=_csv(request, "lang"),
            categories=_csv(request, "category"),
            before_id=_int(request, "before", 0, low=0, high=2**63 - 1) or None,
            limit=limit,
        )
        items = [article_json(row, include_body=include_body) for row in rows]
    return JSONResponse({
        "count": len(items),
        # Pass this back as ?before= for the next page; null means the end.
        "next_cursor": items[-1]["id"] if len(items) == limit else None,
        "items": items,
    })


def get_article(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    article_id = int(request.path_params["article_id"])
    with session_scope(settings) as session:
        row = repo.api_article(session, article_id)
        if row is None:
            raise ApiError(f"no article with id {article_id}", status=404)
        return JSONResponse(article_json(row, include_body=True))


def list_stories(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    hours = _int(request, "hours", 24, low=1, high=24 * 90)
    since = _when(request, "since") or datetime.now(timezone.utc) - timedelta(hours=hours)
    with session_scope(settings) as session:
        rows = repo.top_stories(
            session,
            since=since,
            limit=_int(request, "limit", 20, low=1, high=settings.api_max_limit),
            min_sources=_int(request, "min_sources", 1, low=1, high=100),
        )
        items = [story_json(row) for row in rows]
    return JSONResponse({"count": len(items), "since": _moment(since), "items": items})


def list_sources(request: Request) -> JSONResponse:
    """The configured outlets. Read from YAML, so this needs no database."""
    settings: Settings = request.app.state.settings
    sources = load_sources(settings.sources_dir)
    items = [
        {
            "id": source.id,
            "name": source.name,
            "url": source.url,
            "homepage": source.homepage,
            "method": source.method,
            "lang": source.lang,
            "category": source.category,
            "priority": source.priority,
            "poll_interval_minutes": source.poll_interval_minutes,
            "active": source.active,
            "section_of": source.section_of,
        }
        for source in sources.values()
    ]
    return JSONResponse({"count": len(items), "items": items})


def stats(request: Request) -> JSONResponse:
    settings: Settings = request.app.state.settings
    hours = _int(request, "hours", 24, low=1, high=24 * 90)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with session_scope(settings) as session:
        summary = repo.api_stats(session, since=since)
    return JSONResponse({"window_hours": hours, "since": _moment(since), **summary})


def healthz(request: Request) -> JSONResponse:
    """Liveness for the container healthcheck: is the database answering?"""
    settings: Settings = request.app.state.settings
    try:
        with session_scope(settings) as session:
            session.execute(repo.PING)
    except SQLAlchemyError as exc:
        log.warning("health check failed: %s", exc)
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})


# --- wiring -------------------------------------------------------------------


def _unauthorised() -> JSONResponse:
    return JSONResponse({"error": "missing or invalid X-API-Key"}, status_code=401)


def api_key_middleware(app, keys: frozenset[str]):
    """Every path except /healthz needs a key from API_KEYS."""

    async def middleware(scope, receive, send):
        if scope["type"] == "http" and scope.get("path") not in OPEN_PATHS:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            if headers.get("x-api-key", "") not in keys:
                await _unauthorised()(scope, receive, send)
                return
        await app(scope, receive, send)

    return middleware


def create_app(settings: Settings | None = None) -> Starlette:
    """Build the API. Authentication is opt-in: set API_KEYS to require a key.

    Left empty, the API is open to anyone who can reach the port -- which is
    the point on a trusted network, where the corpus is public reporting
    already. Everything here is read-only, so the exposure is disclosure of
    what has been collected, not tampering.
    """
    settings = settings or load_settings()

    async def on_bad_request(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    middleware = []
    if settings.api_cors_origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=list(settings.api_cors_origins),
                allow_methods=["GET"],
                allow_headers=["X-API-Key"],
            )
        )

    app = Starlette(
        routes=[
            Route(f"{API_PREFIX}/articles", list_articles),
            Route(f"{API_PREFIX}/articles/{{article_id:int}}", get_article),
            Route(f"{API_PREFIX}/stories", list_stories),
            Route(f"{API_PREFIX}/sources", list_sources),
            Route(f"{API_PREFIX}/stats", stats),
            Route("/healthz", healthz),
        ],
        exception_handlers={ApiError: on_bad_request},
        middleware=middleware,
    )
    app.state.settings = settings
    if settings.api_keys:
        app.add_middleware(api_key_middleware, keys=settings.api_keys)
        log.info("API authentication on: %d key(s) accepted", len(settings.api_keys))
    else:
        log.warning(
            "API_KEYS is empty: serving the corpus to anyone who can reach this port"
        )
    return app
