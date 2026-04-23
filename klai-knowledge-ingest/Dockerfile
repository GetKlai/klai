# SPEC-CRAWLER-004 Fase 0: build context is the repo root and the image
# mirrors the repo layout under /repo so the path dep
# `../klai-libs/connector-credentials` resolves WITHIN the workspace.

FROM python:3.12-slim

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y ca-certificates libpq5 && update-ca-certificates && rm -rf /var/lib/apt/lists/*

# SEC-018 F-029: create non-root user before COPY so files are owned correctly.
RUN useradd --create-home --uid 1000 --shell /bin/bash app

# Repo-mirror layout
WORKDIR /repo
COPY --chown=app:app klai-libs/connector-credentials klai-libs/connector-credentials
# SPEC-KB-IMAGE-002 Fase 3: shared image-storage package path-dep
COPY --chown=app:app klai-libs/image-storage klai-libs/image-storage
COPY --chown=app:app klai-knowledge-ingest/pyproject.toml klai-knowledge-ingest/pyproject.toml
COPY --chown=app:app klai-knowledge-ingest/uv.lock klai-knowledge-ingest/uv.lock

WORKDIR /repo/klai-knowledge-ingest

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app klai-knowledge-ingest/knowledge_ingest knowledge_ingest

ENV PATH="/repo/klai-knowledge-ingest/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-knowledge-ingest/.venv"

# SEC-018: drop root for runtime (application writes nothing inside /repo at runtime).
USER app

EXPOSE 8000

CMD ["uvicorn", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]
