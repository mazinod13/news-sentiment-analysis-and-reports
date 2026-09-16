"""Grade stored articles: keywords and A-F criticality for rows that have none.

Ingest grades each article as it stores it; this catches up on the rest --
articles stored while the lexicon was broken, or before grading existed. The
worker runs it automatically; `analyse --missing` / `--all` run it by hand.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.nlp.analysis import analyse
from app.nlp.criticality import GRADES, load_lexicon
from app.settings import NPT, Settings
from app.storage import repositories as repo
from app.storage.db import session_scope

GRADE_BATCH = 200


@dataclass
class GradeReport:
    graded: int = 0
    grades: Counter[str] = field(default_factory=Counter)

    def __str__(self) -> str:
        summary = ", ".join(f"{g} {self.grades[g]}" for g in GRADES if self.grades[g])
        return f"grades: {self.graded} article(s) analysed" + (f" ({summary})" if summary else "")


def grade_stored(
    settings: Settings,
    *,
    only_missing: bool = True,
    limit: int | None = None,
    top_n: int = 10,
    on_batch: Callable[[], None] | None = None,
) -> GradeReport:
    """Analyse stored articles in id order, committing once per batch.

    Raises LexiconError when the lexicon is unusable; callers decide whether
    that is fatal. `on_batch` runs after each committed batch.
    """
    lexicon = load_lexicon(settings.criticality_file)
    report = GradeReport()
    analysed_at = datetime.now(NPT)
    after_id = 0
    while limit is None or report.graded < limit:
        batch = GRADE_BATCH if limit is None else min(GRADE_BATCH, limit - report.graded)
        with session_scope(settings) as session:
            rows = repo.articles_to_analyse(
                session, after_id=after_id, limit=batch, only_missing=only_missing
            )
            for article_id, title, text in rows:
                analysis = analyse(title, text, lexicon, top_n=top_n)
                repo.save_analysis(session, article_id, analysis, analysed_at=analysed_at)
                report.grades[analysis.grade] += 1
        if not rows:
            break
        report.graded += len(rows)
        after_id = rows[-1][0]
        if on_batch:
            on_batch()
    return report
