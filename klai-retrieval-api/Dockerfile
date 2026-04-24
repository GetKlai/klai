FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install uv && uv pip install --system -e .

COPY retrieval_api/ ./retrieval_api/
COPY scripts/ ./scripts/

RUN adduser --disabled-password --gecos "" klai

USER klai

# SPEC-SEC-WEBHOOK-001 REQ-1.6: --proxy-headers + allow-ips=127.0.0.1 for internal
# services. retrieval-api is reached service-to-service on klai-net (portal-api,
# litellm), NOT through Caddy — so no upstream is trusted to set X-Forwarded-For.
# request.client.host then equals the TCP peer's container IP. The app-level XFF
# read in `middleware/auth.py::_source_ip` is also updated (REQ-1.5) to use
# `request.client.host` instead of the raw header.
CMD ["uvicorn", "retrieval_api.main:app", "--host", "0.0.0.0", "--port", "8040", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
