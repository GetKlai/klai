"""RED: Verify retrieval log service (Redis-based).

SPEC-KB-015 REQ-KB-015-01/02/03/04/09:
- Write retrieval log to Redis sorted set with TTL
- Find correlated log by time-window query
- Silent error discard on Redis failure
"""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    r = AsyncMock()
    r.zadd = AsyncMock()
    r.expire = AsyncMock()
    r.zrangebyscore = AsyncMock(return_value=[])
    return r


@pytest.mark.asyncio
async def test_write_retrieval_log_stores_in_redis(mock_redis):
    """write_retrieval_log should ZADD a JSON blob with epoch timestamp as score."""
    with patch("app.services.retrieval_log.get_redis_pool", return_value=mock_redis):
        from app.services.retrieval_log import write_retrieval_log

        now = datetime.now(UTC)
        await write_retrieval_log(
            org_id=1,
            user_id="user123",
            chunk_ids=["c1", "c2"],
            reranker_scores=[0.9, 0.8],
            query_resolved="test query",
            embedding_model_version="bge-m3-v1",
            retrieved_at=now,
        )

        mock_redis.zadd.assert_called_once()
        call_args = mock_redis.zadd.call_args
        key = call_args[0][0]
        assert key == "rl:1:user123"

        # Verify TTL is set
        mock_redis.expire.assert_called_once()
        ttl_args = mock_redis.expire.call_args
        assert ttl_args[0][0] == "rl:1:user123"
        assert ttl_args[0][1] == 3600


@pytest.mark.asyncio
async def test_write_retrieval_log_silent_on_failure(mock_redis):
    """REQ-KB-015-03: Redis write failure must be silently discarded."""
    mock_redis.zadd.side_effect = Exception("Redis down")

    with patch("app.services.retrieval_log.get_redis_pool", return_value=mock_redis):
        from app.services.retrieval_log import write_retrieval_log

        # Should NOT raise
        await write_retrieval_log(
            org_id=1,
            user_id="user123",
            chunk_ids=["c1"],
            reranker_scores=[0.9],
            query_resolved="test",
            embedding_model_version="bge-m3-v1",
            retrieved_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_write_retrieval_log_silent_when_no_redis():
    """When Redis is not configured, silently do nothing."""
    with patch("app.services.retrieval_log.get_redis_pool", return_value=None):
        from app.services.retrieval_log import write_retrieval_log

        # Should NOT raise
        await write_retrieval_log(
            org_id=1,
            user_id="user123",
            chunk_ids=["c1"],
            reranker_scores=[0.9],
            query_resolved="test",
            embedding_model_version="bge-m3-v1",
            retrieved_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_find_correlated_log_selects_closest_before(mock_redis):
    """REQ-KB-015-09: Select entry closest-to-and-before message_created_at."""
    msg_time = datetime(2026, 4, 4, 12, 0, 30, tzinfo=UTC)
    msg_epoch = msg_time.timestamp()

    # Two entries in the window: one 10s before, one 5s before message time
    entry_10s = json.dumps(
        {
            "chunk_ids": ["old"],
            "reranker_scores": [0.5],
            "query_resolved": "old query",
            "embedding_model_version": "bge-m3-v1",
            "retrieved_at": (msg_epoch - 10),
        }
    )
    entry_5s = json.dumps(
        {
            "chunk_ids": ["close"],
            "reranker_scores": [0.9],
            "query_resolved": "close query",
            "embedding_model_version": "bge-m3-v1",
            "retrieved_at": (msg_epoch - 5),
        }
    )

    mock_redis.zrangebyscore = AsyncMock(return_value=[entry_10s, entry_5s])

    with patch("app.services.retrieval_log.get_redis_pool", return_value=mock_redis):
        from app.services.retrieval_log import find_correlated_log

        result = await find_correlated_log(org_id=1, user_id="user123", message_created_at=msg_time)

        assert result is not None
        assert result["chunk_ids"] == ["close"]


@pytest.mark.asyncio
async def test_find_correlated_log_returns_none_on_empty(mock_redis):
    """No entries in window → None."""
    mock_redis.zrangebyscore = AsyncMock(return_value=[])

    with patch("app.services.retrieval_log.get_redis_pool", return_value=mock_redis):
        from app.services.retrieval_log import find_correlated_log

        result = await find_correlated_log(
            org_id=1,
            user_id="user123",
            message_created_at=datetime.now(UTC),
        )
        assert result is None


@pytest.mark.asyncio
async def test_find_correlated_log_silent_on_failure(mock_redis):
    """Redis failure during correlation → return None, no exception."""
    mock_redis.zrangebyscore.side_effect = Exception("Redis down")

    with patch("app.services.retrieval_log.get_redis_pool", return_value=mock_redis):
        from app.services.retrieval_log import find_correlated_log

        result = await find_correlated_log(
            org_id=1,
            user_id="user123",
            message_created_at=datetime.now(UTC),
        )
        assert result is None
