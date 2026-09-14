"""Text -> tagged tokens, Nepali and English together.

Language is decided per word by script, not per article: Nepali bodies quote
English names and acronyms, and some outlets configured as Nepali publish in
English, so a per-article switch would mis-tag one or the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.nlp import NN, NUM, PUNC, english, nepali

_DEVA = "ऀ-ॣॱ-ॿ"
_TOKEN_RE = re.compile(
    rf"(?P<deva>[{_DEVA}]+(?:[-–][{_DEVA}]+)*)"
    r"|(?P<num>[0-9०-९]+(?:[.,:/][0-9०-९]+)*)"
    r"|(?P<latin>[A-Za-z]+(?:['’-][A-Za-z]+)*)"
    r"|(?P<punc>\S)"
)


@dataclass(frozen=True)
class Token:
    text: str
    stem: str
    tag: str
    # A Nepali case marker was stripped; the marker is where the noun phrase ends.
    closes_phrase: bool = False


def tokenize(text: str | None) -> list[Token]:
    tokens: list[Token] = []
    for match in _TOKEN_RE.finditer(nepali.normalize(text or "")):
        word, kind = match.group(), match.lastgroup
        if kind == "deva":
            tag, stem = nepali.tag(word)
            tokens.append(Token(word, stem, tag, closes_phrase=tag == NN and stem != word))
        elif kind == "latin":
            tag, stem = english.tag(word)
            tokens.append(Token(word, stem, tag))
        elif kind == "num":
            tokens.append(Token(word, word, NUM))
        else:
            tokens.append(Token(word, word, PUNC))
    return tokens
