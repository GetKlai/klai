# SPEC-CRAWLER-004 Fase 0: build context is the repo root and the image
# mirrors the repo layout under /repo so the path dep
# `../klai-libs/connector-credentials` resolves WITHIN the workspace.
# Switched from `pip install .` to uv because pip does not read
# `[tool.uv.sources]` and would fall through to a PyPI lookup that fails.

FROM python:3.12-slim AS deps

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Repo-mirror layout
WORKDIR /repo
COPY klai-libs/connector-credentials klai-libs/connector-credentials
COPY klai-connector/pyproject.toml klai-connector/pyproject.toml
COPY klai-connector/uv.lock klai-connector/uv.lock

WORKDIR /repo/klai-connector

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project


# Stage 2: Application
FROM python:3.12-slim

WORKDIR /repo/klai-connector

# Venv (has editable klai-connector-credentials pointer) + the shared-lib
# source the editable install points at.
COPY --from=deps /repo/klai-connector/.venv /repo/klai-connector/.venv
COPY --from=deps /repo/klai-libs /repo/klai-libs

ENV PATH="/repo/klai-connector/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-connector/.venv"

# Application code (app/, alembic/, tests/ are all under klai-connector/).
COPY klai-connector/ .

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8200

USER klai

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]
