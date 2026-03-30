FROM python:3.12-slim

RUN apt-get update && apt-get install -y ca-certificates libpq5 && update-ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY knowledge_ingest/ knowledge_ingest/

EXPOSE 8000

CMD ["uvicorn", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]
