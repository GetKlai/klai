# SPEC-CRAWLER-004 Fase 0: build context is the repo root and the image
# mirrors the repo layout under /repo so the path dep
# `../../klai-libs/connector-credentials` resolves WITHIN the workspace.
# (An earlier attempt flattened the layout to /app, which broke uv's path
# normalisation: `/app/../../klai-libs/...` tried to escape the filesystem
# root.)

FROM python:3.13-slim AS builder

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Repo-mirror layout: /repo/klai-libs/..., /repo/klai-portal/backend/...
WORKDIR /repo
COPY klai-libs/connector-credentials klai-libs/connector-credentials
COPY klai-portal/backend/pyproject.toml klai-portal/backend/pyproject.toml
COPY klai-portal/backend/uv.lock klai-portal/backend/uv.lock

WORKDIR /repo/klai-portal/backend

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.13-slim

# Match builder WORKDIR so venv shebangs (which embed the absolute path
# /repo/klai-portal/backend/.venv/bin/python3.13) stay valid.
WORKDIR /repo/klai-portal/backend

# Apply security patches to base image OS packages
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Venv (contains the editable klai-connector-credentials pointer) + the
# shared lib source the editable install points at.
COPY --from=builder /repo/klai-portal/backend/.venv /repo/klai-portal/backend/.venv
COPY --from=builder /repo/klai-libs /repo/klai-libs

# Make the venv the default python on PATH
ENV PATH="/repo/klai-portal/backend/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-portal/backend/.venv"

# Application code
COPY klai-portal/backend/app app
COPY klai-portal/backend/alembic alembic
COPY klai-portal/backend/alembic.ini alembic.ini

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8010

USER klai

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
