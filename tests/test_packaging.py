"""Keeps requirements*.txt and pyproject.toml from drifting apart.

Two files listing dependencies is a classic source of "works in Docker, breaks
in CI". pyproject.toml is the source of truth; these tests fail the moment
someone adds a dependency to one and forgets the other.
"""

from __future__ import annotations

import pytest

from app.settings import ROOT

tomllib = pytest.importorskip(
    "tomllib", reason="tomllib needs Python 3.11+ (the version this project targets)"
)


def parse_requirements(name: str) -> list[str]:
    """Requirement lines only -- comments, blanks and `-r` includes dropped."""
    text = (ROOT / name).read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith(("#", "-"))
    ]


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_runtime_requirements_match_pyproject(pyproject):
    assert parse_requirements("requirements.txt") == pyproject["project"]["dependencies"], (
        "requirements.txt and pyproject.toml [project.dependencies] disagree -- "
        "update both in the same commit"
    )


def test_dev_requirements_match_pyproject(pyproject):
    declared = pyproject["project"]["optional-dependencies"]["dev"]
    assert parse_requirements("requirements-dev.txt") == declared, (
        "requirements-dev.txt and pyproject.toml [project.optional-dependencies].dev "
        "disagree -- update both in the same commit"
    )


def test_dev_requirements_include_runtime():
    """Without the -r include, `pip install -r requirements-dev.txt` would
    silently give you a broken environment with no httpx."""
    text = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in text
