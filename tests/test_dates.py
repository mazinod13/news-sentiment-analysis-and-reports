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


class TestFeedDates:
    """feedparser normalises *_parsed to UTC no matter what offset the feed
    declared. Relabelling that as Nepal time instead of converting shifted
    every feed-dated article 5h45m early -- caught on Pokhara Hotline, whose
    feed publishes +0000 while its article pages publish +05:45."""

    def _entry(self, pubdate: str):
        import feedparser

        feed = feedparser.parse(
            "<rss version='2.0'><channel><item>"
            f"<title>t</title><link>https://x.test/1</link><pubDate>{pubdate}</pubDate>"
            "</item></channel></rss>"
        )
        return feed.entries[0]

    def test_utc_feed_date_is_converted_not_relabelled(self):
        from app.parsing.dates import parse_feed_datetime

        parsed = parse_feed_datetime(self._entry("Mon, 17 Aug 2026 13:32:57 +0000"))
        assert parsed.isoformat() == "2026-08-17T19:17:57+05:45"

    def test_offset_feed_date_survives_roundtrip(self):
        """A feed already stating +05:45 must land on the same instant."""
        from app.parsing.dates import parse_feed_datetime

        parsed = parse_feed_datetime(self._entry("Mon, 17 Aug 2026 19:17:57 +0545"))
        assert parsed.isoformat() == "2026-08-17T19:17:57+05:45"

    def test_missing_date_returns_none(self):
        import feedparser

        from app.parsing.dates import parse_feed_datetime

        feed = feedparser.parse(
            "<rss version='2.0'><channel><item>"
            "<title>t</title><link>https://x.test/1</link>"
            "</item></channel></rss>"
        )
        assert parse_feed_datetime(feed.entries[0]) is None


class TestNumericBikramSambat:
    """All-numeric BS dates, added for moha.gov.np and traffic.nepalpolice.gov.np.

    Both write every listing date as "२०८१-०९-०८" -- no month name anywhere.
    Before this the parser only understood month-name forms and those outlets
    lost every date.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("२०८१-०९-०८", "2024-12-23"),   # moha, hyphens
            ("२०८१/०९/०८", "2024-12-23"),   # slashes
            ("२०८३।०५।१६", "2026-09-01"),   # danda, as Nepali prose writes it
            ("२०८३-०४-२८", "2026-08-13"),   # traffic police
        ],
    )
    def test_parses_devanagari_numeric_dates(self, raw, expected):
        assert parse_datetime(raw, fmt="bs").date().isoformat() == expected

    def test_auto_still_reads_ascii_iso_as_gregorian(self):
        """THE REASON THE PATTERN IS GATED ON DEVANAGARI NUMERALS.

        parse_datetime tries BS before ISO under fmt="auto". An ungated numeric
        pattern would match "2026-09-03" -- an ordinary ISO date used by
        ocmcm-gandaki and mofa -- read 2026 as a BS year and place the article
        in 1969. Every ISO date in the project would move ~57 years early.
        """
        assert parse_datetime("2026-09-03", fmt="auto").date().isoformat() == "2026-09-03"
        assert parse_datetime("2026-09-03", fmt="iso").date().isoformat() == "2026-09-03"

    def test_ascii_numeric_is_not_guessed_as_bikram_sambat(self):
        """"2081-09-08" in ASCII is genuinely ambiguous, so we do not guess: it
        stays Gregorian. Only the Devanagari form is treated as Nepali."""
        assert parse_datetime("2081-09-08", fmt="auto").year == 2081

    def test_month_name_forms_still_work(self):
        """Regression: the numeric branch runs last and must not shadow these."""
        assert parse_datetime("भदौ ३, २०८३", fmt="bs").date().isoformat() == "2026-08-19"
        assert parse_datetime("श्रावण२९, २०८३", fmt="bs").date().isoformat() == "2026-08-14"
