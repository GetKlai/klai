# SPEC-SEC-INTERNAL-001 B4: build context is the repo root and the image
# mirrors the repo layout under /repo so the path dep
# `../../klai-libs/log-utils` resolves WITHIN the workspace. Same pattern
# as klai-knowledge-mcp / klai-connector / klai-portal/backend. Switched
# from `pip install` to uv because pip does not read [tool.uv.sources]
# and would fall through to a PyPI lookup that fails for klai-log-utils.

FROM python:3.12-slim AS deps

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Repo-mirror layout: /repo/klai-libs/..., /repo/klai-scribe/scribe-api/...
WORKDIR /repo
COPY klai-libs/log-utils klai-libs/log-utils
COPY klai-scribe/scribe-api/pyproject.toml klai-scribe/scribe-api/pyproject.toml
COPY klai-scribe/scribe-api/uv.lock klai-scribe/scribe-api/uv.lock

WORKDIR /repo/klai-scribe/scribe-api

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project


# Stage 2: Application
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /repo/klai-scribe/scribe-api

# Venv (has the editable klai-log-utils pointer) + the shared-lib source
# the editable install points at.
COPY --from=deps /repo/klai-scribe/scribe-api/.venv /repo/klai-scribe/scribe-api/.venv
COPY --from=deps /repo/klai-libs /repo/klai-libs

ENV PATH="/repo/klai-scribe/scribe-api/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-scribe/scribe-api/.venv" \
    LOG_LEVEL=INFO

# Application code
COPY klai-scribe/scribe-api/app app
COPY klai-scribe/scribe-api/alembic alembic
COPY klai-scribe/scribe-api/alembic.ini alembic.ini
# SPEC-SEC-WEBHOOK-001 REQ-6: shared uvicorn launcher (Caddy IP resolution +
# --proxy-headers). Copied from repo root scripts/ directory.
COPY scripts/uvicorn-launch.sh /app/scripts/uvicorn-launch.sh
RUN chmod +x /app/scripts/uvicorn-launch.sh
# SPEC-SEC-AUDIT-2026-04 C5 / SPEC-SEC-HYGIENE-001: container entrypoint runs
# alembic upgrade head before starting uvicorn so that new migrations are
# applied automatically on every deploy. Mirrors the portal-api pattern.
# See scribe-deploy-no-alembic pitfall in .claude/rules/klai/pitfalls/process-rules.md.
COPY klai-scribe/scribe-api/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

RUN adduser --disabled-password --gecos "" klai && \
    mkdir -p /data/audio && chown klai:klai /data/audio

EXPOSE 8020

USER klai

# SPEC-SEC-AUDIT-2026-04 C5: entrypoint.sh runs alembic upgrade head then
# delegates to the shared uvicorn-launch.sh (Caddy IP resolution +
# --proxy-headers). No CMD needed — entrypoint.sh is the sole startup path.
ENTRYPOINT ["/app/entrypoint.sh"]
