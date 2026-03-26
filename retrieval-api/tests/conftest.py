"""Shared test fixtures for retrieval-api tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Synchronous test client for the FastAPI app."""
    from retrieval_api.main import app

    return TestClient(app)


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
