"""
Tests for Zitadel get_session_details helper (SPEC-AUTH-006 R4).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestGetSessionDetails:
    """get_session_details must return zitadel_user_id and email from session."""

    @pytest.mark.asyncio
    async def test_returns_user_id_and_email(self) -> None:
        from app.services.zitadel import ZitadelClient

        client = ZitadelClient.__new__(ZitadelClient)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session": {
                "factors": {
                    "user": {
                        "id": "user-abc-123",
                        "loginName": "test@acme.nl",
                    }
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        client._http = mock_http

        result = await client.get_session_details("session-id-1", "session-token-1")

        assert result["zitadel_user_id"] == "user-abc-123"
        assert result["email"] == "test@acme.nl"

    @pytest.mark.asyncio
    async def test_passes_session_token_header(self) -> None:
        from app.services.zitadel import ZitadelClient

        client = ZitadelClient.__new__(ZitadelClient)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "session": {
                "factors": {
                    "user": {
                        "id": "user-1",
                        "loginName": "a@b.com",
                    }
                }
            }
        }
        mock_resp.raise_for_status = MagicMock()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_resp)
        client._http = mock_http

        await client.get_session_details("sid", "stoken")

        mock_http.get.assert_called_once()
        call_kwargs = mock_http.get.call_args
        assert "x-zitadel-session-token" in call_kwargs.kwargs.get("headers", {})
