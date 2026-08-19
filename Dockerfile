FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Kathmandu

WORKDIR /app

# Dependency layer. Only pyproject.toml is copied so this layer stays cached and
# is rebuilt when a dependency changes -- not on every source edit. The stub
# package exists purely so the editable install has something to point at; the
# real source is copied over it below.
COPY pyproject.toml README.md ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir -e ".[dev]"

COPY . .

CMD ["python", "-m", "app.main", "--help"]
