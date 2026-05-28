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
# SPEC-TI-003 AC-6: identity-assert path-dep (X-Caller-Service header verification on ingest endpoints)
COPY --chown=app:app klai-libs/identity-assert klai-libs/identity-assert
# SPEC-RAG-PERSONAL-SCOPE-001 REQ-1: canonical personal-KB slug helper.
COPY --chown=app:app klai-libs/kb-slugs klai-libs/kb-slugs
# SPEC-CHAT-GUARDRAILS-001: shared LLM safety policy path-dep.
COPY --chown=app:app klai-libs/llm-safety klai-libs/llm-safety
COPY --chown=app:app klai-knowledge-ingest/pyproject.toml klai-knowledge-ingest/pyproject.toml
COPY --chown=app:app klai-knowledge-ingest/uv.lock klai-knowledge-ingest/uv.lock

WORKDIR /repo/klai-knowledge-ingest

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project && uv cache clean

COPY --chown=app:app klai-knowledge-ingest/knowledge_ingest knowledge_ingest
# SPEC-INGEST-ALEMBIC-001: alembic migration definitions
COPY --chown=app:app klai-knowledge-ingest/alembic alembic
COPY --chown=app:app klai-knowledge-ingest/alembic.ini alembic.ini
# Operator scripts (e.g. backfill_entity_names.py) — runnable inside the
# container via `docker exec ... python scripts/<file>` for one-shot ops.
COPY --chown=app:app klai-knowledge-ingest/scripts scripts
# SPEC-INGEST-ALEMBIC-001: entrypoint runs alembic upgrade head before uvicorn
COPY --chown=app:app klai-knowledge-ingest/entrypoint.sh entrypoint.sh
RUN chmod +x /repo/klai-knowledge-ingest/entrypoint.sh
# SPEC-SEC-WEBHOOK-001 REQ-6: shared uvicorn launcher (Caddy IP resolution +
# --proxy-headers). Copied from repo root scripts/ directory.
COPY --chown=app:app scripts/uvicorn-launch.sh /app/scripts/uvicorn-launch.sh
RUN chmod +x /app/scripts/uvicorn-launch.sh

ENV PATH="/repo/klai-knowledge-ingest/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-knowledge-ingest/.venv"

# SEC-018: drop root for runtime (application writes nothing inside /repo at runtime).
USER app

EXPOSE 8000

# SPEC-SEC-WEBHOOK-001 REQ-1.6 + REQ-6: shared launcher resolves Caddy IP at
# start time and passes --proxy-headers --forwarded-allow-ips=<resolved>.
# knowledge-ingest is internal-only (klai-net), so DNS lookup falls back to
# 127.0.0.1 — the correct safe default for an internal service.
# SPEC-INGEST-ALEMBIC-001 + SPEC-SEC-WEBHOOK-001 REQ-1.6 + REQ-6:
# entrypoint applies alembic migrations, then shared launcher resolves Caddy IP.
ENTRYPOINT ["/repo/klai-knowledge-ingest/entrypoint.sh"]
CMD ["/app/scripts/uvicorn-launch.sh", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]
