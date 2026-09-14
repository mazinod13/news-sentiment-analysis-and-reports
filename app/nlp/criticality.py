"""Criticality grade for an article: A (most critical) to F (routine).

Terms, weights and thresholds live in config/criticality.yaml, so deciding
what counts as critical is a config change. For each matched term:

    points = tier weight x min(body hits + title_multiplier x title hits, per_term_cap)

The score is the sum of points, and the grade is the highest one whose
threshold the score reaches. When the story is about prevention, awareness or
training rather than an event, the score is multiplied down and the grade
capped -- a suicide-prevention agreement is not a suicide.

Matching is by whole word, never substring: a term matches a token's text or
its stem, and a trailing `*` makes it a prefix match. So `बम` does not fire on
बमोजिम, while `बाढी*` catches बाढीपीडित. Each token is claimed by at most one
term, the most severe, so संकटकाल is not also counted as संकट.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.nlp.nepali import normalize
from app.nlp.tokens import Token

GRADES = ("A", "B", "C", "D", "E", "F")
_PREFIX_KEY = 2


class LexiconError(ValueError):
    """The criticality lexicon is missing or malformed."""


@dataclass(frozen=True)
class Term:
    label: str
    tier: str
    weight: float
    parts: tuple[str, ...]

    def matches_at(self, tokens: list[Token], start: int) -> bool:
        if start + len(self.parts) > len(tokens):
            return False
        return all(_part_matches(part, tokens[start + i]) for i, part in enumerate(self.parts))


def _forms(token: Token) -> set[str]:
    return {token.text.lower(), token.stem.lower()}


def _part_matches(part: str, token: Token) -> bool:
    if part.endswith("*"):
        return any(form.startswith(part[:-1]) for form in _forms(token))
    return part in _forms(token)


class TermIndex:
    """Candidate terms by a token's first word, so grading does not test every
    term at every position."""

    def __init__(self, terms: tuple[Term, ...]):
        self.exact: dict[str, list[Term]] = {}
        self.prefix: dict[str, list[Term]] = {}
        for term in terms:
            first = term.parts[0]
            if first.endswith("*"):
                self.prefix.setdefault(first[:_PREFIX_KEY], []).append(term)
            else:
                self.exact.setdefault(first, []).append(term)

    def _candidates(self, token: Token) -> list[Term]:
        found: list[Term] = []
        for form in _forms(token):
            found.extend(self.exact.get(form, ()))
            found.extend(self.prefix.get(form[:_PREFIX_KEY], ()))
        return found

    def count(self, tokens: list[Token]) -> Counter[Term]:
        hits: Counter[Term] = Counter()
        position = 0
        while position < len(tokens):
            best: Term | None = None
            for term in self._candidates(tokens[position]):
                if term.matches_at(tokens, position) and (
                    best is None or (term.weight, len(term.parts)) > (best.weight, len(best.parts))
                ):
                    best = term
            if best is None:
                position += 1
            else:
                hits[best] += 1
                position += len(best.parts)
        return hits


@dataclass(frozen=True)
class Match:
    term: str
    tier: str
    count: int
    points: float


@dataclass(frozen=True)
class Criticality:
    grade: str
    score: float
    matches: tuple[Match, ...]
    dampened: bool


@dataclass(frozen=True, eq=False)
class Lexicon:
    terms: tuple[Term, ...]
    dampeners: tuple[Term, ...]
    thresholds: tuple[tuple[str, float], ...]
    per_term_cap: float
    title_multiplier: float
    dampener_multiplier: float
    dampened_max_grade: str | None
    term_index: TermIndex = field(init=False, repr=False)
    dampener_index: TermIndex = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "term_index", TermIndex(self.terms))
        object.__setattr__(self, "dampener_index", TermIndex(self.dampeners))

    def grade_for(self, score: float) -> str:
        for grade, minimum in self.thresholds:
            if score >= minimum:
                return grade
        return GRADES[-1]


def score_criticality(title: list[Token], body: list[Token], lexicon: Lexicon) -> Criticality:
    title_hits = lexicon.term_index.count(title)
    body_hits = lexicon.term_index.count(body)

    total = 0.0
    matches: list[Match] = []
    for term in title_hits.keys() | body_hits.keys():
        hits = body_hits[term] + lexicon.title_multiplier * title_hits[term]
        points = term.weight * min(hits, lexicon.per_term_cap)
        total += points
        matches.append(Match(term.label, term.tier, title_hits[term] + body_hits[term],
                             round(points, 2)))

    dampened = total > 0 and (
        bool(lexicon.dampener_index.count(title))
        or sum(lexicon.dampener_index.count(body).values()) >= 2
    )
    if dampened:
        total *= lexicon.dampener_multiplier

    total = round(total, 2)
    grade = lexicon.grade_for(total)
    if dampened and lexicon.dampened_max_grade:
        grade = max(grade, lexicon.dampened_max_grade, key=GRADES.index)
    matches.sort(key=lambda match: (-match.points, match.term))
    return Criticality(grade, total, tuple(matches), dampened)


# --- loading ------------------------------------------------------------------

_cache: dict[Path, tuple[float, Lexicon]] = {}


def load_lexicon(path: Path | str) -> Lexicon:
    """Parse the lexicon, re-reading it only when the file changes -- so an
    edit takes effect on the worker's next cycle without a restart."""
    path = Path(path)
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise LexiconError(f"{path}: cannot read ({exc})") from None
    cached = _cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LexiconError(f"{path}: invalid YAML ({exc})") from None
    lexicon = parse_lexicon(data, source=str(path))
    _cache[path] = (mtime, lexicon)
    return lexicon


def parse_lexicon(data: object, source: str = "criticality lexicon") -> Lexicon:
    def fail(message: str) -> LexiconError:
        return LexiconError(f"{source}: {message}")

    if not isinstance(data, dict):
        raise fail("expected a mapping at the top level")

    grades = data.get("grades")
    if not isinstance(grades, dict) or set(grades) != set(GRADES):
        raise fail(f"`grades` must give a threshold for each of {', '.join(GRADES)}")
    thresholds: list[tuple[str, float]] = []
    for grade in GRADES:
        value = _number(grades[grade], f"grade {grade} threshold", fail)
        if thresholds and value >= thresholds[-1][1]:
            raise fail("grade thresholds must strictly descend from A to F")
        thresholds.append((grade, value))

    tiers = data.get("tiers")
    if not isinstance(tiers, dict) or not tiers:
        raise fail("`tiers` must map tier names to {weight, terms}")
    terms: list[Term] = []
    for name, tier in tiers.items():
        if not isinstance(tier, dict):
            raise fail(f"tier {name!r} must be a mapping with weight and terms")
        weight = _positive(tier.get("weight"), f"tier {name!r} weight", fail)
        for label in _term_strings(tier.get("terms"), f"tier {name!r}", fail):
            terms.append(Term(label, str(name), weight, _parts(label, fail)))

    dampen = data.get("dampeners") or {}
    if not isinstance(dampen, dict):
        raise fail("`dampeners` must be a mapping with multiplier and terms")
    multiplier = _number(dampen.get("multiplier", 1), "dampeners.multiplier", fail)
    if not 0 < multiplier <= 1:
        raise fail("dampeners.multiplier must be greater than 0 and at most 1")
    max_grade = dampen.get("max_grade")
    if max_grade is not None and max_grade not in GRADES:
        raise fail(f"dampeners.max_grade must be one of {', '.join(GRADES)}")
    dampeners = (
        [Term(label, "dampener", 0.0, _parts(label, fail))
         for label in _term_strings(dampen.get("terms"), "dampeners", fail)]
        if dampen
        else []
    )

    return Lexicon(
        terms=tuple(terms),
        dampeners=tuple(dampeners),
        thresholds=tuple(thresholds),
        per_term_cap=_positive(data.get("per_term_cap", 3), "per_term_cap", fail),
        title_multiplier=_positive(data.get("title_multiplier", 2), "title_multiplier", fail),
        dampener_multiplier=multiplier,
        dampened_max_grade=max_grade,
    )


def _number(value: object, what: str, fail) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise fail(f"{what} must be a number, got {value!r}")
    return float(value)


def _positive(value: object, what: str, fail) -> float:
    number = _number(value, what, fail)
    if number <= 0:
        raise fail(f"{what} must be greater than 0")
    return number


def _term_strings(value: object, where: str, fail) -> list[str]:
    groups = value.values() if isinstance(value, dict) else [value]
    out: list[str] = []
    for group in groups:
        if not isinstance(group, list):
            raise fail(f"{where} terms must be a list, or a mapping of language to list")
        for item in group:
            if not isinstance(item, str) or not item.strip():
                raise fail(f"{where} has an empty or non-text term: {item!r}")
            out.append(item.strip())
    if not out:
        raise fail(f"{where} has no terms")
    return out


def _parts(label: str, fail) -> tuple[str, ...]:
    parts = tuple(normalize(label).lower().split())
    for part in parts:
        if "*" in part[:-1]:
            raise fail(f"term {label!r}: `*` is only allowed at the end of a word")
        if part.endswith("*") and len(part) - 1 < _PREFIX_KEY:
            raise fail(
                f"term {label!r}: a prefix needs at least {_PREFIX_KEY} characters before `*`"
            )
    return parts
