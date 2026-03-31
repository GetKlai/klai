FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .


FROM python:3.12-slim

WORKDIR /app

RUN pip install --upgrade pip

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8010

USER klai

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8010"]
