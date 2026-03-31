FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install uv && uv pip install --system -e .

COPY retrieval_api/ ./retrieval_api/
COPY scripts/ ./scripts/

RUN adduser --disabled-password --gecos "" klai

USER klai

CMD ["uvicorn", "retrieval_api.main:app", "--host", "0.0.0.0", "--port", "8040"]
