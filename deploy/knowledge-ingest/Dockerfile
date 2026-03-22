FROM python:3.12-slim

RUN apt-get update && apt-get install -y ca-certificates && update-ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY knowledge_ingest/ knowledge_ingest/

EXPOSE 8000

CMD ["uvicorn", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]
