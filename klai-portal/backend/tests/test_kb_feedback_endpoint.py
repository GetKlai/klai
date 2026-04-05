"""RED: Verify POST /internal/v1/kb-feedback endpoint.

SPEC-KB-015 REQ-KB-015-08 through REQ-KB-015-14, REQ-KB-015-22:
- Resolve librechat_tenant_id -> org_id
- 404 for unknown tenant
- Idempotency on (message_id, conversation_id)
- Time-window correlation with retrieval log
- Product event emission
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_db():
    """Mock async DB session."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_org():
    """Mock PortalOrg."""
    org = MagicMock()
    org.id = 42
    org.zitadel_org_id = "zit-org-1"
    org.librechat_container = "tenant-abc"
    return org


def _mock_result(value):
    """Create a mock SQLAlchemy result that returns value from scalar_one_or_none."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_unknown_tenant_returns_404():
    """REQ-KB-015-11: Unknown librechat_tenant_id -> 404."""
    from app.api.internal import KbFeedbackIn, post_kb_feedback

    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer test-secret"}

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_mock_result(None))

    body = KbFeedbackIn(
        conversation_id="conv-1",
        message_id="msg-1",
        message_created_at=datetime.now(UTC),
        rating="thumbsUp",
        librechat_user_id="user-abc",
        librechat_tenant_id="unknown-tenant",
    )

    with patch("app.api.internal._require_internal_token"):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await post_kb_feedback(body=body, request=mock_request, db=mock_db)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_idempotent_duplicate_returns_200(mock_org):
    """REQ-KB-015-12: Duplicate (message_id, conversation_id) -> 200."""
    from app.api.internal import KbFeedbackIn, post_kb_feedback

    mock_request = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="1")  # idempotency key exists

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_mock_result(mock_org))

    body = KbFeedbackIn(
        conversation_id="conv-1",
        message_id="msg-1",
        message_created_at=datetime.now(UTC),
        rating="thumbsUp",
        librechat_user_id="user-abc",
        librechat_tenant_id="tenant-abc",
    )

    with (
        patch("app.api.internal._require_internal_token"),
        patch("app.api.internal.get_redis_pool", return_value=mock_redis),
        patch("app.api.internal.set_tenant"),
    ):
        from starlette.responses import Response as StarletteResponse

        result = await post_kb_feedback(body=body, request=mock_request, db=mock_db)
        assert isinstance(result, StarletteResponse)
        assert result.status_code == 200


@pytest.mark.asyncio
async def test_successful_correlated_feedback(mock_org):
    """Correlated feedback: retrieval log found, Qdrant update scheduled, event emitted."""
    from app.api.internal import KbFeedbackIn, post_kb_feedback

    mock_request = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)  # no idempotency key
    mock_redis.set = AsyncMock()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_mock_result(mock_org))
    mock_db.commit = AsyncMock()

    correlated_log = {
        "chunk_ids": ["c1", "c2"],
        "reranker_scores": [0.9, 0.8],
        "query_resolved": "test",
        "embedding_model_version": "bge-m3-v1",
    }

    body = KbFeedbackIn(
        conversation_id="conv-1",
        message_id="msg-1",
        message_created_at=datetime.now(UTC),
        rating="thumbsUp",
        librechat_user_id="user-abc",
        librechat_tenant_id="tenant-abc",
    )

    with (
        patch("app.api.internal._require_internal_token"),
        patch("app.api.internal.get_redis_pool", return_value=mock_redis),
        patch("app.api.internal.set_tenant"),
        patch("app.api.internal.find_correlated_log", return_value=correlated_log),
        patch("app.api.internal.emit_event") as mock_emit,
        patch("app.api.internal.schedule_quality_update") as mock_schedule,
    ):
        await post_kb_feedback(body=body, request=mock_request, db=mock_db)

        # DB insert happened (raw SQL)
        mock_db.execute.assert_called()
        mock_db.commit.assert_called()

        # Quality update scheduled
        mock_schedule.assert_called_once_with(["c1", "c2"], "thumbsUp", 42)

        # Product event emitted
        mock_emit.assert_called_once()
        emit_args = mock_emit.call_args
        assert emit_args[0][0] == "knowledge.feedback"
        assert emit_args[1]["org_id"] == 42

        # Idempotency key set in Redis
        mock_redis.set.assert_called_once()


@pytest.mark.asyncio
async def test_uncorrelated_feedback(mock_org):
    """Uncorrelated: no retrieval log found, stored with correlated=False, no Qdrant update."""
    from app.api.internal import KbFeedbackIn, post_kb_feedback

    mock_request = MagicMock()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=_mock_result(mock_org))
    mock_db.commit = AsyncMock()

    body = KbFeedbackIn(
        conversation_id="conv-2",
        message_id="msg-2",
        message_created_at=datetime.now(UTC),
        rating="thumbsDown",
        librechat_user_id="user-abc",
        librechat_tenant_id="tenant-abc",
    )

    with (
        patch("app.api.internal._require_internal_token"),
        patch("app.api.internal.get_redis_pool", return_value=mock_redis),
        patch("app.api.internal.set_tenant"),
        patch("app.api.internal.find_correlated_log", return_value=None),
        patch("app.api.internal.emit_event") as mock_emit,
        patch("app.api.internal.schedule_quality_update") as mock_schedule,
    ):
        await post_kb_feedback(body=body, request=mock_request, db=mock_db)

        # Quality update NOT scheduled (uncorrelated)
        mock_schedule.assert_not_called()

        # Event still emitted
        mock_emit.assert_called_once()
        emit_kwargs = mock_emit.call_args[1]
        assert emit_kwargs["properties"]["correlated"] is False
        assert emit_kwargs["properties"]["chunk_count"] == 0
