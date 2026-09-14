"""English word -> (tag, stem).

English needs no stemming for chunking: stopwords and common reporting verbs
break phrases, everything else is noun-like. This is the classic RAKE split,
and it is what English articles and English words inside Nepali text get.
"""

from __future__ import annotations

from app.nlp import FW, NN

STOPWORDS = frozenset(
    """
    a about above across after again against ago all almost along already also although always
    am among amid an and another any are around as at away back be became because become been
    before being below between both but by can could did do does doing done down during each
    either else even ever every few for from further get gets got had has have having he her
    here hers him his how however i if in including into is it its itself just last least less
    like made make many may me might more most much must my near neither never new next no nor
    not now of off often on once one only or other others our out over own per perhaps quite
    rather said same say says she should since so some still such than that the their them then
    there these they this those though through thus to too toward towards under until up upon us
    very via was we well were what when where whether which while who whom whose why will with
    within without would yet you your
    according added announced asked began called continue continued continues decided expressed
    held informed noted reported started stated taken told took urged
    monday tuesday wednesday thursday friday saturday sunday january february march april june
    july august september october november december today yesterday tomorrow week year years
    month mr mrs ms dr hrs npt
    """.split()
)


def tag(word: str) -> tuple[str, str]:
    lower = word.lower()
    if lower.endswith(("'s", "’s")):
        lower = lower[:-2]
    if len(lower) < 2 or lower in STOPWORDS:
        return FW, lower
    return NN, lower
