import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


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
    """Test client with auth middleware. Patches qdrant and db pool to skip startup."""
    with patch("knowledge_ingest.qdrant_store.ensure_collection", new_callable=AsyncMock), \
         patch("knowledge_ingest.db.get_pool", new_callable=AsyncMock, return_value=mock_pool), \
         patch("knowledge_ingest.db.close_pool", new_callable=AsyncMock):
        from knowledge_ingest.app import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
