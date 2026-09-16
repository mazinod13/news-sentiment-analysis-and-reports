"""Container liveness: is the worker's heartbeat recent?

The worker touches a heartbeat file only after work SUCCEEDS -- a finished
cycle or a committed catch-up batch. A worker stuck waiting on an unreachable
database, or failing every cycle, stops beating and the container turns
unhealthy in Portainer, instead of looking fine while doing nothing.
"""

from __future__ import annotations

import time
from pathlib import Path


def beat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time():.0f}\n", encoding="utf-8")


def check(path: Path, max_age_seconds: int, *, now: float | None = None) -> tuple[bool, str]:
    """(healthy, message) for a healthcheck to print and exit on."""
    now = time.time() if now is None else now
    try:
        age = now - path.stat().st_mtime
    except FileNotFoundError:
        return False, f"unhealthy: no heartbeat yet at {path}"
    if age > max_age_seconds:
        return False, f"unhealthy: last heartbeat {age:.0f}s ago (limit {max_age_seconds}s)"
    return True, f"healthy: last heartbeat {age:.0f}s ago"
