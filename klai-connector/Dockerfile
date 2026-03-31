# Stage 1: Dependencies
FROM python:3.12-slim AS deps

WORKDIR /build
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Stage 2: Application
FROM python:3.12-slim

WORKDIR /app
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin
COPY . .

RUN adduser --disabled-password --gecos "" klai

EXPOSE 8200

USER klai

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200"]
