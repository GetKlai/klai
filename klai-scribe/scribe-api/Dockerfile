FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY . .

ENV LOG_LEVEL=INFO

RUN adduser --disabled-password --gecos "" klai && \
    mkdir -p /data/audio && chown klai:klai /data/audio

EXPOSE 8020

USER klai

# SPEC-SEC-WEBHOOK-001 REQ-1.6: --proxy-headers + allow-ips=127.0.0.1 for internal
# services. scribe-api is reached only on klai-net (portal-api BFF proxy); no
# upstream is trusted to set X-Forwarded-For. request.client.host reflects the
# TCP peer's container IP, which is the legitimate caller identity for
# service-to-service rate-limiting.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
