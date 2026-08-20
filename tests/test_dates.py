"""Shared date-parsing tests. Add a case here when an outlet shows you a date
format the parser has not seen before."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.parsing.dates import DateParseError, is_implausible, parse_bs_datetime, parse_datetime
from app.settings import NPT


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("भदौ ३, २०८३ बुधबार १४:२९:५३", "2026-08-19"),   # Annapurna Post article page
        ("०३ भदौ २०८३, बुधबार", "2026-08-19"),            # Annapurna Post header
        ("२०८३ भदौ ३", "2026-08-19"),                     # year-first variant
        ("Bhadau 3, 2083", "2026-08-19"),                 # romanised
        ("१ बैशाख २०८२", "2025-04-14"),                    # Nepali new year
    ],
)
def test_bikram_sambat_conversion(raw, expected):
    assert parse_bs_datetime(raw).date().isoformat() == expected


def test_bs_time_component():
    parsed = parse_bs_datetime("भदौ ३, २०८३ बुधबार १४:२९:५३")
    assert (parsed.hour, parsed.minute, parsed.second) == (14, 29, 53)
    assert parsed.tzinfo is not None


def test_bs_rejects_garbage():
    with pytest.raises(DateParseError):
        parse_bs_datetime("कुनै मिति छैन")


def test_auto_falls_back_to_iso():
    parsed = parse_datetime("2026-08-19T14:29:53+05:45")
    assert parsed.date().isoformat() == "2026-08-19"


def test_naive_iso_gets_nepal_time():
    parsed = parse_datetime("2026-08-19 14:29:53", fmt="iso")
    assert parsed.utcoffset().total_seconds() == 5 * 3600 + 45 * 60


def test_implausible_dates():
    now = datetime.now(NPT)
    assert is_implausible(now + timedelta(days=1)) is True     # future
    assert is_implausible(datetime(1998, 1, 1, tzinfo=NPT)) is True
    assert is_implausible(now - timedelta(hours=2)) is False
