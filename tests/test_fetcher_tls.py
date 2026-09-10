"""TLS trust-bundle tests.

Shared code. The bundle exists because some Nepali government sites serve an
incomplete certificate chain; see certs/README.md.
"""

from __future__ import annotations

import ssl
from datetime import datetime, timedelta, timezone

import certifi
import pytest

from app.ingestion.fetcher import build_ssl_context
from app.settings import ROOT, load_settings

CERTS_DIR = ROOT / "certs"

# Fail well before a certificate actually stops working, so this surfaces as a
# red test during normal work rather than as an outage.
EXPIRY_WARNING_DAYS = 30


def pem_files():
    return sorted(CERTS_DIR.glob("*.pem"))


def test_certs_directory_is_shipped():
    """Docker copies the repo, so a missing directory means every site with a
    short chain breaks in the container but works locally."""
    assert CERTS_DIR.is_dir()
    assert pem_files(), "certs/ has no .pem files -- did they get gitignored?"


def test_settings_point_at_the_certs_directory():
    assert load_settings().ca_certs_dir == CERTS_DIR


def test_context_still_verifies():
    """The whole point: we ADD trust anchors, we do not switch verification off.

    If this ever flips to CERT_NONE, every https fetch in the project silently
    stops checking who it is talking to.
    """
    context = build_ssl_context(CERTS_DIR)

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_context_loads_more_anchors_than_certifi_alone():
    bare = build_ssl_context(None)
    augmented = build_ssl_context(CERTS_DIR)

    assert len(augmented.get_ca_certs()) > len(bare.get_ca_certs())


def test_missing_directory_is_not_fatal(tmp_path):
    """A fresh clone without certs/, or a container that skipped it, should
    still fetch every normally-configured site."""
    context = build_ssl_context(tmp_path / "does-not-exist")

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.get_ca_certs()


def test_unreadable_pem_is_skipped_not_fatal(tmp_path):
    (tmp_path / "junk.pem").write_text("not a certificate", encoding="utf-8")

    context = build_ssl_context(tmp_path)

    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("pem", pem_files(), ids=lambda p: p.name)
def test_certificate_is_not_close_to_expiring(pem):
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cafile=str(pem))
    loaded = {c["serialNumber"] for c in context.get_ca_certs()}

    bare = ssl.create_default_context(cafile=certifi.where())
    added = loaded - {c["serialNumber"] for c in bare.get_ca_certs()}
    assert added, f"{pem.name} added no trust anchor -- is it a valid certificate?"

    for cert in context.get_ca_certs():
        if cert["serialNumber"] not in added:
            continue
        expires = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(
            tzinfo=timezone.utc
        )
        remaining = expires - datetime.now(timezone.utc)
        assert remaining > timedelta(days=EXPIRY_WARNING_DAYS), (
            f"{pem.name} expires {expires:%Y-%m-%d} ({remaining.days} days) -- "
            "re-download it from the issuer's AIA URL; see certs/README.md"
        )
