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


@pytest.mark.anyio
async def test_delete_recording_405_treated_as_success() -> None:
    """Vexa 0.12 answers 405, not 404, for the waived DELETE /recordings route.

    The route is declared in the sealed api.v1 but sits on upstream's KNOWN_GAPS
    list (issue #591), so the router never registers it and FastAPI replies 405.
    Before this was handled, recording_cleanup treated it as a failure and
    re-queued forever, logging a warning per pass — seen in production minutes
    after the 0.12 cutover.
    """
    client = VexaClient()
    with patch.object(client, "_http") as http:
        http.delete = AsyncMock(return_value=_response(405))
        assert await client.delete_recording(42) is True


@pytest.mark.anyio
async def test_client_sends_x_user_id() -> None:
    """0.12 rejects a spawn without X-User-Id (401 Invalid user identity)."""
    client = VexaClient()
    assert client._http.headers.get("X-User-Id") == VexaClient.VEXA_USER_ID
