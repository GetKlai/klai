"""
Tests for POST /api/auth/select-workspace (SPEC-AUTH-006 R9).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


class TestSelectWorkspace:
    """POST /api/auth/select-workspace validates ref and org, then finalizes."""

    @pytest.mark.asyncio
    async def test_valid_ref_and_org_finalizes(self) -> None:
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        mock_org = MagicMock()
        mock_org.slug = "acme"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_org

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        body = SelectWorkspaceRequest(ref="test-ref-uuid", org_id=1)

        with (
            patch("app.api.auth_select.pending_session_svc") as mock_svc,
            patch("app.api.auth_select.zitadel") as mock_zitadel,
            patch("app.api.auth_select.emit_event"),
        ):
            mock_svc.consume = AsyncMock(
                return_value={
                    "session_id": "sid",
                    "session_token": "stk",
                    "zitadel_user_id": "u1",
                    "email": "a@b.com",
                    "auth_request_id": "ar1",
                    "org_ids": [1, 2],
                }
            )
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/callback")

            response = await select_workspace(body=body, db=mock_db)

        assert response.workspace_url is not None
        assert "acme" in response.workspace_url

    @pytest.mark.asyncio
    async def test_invalid_ref_returns_404(self) -> None:
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        mock_db = AsyncMock()
        body = SelectWorkspaceRequest(ref="invalid-ref", org_id=1)

        with (
            patch("app.api.auth_select.pending_session_svc") as mock_svc,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_svc.consume = AsyncMock(return_value=None)
            await select_workspace(body=body, db=mock_db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_org_not_in_allowed_list_returns_403(self) -> None:
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        mock_db = AsyncMock()
        body = SelectWorkspaceRequest(ref="test-ref", org_id=99)

        with (
            patch("app.api.auth_select.pending_session_svc") as mock_svc,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_svc.consume = AsyncMock(
                return_value={
                    "session_id": "sid",
                    "session_token": "stk",
                    "zitadel_user_id": "u1",
                    "email": "a@b.com",
                    "auth_request_id": "ar1",
                    "org_ids": [1, 2],
                }
            )
            await select_workspace(body=body, db=mock_db)

        assert exc_info.value.status_code == 403
