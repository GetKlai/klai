# SPEC-LOGGING-EXTRACT-001: build context is the repo root and the image
# mirrors the repo layout under /repo so the path dep
# `../klai-libs/log-utils` resolves WITHIN the workspace. Switched from
# `pip install .` to uv because pip does not read `[tool.uv.sources]` and
# would fall through to a PyPI lookup that fails (uv-pip-install-skips-uv-sources
# pitfall — same trap that bit klai-connector pre-fix).

FROM python:3.12-slim AS deps

# uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# Repo-mirror layout
WORKDIR /repo
COPY klai-libs/log-utils klai-libs/log-utils
COPY klai-mailer/pyproject.toml klai-mailer/pyproject.toml
COPY klai-mailer/uv.lock klai-mailer/uv.lock

WORKDIR /repo/klai-mailer

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN uv sync --frozen --no-dev --no-install-project


# Stage 2: Application
FROM python:3.12-slim

WORKDIR /repo/klai-mailer

# Venv (has editable klai-log-utils pointer) + the shared-lib source the
# editable install points at.
COPY --from=deps /repo/klai-mailer/.venv /repo/klai-mailer/.venv
COPY --from=deps /repo/klai-libs /repo/klai-libs

ENV PATH="/repo/klai-mailer/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/repo/klai-mailer/.venv"

# Application code (app/, theme/, zitadel-message-texts/ are under klai-mailer/).
COPY klai-mailer/app/ ./app/
COPY klai-mailer/theme/ ./theme/

RUN useradd --system --no-create-home mailer
USER mailer

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
