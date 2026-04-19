"""Shared pytest fixtures for knowledge-ingest tests.

SPEC-SEC-011: ``KNOWLEDGE_INGEST_SECRET`` is now a required environment
variable — ``Settings()`` raises ``ValidationError`` when it is missing.
We set a test value before any ``knowledge_ingest`` module is imported so
``config.py``'s module-level ``settings = Settings()`` succeeds during
test collection. The real production deploy injects this via SOPS.
"""
from __future__ import annotations

import os

os.environ.setdefault("KNOWLEDGE_INGEST_SECRET", "test-secret-value-123")

# Imports below need the env var above — keep this order to allow the
# module-level ``settings = Settings()`` call in ``knowledge_ingest.config``
# to succeed under the SPEC-SEC-011 validator.
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Default header that lets existing TestClient requests pass through the
# SPEC-SEC-011 ``InternalSecretMiddleware`` without each test having to set
# it explicitly. Matches the value seeded into the process environment above.
_INTERNAL_HEADER = {"X-Internal-Secret": os.environ["KNOWLEDGE_INGEST_SECRET"]}


def _make_mock_pool():
    """Return a mock asyncpg pool that does nothing."""
    pool = MagicMock()
    pool.execute = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    pool.close = AsyncMock(return_value=None)
    return pool


@pytest.fixture
def mock_pool():
    return _make_mock_pool()


@pytest.fixture
def client(mock_pool):
    """Test client with auth middleware.

    Patches qdrant and db pool to skip startup, and injects the default
    ``X-Internal-Secret`` header so pre-existing tests (written before
    SPEC-SEC-011) continue to exercise the happy-path flows without
    modification. Auth-focused tests that must exercise the 401 path should
    build their own TestClient instead.
    """
    with patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock), \
         patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock), \
         patch("knowledge_ingest.config.settings.enrichment_enabled", False):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            c.headers.update(_INTERNAL_HEADER)
            yield c
