# SPEC-CRAWLER-004 Fase 0: build context is the repo root so the shared
# `klai-connector-credentials` library (a path dep in pyproject.toml) is
# reachable from inside the build. Paths below are relative to the repo root.

FROM python:3.12-slim

RUN apt-get update && apt-get install -y ca-certificates libpq5 && update-ca-certificates && rm -rf /var/lib/apt/lists/*

# SEC-018 F-029: create non-root user before COPY so files are owned correctly.
RUN useradd --create-home --uid 1000 --shell /bin/bash app

WORKDIR /app

# Bring in the shared lib first — the pyproject.toml declares a path dep
# `../klai-libs/connector-credentials`. We replicate the sibling layout
# inside the image.
COPY klai-libs/connector-credentials /klai-libs/connector-credentials

COPY klai-knowledge-ingest/pyproject.toml klai-knowledge-ingest/uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY --chown=app:app klai-knowledge-ingest/knowledge_ingest/ knowledge_ingest/

# SEC-018: drop root for runtime (application writes nothing inside /app at runtime).
USER app

EXPOSE 8000

CMD ["uvicorn", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]
