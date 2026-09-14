"""One call that turns an article into keywords and a criticality grade."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.nlp.criticality import Criticality, Lexicon, score_criticality
from app.nlp.keywords import Keyword, extract_keywords
from app.nlp.tokens import tokenize


@dataclass(frozen=True)
class Analysis:
    keywords: tuple[Keyword, ...]
    criticality: Criticality

    @property
    def grade(self) -> str:
        return self.criticality.grade

    def as_row(self) -> dict:
        """Column values for the article_analysis table."""
        return {
            "grade": self.criticality.grade,
            "score": self.criticality.score,
            "dampened": self.criticality.dampened,
            "keywords": [asdict(keyword) for keyword in self.keywords],
            "matches": [asdict(match) for match in self.criticality.matches],
        }


def analyse(title: str | None, body: str | None, lexicon: Lexicon, *, top_n: int = 10) -> Analysis:
    title_tokens = tokenize(title)
    body_tokens = tokenize(body)
    return Analysis(
        keywords=tuple(extract_keywords(title_tokens, body_tokens, top_n=top_n)),
        criticality=score_criticality(title_tokens, body_tokens, lexicon),
    )
