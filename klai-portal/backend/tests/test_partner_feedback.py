"""RED: Verify POST /partner/v1/feedback.

SPEC-API-001 TASK-010:
- Rating validation (only thumbsUp/thumbsDown)
- Feedback permission denied -> 403
- Correlated case -> quality update scheduled
- Uncorrelated case -> no quality update
- Idempotent duplicate -> 200 without new row
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_auth(permissions: dict | None = None, kb_access: dict | None = None):
    """Create a PartnerAuthContext for testing."""
    from app.api.partner_dependencies import PartnerAuthContext

    return PartnerAuthContext(
        key_id="key-uuid-1",
        org_id=42,
        zitadel_org_id="zit-org-42",
        permissions=permissions or {"chat": True, "feedback": True, "knowledge_append": False},
        kb_access=kb_access or {10: "read"},
        rate_limit_rpm=60,
    )


@pytest.mark.asyncio
async def test_rating_validation():
    """Rating must be thumbsUp or thumbsDown — Pydantic rejects other values."""
    from pydantic import ValidationError

    from app.api.partner import PartnerFeedbackRequest

    with pytest.raises(ValidationError):
        PartnerFeedbackRequest(
            message_id="msg-1",
            rating="invalid",
        )


@pytest.mark.asyncio
async def test_feedback_permission_denied():
    """No feedback permission -> 403."""
    from app.api.partner import PartnerFeedbackRequest, submit_feedback

    auth = _make_auth(permissions={"chat": True, "feedback": False, "knowledge_append": False})
    db = AsyncMock()

    req = PartnerFeedbackRequest(
        message_id="msg-1",
        rating="thumbsUp",
    )

    with pytest.raises(HTTPException) as exc_info:
        await submit_feedback(request=req, auth=auth, db=db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_correlated_feedback_schedules_quality_update():
    """Correlated feedback: retrieval log found -> quality update scheduled."""
    from app.api.partner import PartnerFeedbackRequest, submit_feedback

    auth = _make_auth()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)  # no idempotency key
    mock_redis.set = AsyncMock()

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    correlated_log = {
        "chunk_ids": ["c1", "c2"],
        "reranker_scores": [0.9, 0.8],
        "query_resolved": "test",
        "embedding_model_version": "bge-m3-v1",
    }

    req = PartnerFeedbackRequest(
        message_id="msg-1",
        rating="thumbsUp",
    )

    with (
        patch("app.api.partner.get_redis_pool", return_value=mock_redis),
        patch("app.api.partner.find_correlated_log", return_value=correlated_log),
        patch("app.api.partner.schedule_quality_update") as mock_schedule,
        patch("app.api.partner.emit_event") as mock_emit,
    ):
        result = await submit_feedback(request=req, auth=auth, db=db)

    assert result == {"ok": True}
    mock_schedule.assert_called_once_with(["c1", "c2"], "thumbsUp", 42)
    mock_emit.assert_called_once()


@pytest.mark.asyncio
async def test_uncorrelated_feedback_no_quality_update():
    """Uncorrelated: no retrieval log -> no quality update, event still emitted."""
    from app.api.partner import PartnerFeedbackRequest, submit_feedback

    auth = _make_auth()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    req = PartnerFeedbackRequest(
        message_id="msg-2",
        rating="thumbsDown",
    )

    with (
        patch("app.api.partner.get_redis_pool", return_value=mock_redis),
        patch("app.api.partner.find_correlated_log", return_value=None),
        patch("app.api.partner.schedule_quality_update") as mock_schedule,
        patch("app.api.partner.emit_event") as mock_emit,
    ):
        result = await submit_feedback(request=req, auth=auth, db=db)

    assert result == {"ok": True}
    mock_schedule.assert_not_called()
    mock_emit.assert_called_once()
    emit_kwargs = mock_emit.call_args[1]
    assert emit_kwargs["properties"]["correlated"] is False


@pytest.mark.asyncio
async def test_idempotent_duplicate_returns_200():
    """Duplicate message_id -> 200 without inserting new row."""
    from app.api.partner import PartnerFeedbackRequest, submit_feedback

    auth = _make_auth()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="1")  # idempotency key exists

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    req = PartnerFeedbackRequest(
        message_id="msg-1",
        rating="thumbsUp",
    )

    with (
        patch("app.api.partner.get_redis_pool", return_value=mock_redis),
    ):
        from starlette.responses import Response as StarletteResponse

        result = await submit_feedback(request=req, auth=auth, db=db)
        assert isinstance(result, StarletteResponse)
        assert result.status_code == 200

    # No DB write
    db.commit.assert_not_called()
