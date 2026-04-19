FROM python:3.13-slim AS builder

# Install uv from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /build

# Install deps from the lockfile. Copying only pyproject + lockfile first lets
# Docker cache the dependency layer until deps actually change.
COPY pyproject.toml uv.lock ./

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.13-slim

WORKDIR /app

# Apply security patches to base image OS packages
RUN apt-get update && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copy the prebuilt virtualenv from the builder
COPY --from=builder /build/.venv /app/.venv

# Make the venv the default python on PATH
ENV PATH="/app/.venv/bin:${PATH}" \
    VIRTUAL_ENV="/app/.venv"

# Copy application code
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8010

USER klai

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
