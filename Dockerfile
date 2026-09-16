# syntax=docker/dockerfile:1
#
# Alpine, and a virtualenv copied out of a build stage, so the runtime image
# carries no pip, no build cache and no compilers. Every dependency ships a
# musl wheel, so nothing is compiled here.
ARG PYTHON_VERSION=3.12

# --- build: dependencies into a virtualenv -----------------------------------
FROM python:${PYTHON_VERSION}-alpine AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt ./
# --only-binary=:all: fails the build loudly if a wheel is ever missing for
# musl, instead of quietly falling back to a source build that needs gcc.
RUN python -m venv /venv \
    && /venv/bin/pip install --only-binary=:all: -r requirements.txt

# --- test: build + dev tooling + the whole tree ------------------------------
#   docker compose run --rm test
FROM build AS test
ENV PATH=/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONIOENCODING=utf-8
COPY requirements-dev.txt ./
RUN /venv/bin/pip install --only-binary=:all: -r requirements-dev.txt
COPY . .
CMD ["pytest"]

# --- runtime: what actually runs (default target) ----------------------------
# The package is not pip-installed on purpose: app/settings.py resolves config/
# and certs/ relative to the source tree, so the code must stay at /app.
FROM python:${PYTHON_VERSION}-alpine AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONPATH=/app \
    PATH=/venv/bin:$PATH \
    TZ=Asia/Kathmandu \
    HEARTBEAT_FILE=/tmp/news-worker.heartbeat

WORKDIR /app

COPY --from=build /venv /venv

RUN adduser -D -u 1000 app \
    && mkdir -p /app/data \
    && chown app /app/data

COPY app ./app
COPY config ./config
COPY certs ./certs

USER app

# The service looks after itself: waits for the database, creates missing
# tables, grades and clusters what is pending, then polls on schedule.
CMD ["python", "-m", "app.main", "worker"]
