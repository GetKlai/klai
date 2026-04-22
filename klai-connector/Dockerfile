# SPEC-CRAWLER-004 Fase 0: build context is the repo root so the shared
# `klai-connector-credentials` library (a path dep in pyproject.toml) is
# reachable from inside the build. Paths below are relative to the repo root.
#
# Switched from `pip install .` to uv so `[tool.uv.sources]` is honoured (pip
# does not read that table, so the path dep would otherwise fall through to a
# PyPI lookup that fails).

FROM python:3.12-slim AS deps

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Bring in the shared lib first — the connector's pyproject.toml declares a
# path dep `../klai-libs/connector-credentials`, so we replicate the sibling
# layout inside the image. The image copy lives at /klai-libs/... and
# pyproject.toml lives at /build/pyproject.toml, so `../klai-libs/...`
# resolves to `/klai-libs/...` correctly.
COPY klai-libs/connector-credentials /klai-libs/connector-credentials

COPY klai-connector/pyproject.toml klai-connector/uv.lock ./

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# Install system-wide so the runtime stage can pick up site-packages directly
# (preserves the previous multi-stage layout).
RUN uv sync --frozen --no-dev --no-install-project

# Stage 2: Application
FROM python:3.12-slim

WORKDIR /app

# Carry uv's venv and the shared-lib source (the venv contains editable links
# pointing to /klai-libs/connector-credentials).
COPY --from=deps /build/.venv /app/.venv
COPY --from=deps /klai-libs /klai-libs

ENV PATH="/app/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/app/.venv"

COPY klai-connector/ .

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8200

USER klai

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]
