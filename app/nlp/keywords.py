"""Keywords: noun-phrase chunks, ranked by frequency, length and the title.

The chunk rule is the `NP:{<NN.*>}` grammar of the Hindi approach -- a keyword
is a run of noun-like words -- plus one Nepali rule: a word that carried a case
marker (नेपालका, मन्त्रालयमा) ends the phrase.

    गृह मन्त्रालय र काठमाडौं विश्वविद्यालयको मानसिक स्वास्थ्य विभागबीच ...
    [गृह मन्त्रालय]   [काठमाडौं विश्वविद्यालय]   [मानसिक स्वास्थ्य विभाग]
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.nlp import NN
from app.nlp.tokens import Token

MAX_PHRASE_WORDS = 4
TITLE_BOOST = 2.0


@dataclass(frozen=True)
class Keyword:
    text: str
    score: float
    count: int


def noun_phrases(tokens: list[Token]) -> list[tuple[str, ...]]:
    phrases: list[tuple[str, ...]] = []
    run: list[str] = []

    def flush() -> None:
        # A run longer than a phrase is usually a list of names; keep the words.
        if len(run) <= MAX_PHRASE_WORDS:
            phrases.append(tuple(run))
        else:
            phrases.extend((word,) for word in run)
        run.clear()

    for token in tokens:
        if token.tag == NN and len(token.stem) >= 2:
            run.append(token.stem)
            if token.closes_phrase:
                flush()
        elif run:
            flush()
    if run:
        flush()
    return phrases


def _contains(outer: tuple[str, ...], inner: tuple[str, ...]) -> bool:
    size = len(inner)
    return len(outer) > size and any(
        outer[i : i + size] == inner for i in range(len(outer) - size + 1)
    )


def extract_keywords(
    title: list[Token], body: list[Token], *, top_n: int = 10
) -> list[Keyword]:
    in_title = Counter(noun_phrases(title))
    counts = in_title + Counter(noun_phrases(body))

    ranked = sorted(
        (
            (
                phrase,
                count * (1 + 0.5 * (len(phrase) - 1)) * (TITLE_BOOST if phrase in in_title else 1),
                count,
            )
            for phrase, count in counts.items()
        ),
        key=lambda item: (-item[1], -len(item[0]), item[0]),
    )

    selected: list[tuple[tuple[str, ...], float, int]] = []
    for phrase, score, count in ranked:
        # A sub-phrase that never occurs outside a chosen phrase adds nothing.
        if any(_contains(kept, phrase) and count <= kept_count for kept, _, kept_count in selected):
            continue
        selected.append((phrase, score, count))
        if len(selected) == top_n:
            break

    return [Keyword(" ".join(phrase), round(score, 2), count) for phrase, score, count in selected]
