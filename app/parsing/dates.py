"""Date handling: Bikram Sambat -> Gregorian, feed dates, meta-tag dates.

Nepali outlets publish dates three different ways and none of them can be
trusted on its own:

  * a real RFC-822 <pubDate> in the feed (best case, often absent or wrong),
  * an ISO timestamp in an article <meta> tag,
  * a Bikram Sambat string in Devanagari, e.g. "भदौ ३, २०८३ बुधबार १४:२९:५३",
    which is the ONLY date Annapurna Post exposes.

Everything returned here is timezone-aware in Asia/Kathmandu (UTC+05:45).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import nepali_datetime
from dateutil import parser as dateutil_parser

from app.parsing.clean import to_ascii_digits
from app.settings import NPT

# Every spelling of every Nepali month we have seen in the wild, longest first
# so that "श्रावण" is not shadowed by a shorter prefix during matching.
NEPALI_MONTHS: dict[str, int] = {
    "बैशाख": 1, "वैशाख": 1, "baishakh": 1, "baisakh": 1,
    "जेठ": 2, "जेष्ठ": 2, "ज्येष्ठ": 2, "jestha": 2, "jeth": 2,
    "असार": 3, "अषाढ": 3, "आषाढ": 3, "ashad": 3, "asar": 3,
    "साउन": 4, "श्रावण": 4, "shrawan": 4, "saun": 4,
    "भदौ": 5, "भाद्र": 5, "bhadau": 5, "bhadra": 5,
    "असोज": 6, "आश्विन": 6, "ashoj": 6, "asoj": 6,
    "कार्तिक": 7, "कात्तिक": 7, "kartik": 7,
    "मंसिर": 8, "मङ्सिर": 8, "mangsir": 8,
    "पुष": 9, "पुस": 9, "poush": 9, "push": 9,
    "माघ": 10, "magh": 10,
    "फागुन": 11, "फाल्गुन": 11, "falgun": 11, "phagun": 11,
    "चैत": 12, "चैत्र": 12, "chaitra": 12, "chait": 12,
}

_MONTH_ALTERNATION = "|".join(sorted(NEPALI_MONTHS, key=len, reverse=True))

# A 1-2 digit number that is not part of a longer run of digits -- this is what
# keeps "2083" from being read as a day.
_DAY = r"(?<!\d)(\d{1,2})(?!\d)"
_YEAR = r"(?<!\d)(\d{4})(?!\d)"

# "भदौ ३, २०८३ ..."  (month day year) -- Annapurna Post article body
_BS_MONTH_FIRST = re.compile(rf"({_MONTH_ALTERNATION})\s*{_DAY}\s*,?\s*{_YEAR}", re.I)
# "०३ भदौ २०८३, ..."  (day month year) -- Annapurna Post page header
_BS_DAY_FIRST = re.compile(rf"{_DAY}\s*({_MONTH_ALTERNATION})\s*,?\s*{_YEAR}", re.I)
# "२०८३ भदौ ३"  (year month day) -- common on government portals
_BS_YEAR_FIRST = re.compile(rf"{_YEAR}\s*({_MONTH_ALTERNATION})\s*,?\s*{_DAY}", re.I)
# trailing clock, with or without seconds
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


class DateParseError(ValueError):
    pass


def parse_bs_datetime(text: str) -> datetime:
    """Parse a Bikram Sambat date string into a tz-aware Gregorian datetime.

    >>> parse_bs_datetime("भदौ ३, २०८३ बुधबार १४:२९:५३").date().isoformat()
    '2026-08-19'
    """
    if not text or not text.strip():
        raise DateParseError("empty date string")

    normalised = to_ascii_digits(text).strip()

    # Order matters only in that each pattern pins the year to four digits, so
    # at most one of them can match a given string.
    if match := _BS_MONTH_FIRST.search(normalised):
        month_name, day, year = match.group(1), int(match.group(2)), int(match.group(3))
    elif match := _BS_DAY_FIRST.search(normalised):
        day, month_name, year = int(match.group(1)), match.group(2), int(match.group(3))
    elif match := _BS_YEAR_FIRST.search(normalised):
        year, month_name, day = int(match.group(1)), match.group(2), int(match.group(3))
    else:
        raise DateParseError(f"no Bikram Sambat date found in {text!r}")

    month = NEPALI_MONTHS[month_name.lower()]

    hour = minute = second = 0
    time_match = _TIME_RE.search(normalised[match.end():])
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
        second = int(time_match.group(3) or 0)

    try:
        gregorian = nepali_datetime.date(year, month, day).to_datetime_date()
    except Exception as exc:  # out of the library's supported BS range
        raise DateParseError(f"cannot convert BS {year}-{month:02d}-{day:02d}: {exc}") from exc

    return datetime(
        gregorian.year, gregorian.month, gregorian.day, hour, minute, second, tzinfo=NPT
    )


def parse_datetime(text: str, *, fmt: str = "auto") -> datetime:
    """Parse a date string. fmt is 'bs', 'iso', or 'auto' (try BS, then ISO)."""
    if fmt == "bs":
        return parse_bs_datetime(text)
    if fmt in {"iso", "auto"}:
        if fmt == "auto":
            try:
                return parse_bs_datetime(text)
            except DateParseError:
                pass
        try:
            parsed = dateutil_parser.parse(text)
        except (ValueError, OverflowError) as exc:
            raise DateParseError(f"unparseable date {text!r}") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=NPT)
    raise DateParseError(f"unknown date format {fmt!r}")


def parse_feed_datetime(entry) -> datetime | None:
    """Pull a publish time out of a feedparser entry, or None if absent.

    Absent is common and is not an error -- Annapurna Post ships no <pubDate>
    at all. The caller falls back to the article page, then to fetched_at.
    """
    for attr in ("published_parsed", "updated_parsed"):
        struct = getattr(entry, attr, None)
        if struct:
            # feedparser normalises *_parsed to UTC regardless of the offset the
            # feed declared. Labelling it NPT instead of converting from UTC
            # shifts every article 5h45m early -- convert, never relabel.
            return datetime(*struct[:6], tzinfo=timezone.utc).astimezone(NPT)
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parse_datetime(raw)
            except DateParseError:
                continue
    return None


def is_implausible(when: datetime, *, now: datetime | None = None) -> bool:
    """True for dates a feed cannot honestly be claiming.

    Guards against the common failure where a feed stamps every item with the
    current time, and against clock-skewed future dates.
    """
    now = now or datetime.now(NPT)
    if when > now.replace(microsecond=0):
        return (when - now).total_seconds() > 3600
    return when.year < 2000
