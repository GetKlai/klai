"""RED: Verify Qdrant quality score update logic.

SPEC-KB-015 REQ-KB-015-14/15/17/18:
- Running average formula: (old * count + signal) / (count + 1)
- Silent skip for missing chunks
- Silent discard on Qdrant errors
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest


def _make_response(status_code: int, json_data: dict) -> httpx.Response:
    """Create a proper httpx Response with a request attached."""
    resp = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://qdrant:6333/test"),
    )
    return resp


@pytest.fixture
def mock_httpx_client():
    client = AsyncMock()
    return client


@pytest.mark.asyncio
async def test_first_thumbs_up_on_neutral_chunk():
    """(0.5 * 0 + 1.0) / (0 + 1) = 1.0"""
    points_response = _make_response(
        200,
        {
            "result": [
                {
                    "id": "chunk-1",
                    "payload": {"quality_score": 0.5, "feedback_count": 0},
                }
            ]
        },
    )
    set_payload_response = _make_response(200, {"status": "ok"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[points_response, set_payload_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.quality_scorer.httpx.AsyncClient", return_value=mock_client),
        patch("app.services.quality_scorer.settings") as mock_settings,
    ):
        mock_settings.qdrant_url = "http://qdrant:6333"
        mock_settings.qdrant_collection = "klai_knowledge"

        from app.services.quality_scorer import apply_quality_score

        await apply_quality_score(["chunk-1"], "thumbsUp", 1)

    set_call = mock_client.post.call_args_list[1]
    body = set_call.kwargs.get("json") or set_call[1].get("json")
    assert body["payload"]["quality_score"] == pytest.approx(1.0)
    assert body["payload"]["feedback_count"] == 1


@pytest.mark.asyncio
async def test_first_thumbs_down_on_neutral_chunk():
    """(0.5 * 0 + 0.0) / (0 + 1) = 0.0"""
    points_response = _make_response(
        200,
        {
            "result": [
                {
                    "id": "chunk-1",
                    "payload": {"quality_score": 0.5, "feedback_count": 0},
                }
            ]
        },
    )
    set_payload_response = _make_response(200, {"status": "ok"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[points_response, set_payload_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.quality_scorer.httpx.AsyncClient", return_value=mock_client),
        patch("app.services.quality_scorer.settings") as mock_settings,
    ):
        mock_settings.qdrant_url = "http://qdrant:6333"
        mock_settings.qdrant_collection = "klai_knowledge"

        from app.services.quality_scorer import apply_quality_score

        await apply_quality_score(["chunk-1"], "thumbsDown", 1)

    set_call = mock_client.post.call_args_list[1]
    body = set_call.kwargs.get("json") or set_call[1].get("json")
    assert body["payload"]["quality_score"] == pytest.approx(0.0)
    assert body["payload"]["feedback_count"] == 1


@pytest.mark.asyncio
async def test_thumbs_down_on_positive_chunk():
    """(0.75 * 3 + 0.0) / (3 + 1) = 0.5625"""
    points_response = _make_response(
        200,
        {
            "result": [
                {
                    "id": "chunk-1",
                    "payload": {"quality_score": 0.75, "feedback_count": 3},
                }
            ]
        },
    )
    set_payload_response = _make_response(200, {"status": "ok"})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=[points_response, set_payload_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.quality_scorer.httpx.AsyncClient", return_value=mock_client),
        patch("app.services.quality_scorer.settings") as mock_settings,
    ):
        mock_settings.qdrant_url = "http://qdrant:6333"
        mock_settings.qdrant_collection = "klai_knowledge"

        from app.services.quality_scorer import apply_quality_score

        await apply_quality_score(["chunk-1"], "thumbsDown", 1)

    set_call = mock_client.post.call_args_list[1]
    body = set_call.kwargs.get("json") or set_call[1].get("json")
    assert body["payload"]["quality_score"] == pytest.approx(0.5625)
    assert body["payload"]["feedback_count"] == 4


@pytest.mark.asyncio
async def test_missing_chunk_silently_skipped():
    """REQ-KB-015-17: Missing chunk_id -> skip silently."""
    points_response = _make_response(200, {"result": []})

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=points_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.quality_scorer.httpx.AsyncClient", return_value=mock_client),
        patch("app.services.quality_scorer.settings") as mock_settings,
    ):
        mock_settings.qdrant_url = "http://qdrant:6333"
        mock_settings.qdrant_collection = "klai_knowledge"

        from app.services.quality_scorer import apply_quality_score

        await apply_quality_score(["nonexistent-chunk"], "thumbsUp", 1)

    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_qdrant_unreachable_silent_discard():
    """REQ-KB-015-18: Qdrant unreachable → silent discard."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.services.quality_scorer.httpx.AsyncClient", return_value=mock_client),
        patch("app.services.quality_scorer.settings") as mock_settings,
    ):
        mock_settings.qdrant_url = "http://qdrant:6333"
        mock_settings.qdrant_collection = "klai_knowledge"

        from app.services.quality_scorer import apply_quality_score

        # Should NOT raise
        await apply_quality_score(["chunk-1"], "thumbsUp", 1)
