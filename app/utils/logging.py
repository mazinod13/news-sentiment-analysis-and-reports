"""Logging setup. Every ingestion log line carries source_id, method and url --
they are the only forensics available when a remote site changes silently.
"""

from __future__ import annotations

import logging
import sys


class _ContextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = " ".join(
            f"{k}={v}"
            for k, v in (
                ("source", getattr(record, "source_id", None)),
                ("method", getattr(record, "method", None)),
                ("url", getattr(record, "url", None)),
            )
            if v is not None
        )
        return f"{base} {extras}".rstrip()


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_ContextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # httpx logs every request at INFO; ours already covers that.
    logging.getLogger("httpx").setLevel(logging.WARNING)
