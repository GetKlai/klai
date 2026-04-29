# SPEC-SEC-IDENTITY-ASSERT-001 Phase B: build context is the repo root and the
# image mirrors the repo layout under /repo so the path dep
# `../klai-libs/identity-assert` resolves WITHIN the workspace. Same pattern
# as klai-knowledge-ingest with klai-libs/image-storage.

FROM python:3.12-slim

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Non-root user (created before COPY so file ownership is right from the start)
RUN useradd --create-home --uid 1000 --shell /bin/bash app

# Repo-mirror layout — klai-libs path-deps must resolve from the workspace root
WORKDIR /repo
COPY --chown=app:app klai-libs/identity-assert klai-libs/identity-assert
COPY --chown=app:app klai-knowledge-mcp/pyproject.toml klai-knowledge-mcp/pyproject.toml
COPY --chown=app:app klai-knowledge-mcp/uv.lock klai-knowledge-mcp/uv.lock

WORKDIR /repo/klai-knowledge-mcp

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=app:app klai-knowledge-mcp/logging_setup.py logging_setup.py
COPY --chown=app:app klai-knowledge-mcp/main.py main.py

ENV PATH="/repo/klai-knowledge-mcp/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-knowledge-mcp/.venv"

USER app

EXPOSE 8080

CMD ["python", "main.py"]
