# Backend image. UNVERIFIED: written without Docker available; build it before relying on it.
#
# Dependencies come from requirements.lock (hash-pinned; regenerate with
#   uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes -o requirements.lock
# ) so two builds install the same packages. The application is run from source (PYTHONPATH=/app),
# not installed, so knowledge/ and migrations/ sit next to it where the CLI and Alembic expect them.

FROM python:3.12-slim-bookworm AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app PATH=/opt/venv/bin:$PATH
WORKDIR /app

COPY --from=deps /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY knowledge ./knowledge
COPY app ./app

RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000
# Liveness only (no database needed); readiness is /ready.
HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
CMD ["uvicorn", "app.api.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
