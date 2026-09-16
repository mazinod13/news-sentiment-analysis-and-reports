"""Column types that keep MySQL honest about time.

MySQL has no timezone-aware type. DATETIME stores wall-clock digits with no
zone, and TIMESTAMP converts through the session's zone and only spans
1970-2038. This corpus is Nepal time (+05:45) mixed with whatever offsets feeds
declare, so:

    on the way in   an aware datetime is converted to UTC and stored
    on the way out  it is labelled UTC again

A naive datetime is rejected rather than guessed at -- that is how a whole
corpus silently shifts by 5h45m.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """Timezone-aware datetimes on a database that has no such type."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            # fsp=6: keep microseconds, or ordering by time loses to ties.
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime reached the database; normalise it to an aware one first"
            )
        value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None) if dialect.name == "mysql" else value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
