"""Read-only HTTP API over the stored corpus: articles, grades, stories, sources.

Starlette rather than FastAPI on purpose: FastAPI's Pydantic v2 core is larger
than every other dependency in this image put together, and the payloads here
are plain dicts built from database rows.
"""
