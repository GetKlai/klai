"""Tests for POST /partner/v1/feedback.

SPEC-API-001 TASK-010:
- Rating validation (only thumbsUp/thumbsDown)
- Feedback permission denied -> 403
- Correlated case -> quality update scheduled
- Uncorrelated case -> no quality update
- Idempotent duplicate -> 200 without new row
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from helpers import make_partner_auth


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

    auth = make_partner_auth(permissions={"chat": True, "feedback": False, "knowledge_append": False})
    req = PartnerFeedbackRequest(message_id="msg-1", rating="thumbsUp")

    with pytest.raises(HTTPException) as exc_info:
        await submit_feedback(request=req, auth=auth, db=AsyncMock())
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_correlated_feedback_schedules_quality_update():
    """Correlated feedback: retrieval log found -> quality update scheduled."""
    from app.api.partner import PartnerFeedbackRequest, submit_feedback

    auth = make_partner_auth()
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

    req = PartnerFeedbackRequest(message_id="msg-1", rating="thumbsUp")

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

    auth = make_partner_auth()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()

    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    req = PartnerFeedbackRequest(message_id="msg-2", rating="thumbsDown")

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
    assert mock_emit.call_args[1]["properties"]["correlated"] is False


@pytest.mark.asyncio
async def test_idempotent_duplicate_returns_200():
    """Duplicate message_id -> 200 without inserting new row."""
    from starlette.responses import Response as StarletteResponse

    from app.api.partner import PartnerFeedbackRequest, submit_feedback

    auth = make_partner_auth()
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="1")  # idempotency key exists

    db = AsyncMock()
    db.commit = AsyncMock()

    req = PartnerFeedbackRequest(message_id="msg-1", rating="thumbsUp")

    with patch("app.api.partner.get_redis_pool", return_value=mock_redis):
        result = await submit_feedback(request=req, auth=auth, db=db)

    assert isinstance(result, StarletteResponse)
    assert result.status_code == 200
    db.commit.assert_not_called()
