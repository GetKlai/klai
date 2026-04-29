# SPEC-SEC-IDENTITY-ASSERT-001 Phase D: build context is the repo root and
# the image mirrors the repo layout under /repo so the path-dep
# `../klai-libs/identity-assert` resolves WITHIN the container too. Same
# pattern as klai-knowledge-mcp (Phase B) and klai-knowledge-ingest.

FROM python:3.12-slim

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Non-root user (created before COPY so file ownership is right from the start)
RUN useradd --create-home --uid 1000 --shell /bin/bash app

# Repo-mirror layout — klai-libs path-deps must resolve from the workspace root
WORKDIR /repo
COPY --chown=app:app klai-libs/identity-assert klai-libs/identity-assert
COPY --chown=app:app klai-retrieval-api/pyproject.toml klai-retrieval-api/pyproject.toml

WORKDIR /repo/klai-retrieval-api

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# `uv pip install -e .` instead of `uv sync --frozen` because retrieval-api
# does not commit a uv.lock today (existing convention); we keep the existing
# Dockerfile resolution behaviour and only add the klai-libs source layout.
RUN uv pip install --system -e .

COPY --chown=app:app klai-retrieval-api/retrieval_api retrieval_api
COPY --chown=app:app klai-retrieval-api/scripts scripts
# SPEC-SEC-WEBHOOK-001 REQ-6: shared uvicorn launcher (Caddy IP resolution +
# --proxy-headers). Copied from repo root scripts/ directory.
COPY --chown=app:app scripts/uvicorn-launch.sh /app/scripts/uvicorn-launch.sh
RUN chmod +x /app/scripts/uvicorn-launch.sh

USER app

# SPEC-SEC-WEBHOOK-001 REQ-1.6 + REQ-6: shared launcher resolves Caddy IP at
# start time and passes --proxy-headers --forwarded-allow-ips=<resolved>.
# retrieval-api is reached service-to-service on klai-net (portal-api, litellm),
# NOT through Caddy, so the DNS lookup will fall back to 127.0.0.1 — which is
# the correct safe default for an internal service.
CMD ["/app/scripts/uvicorn-launch.sh", "retrieval_api.main:app", "--host", "0.0.0.0", "--port", "8040"]
