"""Shared test fixtures for retrieval-api tests.

SPEC-SEC-010: set required security env vars BEFORE importing the settings
module so that the ``model_validator`` does not abort test collection. The
real production deploy injects these via SOPS + Docker Compose env.
"""

from __future__ import annotations

import os

# Security env vars must be present before ``retrieval_api.config`` imports.
# retrieval-api's Settings class uses no env_prefix, so vars are the bare field
# names (matching the existing production compose convention: QDRANT_URL, TEI_URL,
# etc.). See deploy/docker-compose.yml.
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret-do-not-use-in-prod")
os.environ.setdefault("ZITADEL_ISSUER", "https://auth.test.local")
os.environ.setdefault("ZITADEL_API_AUDIENCE", "test-audience")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("RATE_LIMIT_RPM", "600")

import pytest
from fastapi.testclient import TestClient

# Default header that lets all existing TestClient requests pass through the
# SPEC-SEC-010 auth middleware without each test having to set it explicitly.
_INTERNAL_HEADER = {"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}


@pytest.fixture(autouse=True)
def _disable_rate_limit(monkeypatch):
    """Disable Redis-backed rate limiter for all tests unless a test opts in.

    The limiter fails OPEN when Redis is unreachable (REQ-4.5); patching
    :func:`check_and_increment` keeps test runs deterministic and avoids
    accidental reliance on a local Redis.
    """

    async def _always_allow(*_args, **_kwargs):
        return True, 0

    monkeypatch.setattr(
        "retrieval_api.middleware.auth.check_and_increment",
        _always_allow,
    )


@pytest.fixture
def client():
    """Synchronous test client for the FastAPI app.

    The client's default headers include ``X-Internal-Secret`` so all existing
    legacy tests (written before SPEC-SEC-010) continue to exercise the retrieve
    / chat flows without modification.
    """
    from retrieval_api.main import app

    test_client = TestClient(app)
    test_client.headers.update(_INTERNAL_HEADER)
    return test_client


@pytest.fixture
def sample_retrieve_request():
    """Minimal valid retrieve request payload."""
    return {
        "query": "What is the refund policy?",
        "org_id": "org-123",
        "scope": "org",
        "top_k": 5,
    }


@pytest.fixture
def sample_chunk():
    """A single raw chunk dict as returned by search.hybrid_search."""
    return {
        "chunk_id": "chunk-001",
        "text": "Our refund policy allows returns within 30 days.",
        "score": 0.85,
        "artifact_id": "art-001",
        "content_type": "policy",
        "context_prefix": "Refund Policy: ",
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
    }
