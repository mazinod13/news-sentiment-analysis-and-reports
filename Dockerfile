FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Kathmandu

WORKDIR /app

# Dependency layer. Only the requirements files are copied, so this layer stays
# cached and is rebuilt when a dependency changes -- not on every source edit.
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

# Register the package itself. --no-deps because the layer above already
# installed everything, and tests/test_packaging.py guarantees the two lists
# agree with pyproject.toml.
RUN pip install --no-cache-dir -e . --no-deps

CMD ["python", "-m", "app.main", "--help"]
