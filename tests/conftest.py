from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import ROOT
from app.sources import load_selectors, load_source

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    """Read a saved payload. Tests never hit the network."""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fixture_text():
    return fixture


@pytest.fixture
def sources_dir() -> Path:
    return ROOT / "config" / "sources"


@pytest.fixture
def selectors_dir() -> Path:
    return ROOT / "config" / "selectors"


@pytest.fixture
def load_outlet(sources_dir, selectors_dir):
    """Load one outlet's real config + selector pack, exactly as the app does."""

    def _load(source_id: str):
        source = load_source(sources_dir / f"{source_id}.yaml")
        selectors = load_selectors(selectors_dir, source.selectors) if source.selectors else {}
        return source, selectors

    return _load
