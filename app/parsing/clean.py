"""Text cleaning. Pure functions -- no network, no config, trivially testable."""

from __future__ import annotations

import html
import re
import unicodedata

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ​‌‍]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")

# Devanagari digits -> ASCII. Must run before any numeric parsing.
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


def strip_tags(value: str) -> str:
    return _TAG_RE.sub(" ", value)


def clean(value: str | None) -> str:
    """Unescape entities, drop tags, normalise Unicode and collapse whitespace.

    NFC matters for Devanagari: the same word can arrive in different
    normalisation forms from different sites and would otherwise not compare
    equal, which quietly breaks deduplication.
    """
    if not value:
        return ""
    text = html.unescape(value)
    text = strip_tags(text)
    text = html.unescape(text)  # entities sometimes survive one pass
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def to_ascii_digits(value: str) -> str:
    return value.translate(DEVANAGARI_DIGITS)


def looks_devanagari(value: str, threshold: float = 0.2) -> bool:
    """True when enough of the letters are Devanagari to call the text Nepali."""
    letters = [c for c in value if c.isalpha()]
    if not letters:
        return False
    deva = sum(1 for c in letters if "ऀ" <= c <= "ॿ")
    return deva / len(letters) >= threshold
