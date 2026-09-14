# syntax=docker/dockerfile:1

# --- base: runtime dependencies only -----------------------------------------
FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app \
    TZ=Asia/Kathmandu

# zoneinfo needs the system tz database for Asia/Kathmandu (+05:45).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copied alone so this layer is only rebuilt when a dependency changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# --- test: base + pytest/ruff + the full tree ---------------------------------
#   docker compose run --rm test
FROM base AS test
COPY requirements-dev.txt ./
RUN pip install -r requirements-dev.txt
COPY . .
CMD ["pytest"]

# --- runtime: the image that actually runs (default target) ------------------
# The package is not pip-installed on purpose: app/settings.py resolves config/
# and certs/ relative to the source tree, so the code must stay at /app.
FROM base AS runtime

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

COPY app ./app
COPY config ./config
COPY certs ./certs

USER app

CMD ["python", "-m", "app.main", "worker"]
