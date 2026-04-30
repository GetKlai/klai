"""
Tests for POST /api/auth/select-workspace (SPEC-AUTH-009 R4).
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
            # R4-C4.7: org_id must appear in entries (not org_ids list).
            # kind=member triggers the direct-finalize branch (C4.2).
            mock_svc.consume = AsyncMock(
                return_value={
                    "session_id": "sid",
                    "session_token": "stk",
                    "zitadel_user_id": "u1",
                    "email": "a@b.com",
                    "auth_request_id": "ar1",
                    "entries": [
                        {
                            "org_id": 1,
                            "name": "Acme",
                            "slug": "acme",
                            "kind": "member",
                            "auto_accept": False,
                        }
                    ],
                }
            )
            mock_zitadel.finalize_auth_request = AsyncMock(return_value="https://acme.getklai.com/callback")

            response = await select_workspace(body=body, db=mock_db)

        assert response.workspace_url is not None
        assert "acme" in response.workspace_url

    @pytest.mark.asyncio
    async def test_invalid_ref_returns_410(self) -> None:
        """R4-C4.8: missing/expired pending-session -> 410 Gone."""
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        mock_db = AsyncMock()
        body = SelectWorkspaceRequest(ref="invalid-ref", org_id=1)

        with (
            patch("app.api.auth_select.pending_session_svc") as mock_svc,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_svc.consume = AsyncMock(return_value=None)
            await select_workspace(body=body, db=mock_db)

        assert exc_info.value.status_code == 410

    @pytest.mark.asyncio
    async def test_org_not_in_allowed_list_returns_403(self) -> None:
        from app.api.auth_select import SelectWorkspaceRequest, select_workspace

        mock_db = AsyncMock()
        body = SelectWorkspaceRequest(ref="test-ref", org_id=99)

        with (
            patch("app.api.auth_select.pending_session_svc") as mock_svc,
            pytest.raises(HTTPException) as exc_info,
        ):
            # org_id=99 is not in entries -> R4-C4.7 returns 403
            mock_svc.consume = AsyncMock(
                return_value={
                    "session_id": "sid",
                    "session_token": "stk",
                    "zitadel_user_id": "u1",
                    "email": "a@b.com",
                    "auth_request_id": "ar1",
                    "entries": [
                        {
                            "org_id": 1,
                            "name": "Acme",
                            "slug": "acme",
                            "kind": "member",
                            "auto_accept": False,
                        },
                        {
                            "org_id": 2,
                            "name": "Beta",
                            "slug": "beta",
                            "kind": "domain_match",
                            "auto_accept": False,
                        },
                    ],
                }
            )
            await select_workspace(body=body, db=mock_db)

        assert exc_info.value.status_code == 403
