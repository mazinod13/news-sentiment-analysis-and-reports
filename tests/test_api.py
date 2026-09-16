"""The HTTP API, with the database faked out. Offline -- no connection is opened.

Each test replaces the repository call the endpoint makes, so what is under
test is the API itself: authentication, parameter validation, the JSON shape
and the paging cursor.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from app.api import app as api
from app.settings import load_settings

KEY = "test-key"
HEADERS = {"X-API-Key": KEY}
WHEN = datetime(2026, 9, 15, 9, 41, 11, tzinfo=timezone.utc)


def row(**overrides):
    """One joined articles + article_analysis + article_clusters row."""
    fields = {
        "id": 7, "source_id": "kathmandupost", "url": "https://kathmandupost.com/money/1",
        "title": "Government rolls out capital market reform plan", "body": "Full text.",
        "summary": "A 21-point package.", "author": "Yagya Banjade", "lang": "en",
        "category": "economic", "image_url": None, "published_at": WHEN,
        "published_estimated": False, "fetched_at": WHEN, "grade": "C", "score": 5.5,
        "dampened": False, "keywords": [{"text": "capital market", "score": 3.0, "count": 2}],
        "matches": [{"term": "market", "tier": "low", "count": 2, "points": 1.0}],
        "cluster_id": 3,
    }
    return SimpleNamespace(**{**fields, **overrides})


@pytest.fixture
def settings(monkeypatch):
    for name in ("API_KEYS", "API_CORS_ORIGINS", "API_MAX_LIMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_KEYS", KEY)
    return load_settings()


@pytest.fixture
def fake_db(monkeypatch):
    """Every endpoint goes through session_scope; hand it a dummy session."""

    @contextmanager
    def session_scope(_settings):
        yield SimpleNamespace(execute=lambda *a, **k: None, scalar=lambda *a, **k: 0)

    monkeypatch.setattr(api, "session_scope", session_scope)


@pytest.fixture
def client(settings, fake_db):
    return TestClient(api.create_app(settings))


class TestAuthentication:
    """Keys are opt-in: empty API_KEYS serves everyone, a set one is enforced."""

    def test_open_when_no_keys_are_configured(self, settings, fake_db, monkeypatch):
        monkeypatch.setattr(api.repo, "api_articles", lambda session, **kw: [])
        open_app = api.create_app(dataclasses.replace(settings, api_keys=frozenset()))
        with TestClient(open_app) as anonymous:
            assert anonymous.get("/api/v1/articles").status_code == 200

    def test_requires_a_key_once_one_is_configured(self, client):
        assert client.get("/api/v1/articles").status_code == 401

    def test_rejects_a_wrong_key(self, client):
        assert client.get("/api/v1/articles", headers={"X-API-Key": "nope"}).status_code == 401

    def test_health_is_never_keyed(self, client, monkeypatch):
        """The container healthcheck cannot carry a key."""
        monkeypatch.setattr(api.repo, "PING", "SELECT 1")
        assert client.get("/healthz").status_code == 200


class TestArticles:
    def test_returns_articles_with_their_criticality(self, client, monkeypatch):
        monkeypatch.setattr(api.repo, "api_articles", lambda session, **kw: [row()])

        payload = client.get("/api/v1/articles", headers=HEADERS).json()

        assert payload["count"] == 1
        item = payload["items"][0]
        assert item["source_id"] == "kathmandupost"
        assert item["published_at"] == "2026-09-15T09:41:11+00:00"
        assert item["criticality"]["grade"] == "C"
        assert item["criticality"]["keywords"][0]["text"] == "capital market"
        assert item["story_id"] == 3
        assert "body" not in item, "bodies are opt-in: ?body=true"

    def test_body_is_opt_in(self, client, monkeypatch):
        monkeypatch.setattr(api.repo, "api_articles", lambda session, **kw: [row()])
        item = client.get("/api/v1/articles?body=true", headers=HEADERS).json()["items"][0]
        assert item["body"] == "Full text."

    def test_ungraded_article_reports_no_criticality(self, client, monkeypatch):
        monkeypatch.setattr(
            api.repo, "api_articles", lambda session, **kw: [row(grade=None, score=None)]
        )
        item = client.get("/api/v1/articles", headers=HEADERS).json()["items"][0]
        assert item["criticality"] is None

    def test_filters_reach_the_query(self, client, monkeypatch):
        seen = {}

        def capture(session, **kw):
            seen.update(kw)
            return []

        monkeypatch.setattr(api.repo, "api_articles", capture)
        client.get(
            "/api/v1/articles?grade=a,B&source=kathmandupost,setopati&lang=en"
            "&category=economic&since=2026-09-01&until=2026-09-16T00:00:00Z"
            "&before=500&limit=10",
            headers=HEADERS,
        )

        assert seen["grades"] == ["A", "B"], "grades are upper-cased"
        assert seen["sources"] == ["kathmandupost", "setopati"]
        assert seen["langs"] == ["en"] and seen["categories"] == ["economic"]
        assert seen["since"] == datetime(2026, 9, 1, tzinfo=timezone.utc), "bare date reads as UTC"
        assert seen["until"] == datetime(2026, 9, 16, tzinfo=timezone.utc)
        assert seen["before_id"] == 500 and seen["limit"] == 10

    def test_cursor_is_returned_only_on_a_full_page(self, client, monkeypatch):
        monkeypatch.setattr(api.repo, "api_articles", lambda session, **kw: [row(id=9)])

        full = client.get("/api/v1/articles?limit=1", headers=HEADERS).json()
        partial = client.get("/api/v1/articles?limit=5", headers=HEADERS).json()

        assert full["next_cursor"] == 9, "pass back as ?before="
        assert partial["next_cursor"] is None, "a short page is the last page"

    def test_one_article(self, client, monkeypatch):
        monkeypatch.setattr(api.repo, "api_article", lambda session, article_id: row(id=article_id))
        payload = client.get("/api/v1/articles/7", headers=HEADERS).json()
        assert payload["id"] == 7 and payload["body"] == "Full text."

    def test_missing_article_is_404(self, client, monkeypatch):
        monkeypatch.setattr(api.repo, "api_article", lambda session, article_id: None)
        response = client.get("/api/v1/articles/404", headers=HEADERS)
        assert response.status_code == 404 and "no article" in response.json()["error"]


class TestValidation:
    def test_unknown_grade_is_rejected(self, client):
        response = client.get("/api/v1/articles?grade=Z", headers=HEADERS)
        assert response.status_code == 400 and "unknown grade" in response.json()["error"]

    def test_limit_is_capped(self, client, settings):
        response = client.get(
            f"/api/v1/articles?limit={settings.api_max_limit + 1}", headers=HEADERS
        )
        assert response.status_code == 400 and "limit must be between" in response.json()["error"]

    def test_bad_date_is_rejected(self, client):
        response = client.get("/api/v1/articles?since=last-tuesday", headers=HEADERS)
        assert response.status_code == 400 and "ISO-8601" in response.json()["error"]


class TestStoriesAndSources:
    def test_stories(self, client, monkeypatch):
        story = SimpleNamespace(
            id=3, title="NEPSE jumps 48.57 points", lang="ne", article_count=6, source_count=6,
            sources=["annapurna-post-economy", "setopati-economy"], grade="C",
            first_published_at=WHEN, last_published_at=WHEN,
        )
        monkeypatch.setattr(api.repo, "top_stories", lambda session, **kw: [story])

        payload = client.get("/api/v1/stories?hours=48&min_sources=2", headers=HEADERS).json()

        assert payload["items"][0]["source_count"] == 6
        assert payload["items"][0]["sources"][0] == "annapurna-post-economy"

    def test_sources_come_from_config_not_the_database(self, client):
        payload = client.get("/api/v1/sources", headers=HEADERS).json()
        ids = {item["id"] for item in payload["items"]}
        assert payload["count"] > 50
        assert "kathmandupost" in ids
        economy = next(i for i in payload["items"] if i["id"] == "kathmandupost-economy")
        assert economy["section_of"] == "kathmandupost"
        assert economy["category"] == "economic"

    def test_stats(self, client, monkeypatch):
        monkeypatch.setattr(
            api.repo, "api_stats",
            lambda session, since: {
                "articles": 12, "stories": 4, "by_grade": {"A": 1, "F": 11},
                "by_source": [{"source_id": "setopati", "articles": 5}],
            },
        )
        payload = client.get("/api/v1/stats?hours=12", headers=HEADERS).json()
        assert payload["window_hours"] == 12 and payload["by_grade"]["A"] == 1
