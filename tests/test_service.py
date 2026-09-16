"""The unattended service: database URL from .env parts, healthcheck, catch-up timing.

Offline -- nothing here opens a connection.
"""

from __future__ import annotations

import dataclasses
import os
import ssl
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy.engine import make_url

from app.scheduler import health
from app.scheduler.worker import CATCH_UP_EVERY, should_catch_up
from app.settings import NPT, database_url, load_settings
from app.storage.db import connect_args

DB_VARS = (
    "DATABASE_URL", "MYSQL_DRIVER", "MYSQL_USER", "MYSQL_PASSWORD",
    "MYSQL_HOST", "MYSQL_PORT", "MYSQL_DB", "MYSQL_SSLMODE",
    "MYSQL_SSLROOTCERT",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in DB_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestDatabaseUrl:
    def test_built_from_mysql_parts(self, clean_env):
        clean_env.setenv("MYSQL_HOST", "db.internal")
        clean_env.setenv("MYSQL_PORT", "3307")
        clean_env.setenv("MYSQL_USER", "scraper")
        clean_env.setenv("MYSQL_PASSWORD", "secret")
        clean_env.setenv("MYSQL_DB", "news")
        url = make_url(database_url())
        assert (url.host, url.port, url.username, url.password, url.database) == (
            "db.internal", 3307, "scraper", "secret", "news"
        )

    def test_password_with_url_characters_survives(self, clean_env):
        """Pasted raw into a URL, @ / : # would split it in the wrong places."""
        clean_env.setenv("MYSQL_HOST", "db.internal")
        clean_env.setenv("MYSQL_PASSWORD", "p@ss:w/rd#1")
        url = make_url(database_url())
        assert url.password == "p@ss:w/rd#1"
        assert url.host == "db.internal"

    def test_default_driver_is_pymysql(self, clean_env):
        assert make_url(database_url()).get_driver_name() == "pymysql"

    def test_connection_is_utf8mb4(self, clean_env):
        """MySQL's "utf8" is three bytes and cannot hold Devanagari."""
        assert make_url(database_url()).query["charset"] == "utf8mb4"

    def test_sslmode_stays_out_of_the_url(self, clean_env):
        """PyMySQL takes TLS as connect arguments, not as a URL option."""
        clean_env.setenv("MYSQL_SSLMODE", "require")
        assert "sslmode" not in make_url(database_url()).query

    def test_database_url_wins_over_parts(self, clean_env):
        clean_env.setenv("MYSQL_HOST", "ignored")
        clean_env.setenv("DATABASE_URL", "mysql+pymysql://a:b@elsewhere:3306/x")
        assert make_url(database_url()).host == "elsewhere"


class TestDatabaseSsl:
    def _settings(self, clean_env, mode):
        clean_env.setenv("MYSQL_HOST", "db.internal")
        if mode is not None:
            clean_env.setenv("MYSQL_SSLMODE", mode)
        return load_settings()

    @pytest.mark.parametrize("mode", [None, "disable"])
    def test_no_tls_requested(self, clean_env, mode):
        assert connect_args(self._settings(clean_env, mode)) == {}

    def _context(self, clean_env, mode):
        """Build the context PyMySQL itself would, from our options."""
        from pymysql.connections import Connection

        args = connect_args(self._settings(clean_env, mode))
        return Connection._create_ssl_ctx(None, args["ssl"]), args

    def test_require_encrypts_without_verifying(self, clean_env):
        """A server using its own auto-generated certificate is self-signed:
        demanding a trusted chain here would refuse every such connection."""
        context, args = self._context(clean_env, "require")
        assert "ca" not in args["ssl"]
        assert context.verify_mode == ssl.CERT_NONE and not context.check_hostname

    def test_verify_ca_checks_the_certificate_only(self, clean_env):
        context, _ = self._context(clean_env, "verify-ca")
        assert context.verify_mode == ssl.CERT_REQUIRED and not context.check_hostname

    def test_verify_full_checks_certificate_and_hostname(self, clean_env):
        context, _ = self._context(clean_env, "verify-full")
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname

    def test_private_ca_bundle_is_used(self, clean_env, tmp_path):
        """A managed server with its own CA: the bundle must be passed, not ignored."""
        bundle = tmp_path / "ca.pem"
        bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        clean_env.setenv("MYSQL_SSLROOTCERT", str(bundle))
        settings = self._settings(clean_env, "verify-full")
        assert dataclasses.replace(settings).database_ssl_root_cert == str(bundle)
        assert connect_args(settings)["ssl"]["ca"] == str(bundle)

    def test_a_private_bundle_is_ignored_by_require(self, clean_env, tmp_path):
        """require never verifies, so it must not load a CA at all."""
        clean_env.setenv("MYSQL_SSLROOTCERT", str(tmp_path / "ca.pem"))
        assert "ca" not in connect_args(self._settings(clean_env, "require"))["ssl"]


class TestHealthcheck:
    def test_no_heartbeat_is_unhealthy(self, tmp_path):
        healthy, message = health.check(tmp_path / "missing", 60)
        assert not healthy and "no heartbeat" in message

    def test_recent_heartbeat_is_healthy(self, tmp_path):
        path = tmp_path / "beat"
        health.beat(path)
        assert health.check(path, 60)[0]

    def test_stale_heartbeat_is_unhealthy(self, tmp_path):
        path = tmp_path / "beat"
        health.beat(path)
        old = time.time() - 3600
        os.utime(path, (old, old))
        healthy, message = health.check(path, 1800)
        assert not healthy and "limit 1800s" in message


class TestCatchUpTiming:
    NOW = datetime(2026, 9, 16, 9, 0, tzinfo=NPT)

    def test_always_after_polling_sources(self):
        assert should_catch_up(polled_sources=True, last_catch_up=self.NOW, now=self.NOW)

    def test_at_startup(self):
        assert should_catch_up(polled_sources=False, last_catch_up=None, now=self.NOW)

    def test_not_on_every_idle_tick(self):
        recent = self.NOW - timedelta(minutes=1)
        assert not should_catch_up(polled_sources=False, last_catch_up=recent, now=self.NOW)

    def test_periodically_even_when_idle(self):
        """Self-healing: a fixed lexicon is picked up without a new article."""
        old = self.NOW - CATCH_UP_EVERY
        assert should_catch_up(polled_sources=False, last_catch_up=old, now=self.NOW)
