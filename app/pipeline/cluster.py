"""Group stored articles into stories, after ingest.

Runs as its own step rather than inside run_source: sources ingest in parallel
threads, and two outlets' versions of one event arriving in the same cycle
would each start a story. Here articles are taken oldest first, one clusterer
at a time (a Postgres advisory lock), so the first version founds the story
and later ones join it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.nlp.stories import StoryArticle, assign, story_terms
from app.settings import Settings
from app.storage import repositories as repo
from app.storage.db import session_scope

CLUSTER_BATCH = 200


@dataclass
class ClusterReport:
    clustered: int = 0
    joined: int = 0
    locked_out: bool = False

    def __str__(self) -> str:
        if self.locked_out and not self.clustered:
            return "stories: another clusterer is running, skipped"
        started = self.clustered - self.joined
        return (
            f"stories: {self.clustered} article(s) clustered -- "
            f"{self.joined} joined an existing story, {started} started a new one"
        )


def cluster_pending(settings: Settings, *, limit: int | None = None) -> ClusterReport:
    """Assign every unclustered article to a story. Commits once per batch."""
    report = ClusterReport()
    while limit is None or report.clustered < limit:
        batch = CLUSTER_BATCH if limit is None else min(CLUSTER_BATCH, limit - report.clustered)
        with session_scope(settings) as session:
            if not repo.try_lock_clustering(session):
                report.locked_out = True
                return report
            rows = repo.articles_to_cluster(session, limit=batch)
            store = repo.DbClusterStore(session)
            for row in rows:
                article = StoryArticle(
                    article_id=row.id,
                    source_id=row.source_id,
                    title=row.title,
                    lang=row.lang,
                    published_at=row.published_at,
                    terms=story_terms(row.title, row.body or row.summary),
                )
                report.joined += assign(store, article).joined
                report.clustered += 1
        if not rows:
            break
    return report
