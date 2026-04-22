"""
Unit tests for VexaClient HTTP behavior.

Covers cases where recording-cleanup needs deterministic mapping from upstream
HTTP status to success / failure. Regression guard for the 2026-04-22
"delete_recording 404 loop" log-spam incident: if upstream has already
deleted the recording (404), the client must return True so the caller can
mark the recording as cleaned up and stop re-enqueueing it.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.vexa import VexaClient


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        request=httpx.Request("DELETE", "http://meeting-api:8080/recordings/42"),
    )


@pytest.mark.anyio
async def test_delete_recording_204_returns_true() -> None:
    client = VexaClient()
    with patch.object(client, "_http") as http:
        http.delete = AsyncMock(return_value=_response(204))
        assert await client.delete_recording(42) is True


@pytest.mark.anyio
async def test_delete_recording_404_treated_as_success() -> None:
    """Upstream already removed the recording. Caller must not re-queue."""
    client = VexaClient()
    with patch.object(client, "_http") as http:
        http.delete = AsyncMock(return_value=_response(404))
        assert await client.delete_recording(42) is True


@pytest.mark.anyio
async def test_delete_recording_500_returns_false() -> None:
    client = VexaClient()
    with patch.object(client, "_http") as http:
        http.delete = AsyncMock(return_value=_response(500))
        assert await client.delete_recording(42) is False


@pytest.mark.anyio
async def test_delete_recording_network_error_returns_false() -> None:
    client = VexaClient()
    with patch.object(client, "_http") as http:
        http.delete = AsyncMock(
            side_effect=httpx.ConnectError("boom", request=httpx.Request("DELETE", "http://x/recordings/42"))
        )
        assert await client.delete_recording(42) is False
