"""Regression-guard for the SPEC-SEC-IDENTITY-ASSERT-001 silent-degradation incident.

Phase D landed 2026-04-28 and made `X-Caller-Service` REQUIRED on every
retrieval-api `/retrieve` call. The research-api `_auth_headers()` helper
was never updated, so `retrieve_narrow()` silently returned [] for every
focus notebook for 7 days.

These tests assert that the helper now emits the header and that
`retrieve_narrow` propagates it onto the outbound call. See
`pitfalls/process-rules.md` -> `retrieve-caller-service-header-mismatch`.
"""

from __future__ import annotations

import os

# Settings() is called at module-import time inside app.core.config; satisfy
# the required fields with placeholder values BEFORE importing anything that
# transitively pulls Settings in.
os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("RETRIEVAL_API_URL", "http://retrieval-api:8040")
os.environ.setdefault("RETRIEVAL_API_INTERNAL_SECRET", "test-secret")
os.environ.setdefault("ZITADEL_API_AUDIENCE", "test-audience")

from unittest.mock import MagicMock

import pytest


def test_auth_headers_includes_caller_service(monkeypatch):
    """_auth_headers() emits X-Caller-Service: research-api when secret is set."""
    from app.services import retrieval_client as mod

    fake_settings = MagicMock()
    fake_settings.retrieval_api_internal_secret = "test-secret"
    monkeypatch.setattr(mod, "settings", fake_settings)

    headers = mod._auth_headers()

    assert headers.get("X-Internal-Secret") == "test-secret"
    assert headers.get("X-Caller-Service") == "research-api", (
        "X-Caller-Service header missing — retrieval-api 400s and the "
        "caller silently returns []. See pitfalls."
    )


def test_auth_headers_empty_when_no_secret(monkeypatch):
    """No secret configured -> empty dict, NEVER half-set headers."""
    from app.services import retrieval_client as mod

    fake_settings = MagicMock()
    fake_settings.retrieval_api_internal_secret = ""
    monkeypatch.setattr(mod, "settings", fake_settings)

    assert mod._auth_headers() == {}


@pytest.mark.asyncio
async def test_retrieve_narrow_sends_caller_service(monkeypatch):
    """retrieve_narrow() forwards the X-Caller-Service header to httpx."""
    from app.services import retrieval_client as mod

    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"chunks": []}

    class _Client:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _Resp()

    monkeypatch.setattr(mod.httpx, "AsyncClient", _Client)

    fake_settings = MagicMock()
    fake_settings.retrieval_api_url = "http://retrieval-api:8040"
    fake_settings.retrieval_api_internal_secret = "test-secret"
    monkeypatch.setattr(mod, "settings", fake_settings)

    out = await mod.retrieve_narrow(
        question="hello",
        notebook_id="nb-1",
        tenant_id="tenant-1",
    )

    assert out == []
    assert captured["headers"].get("X-Caller-Service") == "research-api"
    assert captured["headers"].get("X-Internal-Secret") == "test-secret"
