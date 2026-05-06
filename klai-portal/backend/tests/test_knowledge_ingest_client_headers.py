"""Contract test for SPEC-TI-010C B-8: portal-api must send X-Caller-Service to knowledge-ingest.

If portal-api stops sending X-Caller-Service, both /ingest/v1/source-count and
/ingest/v1/graph-stats return 403 and the KB dashboard shows null stats silently.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("ZITADEL_JWKS_URL", "https://zitadel.test/.well-known/jwks.json")
os.environ.setdefault("ZITADEL_ISSUER", "https://zitadel.test")
os.environ.setdefault("ZITADEL_PROJECT_ID", "test-project")
os.environ.setdefault("INTERNAL_SECRET", "portal-internal-secret-test")
os.environ.setdefault("MONEYBIRD_WEBHOOK_TOKEN", "test-moneybird-webhook-token")


def test_knowledge_ingest_client_sends_caller_service_header(monkeypatch):
    """_ingest_headers() must include X-Caller-Service: portal-api.

    SPEC-TI-010C B-8: without this header, both stats endpoints return 403
    and the KB dashboard shows null stats silently.
    """
    from app.services import knowledge_ingest_client as mod

    fake_settings = MagicMock()
    fake_settings.knowledge_ingest_secret = "ingest-secret"
    monkeypatch.setattr(mod, "settings", fake_settings)
    monkeypatch.setattr(mod, "get_trace_headers", lambda: {})

    headers = mod._ingest_headers()

    assert headers.get("X-Caller-Service") == "portal-api", (
        "SPEC-TI-010C B-8: knowledge_ingest_client._ingest_headers() must set "
        "X-Caller-Service: portal-api — without it both stats endpoints return 403"
    )
    assert headers.get("X-Internal-Secret") == "ingest-secret"
