"""SPEC-PORTAL-KB-OWNERSHIP-001 Phase 3 — endpoint-level offboard tests.

Covers AC-5 (preview shape), AC-6 (missing dispositions → 400 with list),
AC-7 (transfer + delete + audit), AC-8 (transfer of personal KB → 400),
AC-10 (failure during disposition aborts the entire offboard tx).

Service-level invariants live in ``test_kb_offboarding_service.py``.
This file exercises the wiring in ``app.api.admin.users``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.kb_offboarding import KbDisposition, OffboardPreview, OffboardPreviewKb, UserDeletePreview
from tests.conftest import make_perms


def _user(status: str = "active") -> MagicMock:
    u = MagicMock()
    u.zitadel_user_id = "uid-leaving"
    u.status = status
    u.org_id = 1
    u.github_username = None
    return u


def _org() -> MagicMock:
    o = MagicMock()
    o.id = 1
    o.slug = "voys"
    o.zitadel_org_id = "zitadel-org-1"
    return o


def _scalar_lookup(value: object) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


class TestOffboardPreviewEndpoint:
    """AC-5: preview returns solely-owned org-KBs + all personal-KBs + token counts."""

    @pytest.mark.asyncio
    async def test_preview_returns_kbs_and_token_counts(self) -> None:
        from app.api.admin.users import offboard_preview

        user = _user()
        db = AsyncMock()
        db.execute.side_effect = [_scalar_lookup(user)]

        preview = OffboardPreview(
            org_kbs_solely_owned=[
                OffboardPreviewKb(kb_id=7, slug="team-kb", name="Team KB", owner_type="org", role_count=1),
            ],
            personal_kbs=[
                OffboardPreviewKb(
                    kb_id=11, slug="personal-uid-leaving", name="Persoonlijk", owner_type="user", role_count=1
                ),
            ],
            api_keys_count=2,
            mcp_tokens_count=3,
        )

        with patch(
            "app.api.admin.users.compute_offboard_preview",
            AsyncMock(return_value=preview),
        ) as mock_preview:
            result = await offboard_preview(
                zitadel_user_id="uid-leaving",
                perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                db=db,
            )

        assert result is preview
        mock_preview.assert_awaited_once_with("uid-leaving", 1, db)

    @pytest.mark.asyncio
    async def test_preview_404_when_user_not_in_org(self) -> None:
        from app.api.admin.users import offboard_preview

        db = AsyncMock()
        db.execute.side_effect = [_scalar_lookup(None)]

        with pytest.raises(HTTPException) as exc:
            await offboard_preview(
                zitadel_user_id="uid-ghost",
                perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                db=db,
            )
        assert exc.value.status_code == 404


class TestOffboardEndpointMissingDispositions:
    """AC-6: missing dispositions → 400 with explicit slug list."""

    @pytest.mark.asyncio
    async def test_empty_body_with_kbs_in_preview_returns_400_with_list(self) -> None:
        from app.api.admin.users import OffboardRequest, offboard_user

        user = _user()
        org = _org()

        db = AsyncMock()
        db.execute.side_effect = [_scalar_lookup(user), _scalar_lookup(org)]

        preview = OffboardPreview(
            org_kbs_solely_owned=[
                OffboardPreviewKb(kb_id=7, slug="team-kb", name="Team KB", owner_type="org", role_count=1),
            ],
            personal_kbs=[
                OffboardPreviewKb(
                    kb_id=11, slug="personal-uid-leaving", name="Persoonlijk", owner_type="user", role_count=1
                ),
            ],
            api_keys_count=0,
            mcp_tokens_count=0,
        )

        with patch("app.api.admin.users.compute_offboard_preview", AsyncMock(return_value=preview)):
            with pytest.raises(HTTPException) as exc:
                await offboard_user(
                    zitadel_user_id="uid-leaving",
                    body=OffboardRequest(kb_dispositions=[]),
                    perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                    db=db,
                )

        assert exc.value.status_code == 400
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert detail.get("error_code") == "missing_kb_dispositions"
        assert sorted(detail.get("missing", [])) == ["personal-uid-leaving", "team-kb"]


class TestOffboardEndpointHappyPath:
    """AC-7: transfer + delete + audit + token revoke + status flip."""

    @pytest.mark.asyncio
    async def test_offboard_with_full_dispositions_runs_all_steps(self) -> None:
        from app.api.admin.users import OffboardRequest, offboard_user

        user = _user()
        org = _org()
        org.zitadel_org_id = "zitadel-org-1"

        db = AsyncMock()
        # Sequence inside offboard_user before apply/revoke/membership delete:
        # 1: user lookup
        # 2: org lookup
        # 3: membership delete (rowcount=1)
        membership_delete = MagicMock()
        membership_delete.rowcount = 1
        db.execute.side_effect = [
            _scalar_lookup(user),
            _scalar_lookup(org),
            membership_delete,
        ]
        mock_zitadel = AsyncMock()

        preview = OffboardPreview(
            org_kbs_solely_owned=[
                OffboardPreviewKb(kb_id=7, slug="team-kb", name="Team KB", owner_type="org", role_count=1),
            ],
            personal_kbs=[
                OffboardPreviewKb(
                    kb_id=11, slug="personal-uid-leaving", name="Persoonlijk", owner_type="user", role_count=1
                ),
            ],
            api_keys_count=2,
            mcp_tokens_count=3,
        )

        with (
            patch("app.api.admin.users.compute_offboard_preview", AsyncMock(return_value=preview)),
            patch("app.api.admin.users.apply_dispositions", AsyncMock()) as mock_apply,
            patch(
                "app.api.admin.users.revoke_user_credentials",
                AsyncMock(return_value=(2, 3)),
            ) as mock_revoke,
            patch("app.api.admin.users.zitadel", mock_zitadel),
            patch("app.api.admin.users.settings") as mock_settings,
            patch("app.api.admin.users.log_event", AsyncMock()) as mock_log,
        ):
            mock_settings.zitadel_portal_org_id = "zitadel-portal-org-id"
            await offboard_user(
                zitadel_user_id="uid-leaving",
                body=OffboardRequest(
                    kb_dispositions=[
                        KbDisposition(kb_id=7, action="transfer", transfer_to="admin-1"),
                        KbDisposition(kb_id=11, action="delete"),
                    ]
                ),
                perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                db=db,
            )

        # Both dispositions delegated to the orchestrator.
        mock_apply.assert_awaited_once()
        # Token revoke runs in same tx.
        mock_revoke.assert_awaited_once_with(target_user_id="uid-leaving", org_id=1, db=db)
        # User status flipped + commit.
        assert user.status == "offboarded"
        db.commit.assert_awaited_once()
        # Zitadel deactivate called AFTER commit.
        mock_zitadel.deactivate_user.assert_awaited_once_with("uid-leaving", "zitadel-portal-org-id")
        # user.offboarded audit event includes the new metrics.
        offboard_calls = [c for c in mock_log.await_args_list if c.kwargs.get("action") == "user.offboarded"]
        assert len(offboard_calls) == 1
        details = offboard_calls[0].kwargs.get("details") or {}
        assert details.get("kb_dispositions_count") == 2
        assert details.get("api_keys_deleted") == 2
        assert details.get("mcp_tokens_revoked") == 3


class TestOffboardEndpointFailureRollsBack:
    """AC-10: apply_dispositions failure aborts before status flip."""

    @pytest.mark.asyncio
    async def test_apply_dispositions_failure_aborts_offboard(self) -> None:
        from app.api.admin.users import OffboardRequest, offboard_user

        user = _user()
        org = _org()

        db = AsyncMock()
        db.execute.side_effect = [_scalar_lookup(user), _scalar_lookup(org)]

        preview = OffboardPreview(
            org_kbs_solely_owned=[
                OffboardPreviewKb(kb_id=7, slug="team-kb", name="Team KB", owner_type="org", role_count=1),
            ],
            personal_kbs=[],
            api_keys_count=0,
            mcp_tokens_count=0,
        )

        with (
            patch("app.api.admin.users.compute_offboard_preview", AsyncMock(return_value=preview)),
            patch(
                "app.api.admin.users.apply_dispositions",
                AsyncMock(side_effect=RuntimeError("docs-app down")),
            ),
            patch("app.api.admin.users.revoke_user_credentials", AsyncMock()) as mock_revoke,
            patch("app.api.admin.users.zitadel", AsyncMock()) as mock_zitadel,
        ):
            with pytest.raises(RuntimeError):
                await offboard_user(
                    zitadel_user_id="uid-leaving",
                    body=OffboardRequest(
                        kb_dispositions=[KbDisposition(kb_id=7, action="transfer", transfer_to="admin-1")],
                    ),
                    perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                    db=db,
                )

        # Nothing past apply_dispositions ran.
        mock_revoke.assert_not_awaited()
        mock_zitadel.deactivate_user.assert_not_awaited()


class TestDeleteUserEndpoint:
    """Tenant-admin hard-delete with explicit KB dispositions."""

    @pytest.mark.asyncio
    async def test_delete_preview_returns_created_kbs(self) -> None:
        from app.api.admin.users import delete_user_preview

        user = _user()
        db = AsyncMock()
        db.execute.side_effect = [_scalar_lookup(user)]

        preview = UserDeletePreview(
            org_kbs_created=[
                OffboardPreviewKb(kb_id=7, slug="team-kb", name="Team KB", owner_type="org", role_count=2),
            ],
            personal_kbs=[],
            api_keys_count=1,
            mcp_tokens_count=0,
        )

        with patch("app.api.admin.users.compute_user_delete_preview", AsyncMock(return_value=preview)) as mock_preview:
            result = await delete_user_preview(
                zitadel_user_id="uid-leaving",
                perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                db=db,
            )

        assert result is preview
        mock_preview.assert_awaited_once_with("uid-leaving", 1, db)

    @pytest.mark.asyncio
    async def test_delete_with_full_dispositions_uses_state_machine(self) -> None:
        from app.api.admin.users import DeleteUserRequest, delete_user_with_dispositions
        from app.services.user_memberships import UserMembershipSummary

        user = _user()
        org = _org()
        db = AsyncMock()
        db.execute.side_effect = [_scalar_lookup(user), _scalar_lookup(org)]

        preview = UserDeletePreview(
            org_kbs_created=[
                OffboardPreviewKb(kb_id=7, slug="team-kb", name="Team KB", owner_type="org", role_count=2),
            ],
            personal_kbs=[
                OffboardPreviewKb(kb_id=11, slug="personal", name="Personal", owner_type="user", role_count=1),
            ],
            api_keys_count=2,
            mcp_tokens_count=1,
        )

        state_machine = AsyncMock(return_value=True)
        with (
            patch(
                "app.api.admin.users.get_user_membership_summary",
                AsyncMock(
                    return_value=UserMembershipSummary(total_count=1, remaining_count=0, is_platform_admin=False)
                ),
            ),
            patch("app.api.admin.users.compute_user_delete_preview", AsyncMock(return_value=preview)),
            patch("app.api.admin.users.delete_user_with_state_machine", state_machine),
            patch("app.api.admin.users.fire_role_change_notification") as notify,
        ):
            response = await delete_user_with_dispositions(
                zitadel_user_id="uid-leaving",
                body=DeleteUserRequest(
                    kb_dispositions=[
                        KbDisposition(kb_id=7, action="transfer", transfer_to="admin-1"),
                        KbDisposition(kb_id=11, action="delete"),
                    ]
                ),
                perms=make_perms(role="admin", user_id="admin-1", org_id=1),
                db=db,
            )

        state_machine.assert_awaited_once()
        call_kwargs = state_machine.await_args.kwargs
        assert call_kwargs["success_audit_action"] == "user.deleted"
        assert call_kwargs["partial_failure_audit_action"] == "user.delete_partial_failure"
        assert call_kwargs["delete_global_identity"] is True
        assert call_kwargs["kb_dispositions"][0].action == "transfer"
        db.commit.assert_awaited_once()
        notify.assert_called_once_with("uid-leaving")
        assert response.message == "User deleted."
