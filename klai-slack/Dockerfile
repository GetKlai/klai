# Build context is the repo root, and the image mirrors the repo layout under
# /repo so the path dependency `../klai-libs/log-utils` resolves inside the
# workspace. uv (not pip) because pip ignores [tool.uv.sources] and would look
# the shared lib up on PyPI. Same shape as klai-mailer/Dockerfile.

FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /repo
COPY klai-libs/log-utils klai-libs/log-utils
COPY klai-slack/pyproject.toml klai-slack/pyproject.toml
COPY klai-slack/uv.lock klai-slack/uv.lock

WORKDIR /repo/klai-slack

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim

WORKDIR /repo/klai-slack

# Trivy blocks the deploy on unfixed CVEs in the base image's OS packages.
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /repo/klai-slack/.venv /repo/klai-slack/.venv
COPY --from=deps /repo/klai-libs /repo/klai-libs

ENV PATH="/repo/klai-slack/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-slack/.venv"

COPY klai-slack/app/ ./app/
COPY klai-slack/alembic/ ./alembic/
COPY klai-slack/alembic.ini klai-slack/entrypoint.sh ./

RUN useradd --system --no-create-home slack
USER slack

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
