import pytest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Test client with auth middleware. Patches qdrant to skip startup."""
    with patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
