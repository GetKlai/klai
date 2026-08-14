# Build context is the repo root so the `../klai-libs/log-utils` path dep
# resolves within the workspace (uv-pip-install-skips-uv-sources pitfall).
FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /repo
COPY klai-libs/log-utils klai-libs/log-utils
COPY klai-docker-authz/pyproject.toml klai-docker-authz/pyproject.toml
COPY klai-docker-authz/uv.lock klai-docker-authz/uv.lock

WORKDIR /repo/klai-docker-authz
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim AS runtime

COPY --from=deps /repo /repo
WORKDIR /repo/klai-docker-authz
COPY klai-docker-authz/app app

ENV PATH="/repo/klai-docker-authz/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Non-root: this process sits in front of the Docker API, so it is the last
# container that should be easy to pivot from.
RUN useradd --system --uid 10001 authz
USER authz

EXPOSE 2375 2376
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:2375/health').status==200 else 1)"

CMD ["python", "-m", "app.main"]
