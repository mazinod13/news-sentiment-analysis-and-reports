"""Duplicate detection.

Nepali outlets syndicate heavily -- one agency story appears across a dozen
sites within the hour, and the same story often reappears in a feed under a
second URL. Two layers here:

  1. exact:  canonical url_hash (enforced by a unique index in the database)
  2. near:   64-bit simhash over word shingles, Hamming distance <= 3

Story-level clustering across outlets comes later with embeddings; simhash is
enough to stop the same text being stored twice.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

HASH_BITS = 64

# Measured on Nepali article text (see tests/test_pipeline.py::TestDedupe):
# the same story with an added sentence, a dropped dateline or a fixed typo
# lands at 3-7 bits, while unrelated stories sit at 30+. Nothing was observed
# in between, so 8 splits the two populations with room on both sides.
#
# A feed summary and the same article's full body are ~22 apart and are NOT
# caught here -- they don't need to be, since their canonical URLs are
# identical and the url_hash unique index already collapses them.
NEAR_DUPLICATE_DISTANCE = 8

_TOKEN_RE = re.compile(r"[\wऀ-ॿ]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def shingles(tokens: list[str], size: int = 3) -> list[str]:
    if len(tokens) < size:
        return [" ".join(tokens)] if tokens else []
    return [" ".join(tokens[i : i + size]) for i in range(len(tokens) - size + 1)]


def simhash(text: str) -> int:
    """64-bit simhash. Small edits (a fixed typo, an added tag) barely move it."""
    features = shingles(tokenize(text))
    if not features:
        return 0
    vector = [0] * HASH_BITS
    for feature in features:
        raw = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        digest = int.from_bytes(raw, "big")
        for bit in range(HASH_BITS):
            vector[bit] += 1 if digest >> bit & 1 else -1
    result = 0
    for bit in range(HASH_BITS):
        if vector[bit] > 0:
            result |= 1 << bit
    return result


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << HASH_BITS) - 1)).bit_count()


def to_signed64(value: int) -> int:
    """simhash is unsigned 64-bit; Postgres bigint is signed. Wrap on the way in."""
    return value - (1 << 64) if value >= (1 << 63) else value


def from_signed64(value: int) -> int:
    return value + (1 << 64) if value < 0 else value


def is_near_duplicate(
    candidate: int, known: Iterable[int], *, distance: int = NEAR_DUPLICATE_DISTANCE
) -> bool:
    if candidate == 0:
        return False
    return any(other and hamming(candidate, other) <= distance for other in known)


def dedupe_batch(items, key=lambda item: item.url) -> list:
    """Drop repeats within a single fetch, keeping the first occurrence.

    Index pages list the same story in several rails; feeds occasionally repeat
    an item across pages.
    """
    seen: set = set()
    out = []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out
