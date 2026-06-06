"""SPEC-PORTAL-KB-OWNERSHIP-001 Phase 3 — service-level offboarding tests.

Covers ``app.services.kb_offboarding`` directly, with mocked DB. The
endpoint-level tests live in ``test_admin_offboard_endpoint.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services.kb_offboarding import (
    KbDisposition,
    apply_dispositions,
    compute_user_delete_preview,
    revoke_user_credentials,
)


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = 101
    org.slug = "voys"
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _make_kb(
    *, kb_id: int = 7, owner_type: str = "org", created_by: str = "uid-leaving", slug: str = "team-kb"
) -> MagicMock:
    kb = MagicMock()
    kb.id = kb_id
    kb.org_id = 101
    kb.name = f"KB {kb_id}"
    kb.slug = slug
    kb.owner_type = owner_type
    kb.owner_user_id = None if owner_type == "org" else created_by
    kb.created_by = created_by
    kb.gitea_repo_slug = None
    kb.docs_enabled = False
    kb.default_org_role = None
    return kb


def _make_user(*, zitadel_id: str, status: str = "active", pk: int = 99) -> MagicMock:
    u = MagicMock()
    u.zitadel_user_id = zitadel_id
    u.id = pk
    u.org_id = 101
    u.status = status
    return u


def _scalar_result(value: object) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    r.scalar_one.return_value = value
    return r


def _scalars_result(values: list[object]) -> MagicMock:
    r = MagicMock()
    r.scalars.return_value.all.return_value = values
    return r


class TestKbDispositionValidation:
    """REQ-2.x body schema enforces the transfer/delete invariants."""

    def test_transfer_without_transfer_to_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KbDisposition(kb_id=1, action="transfer")

    def test_delete_with_transfer_to_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KbDisposition(kb_id=1, action="delete", transfer_to="uid-x")

    def test_transfer_with_transfer_to_accepted(self) -> None:
        d = KbDisposition(kb_id=1, action="transfer", transfer_to="uid-new")
        assert d.transfer_to == "uid-new"

    def test_delete_without_transfer_to_accepted(self) -> None:
        d = KbDisposition(kb_id=1, action="delete")
        assert d.transfer_to is None


class TestUserDeletePreview:
    """Delete preview includes every org KB created by the user, not only sole-owner KBs."""

    @pytest.mark.asyncio
    async def test_delete_preview_lists_all_created_org_kbs(self) -> None:
        owned = _make_kb(kb_id=7, owner_type="org", created_by="uid-leaving", slug="owned")
        co_owned = _make_kb(kb_id=8, owner_type="org", created_by="uid-leaving", slug="co-owned")
        personal = _make_kb(kb_id=11, owner_type="user", created_by="uid-leaving", slug="personal")

        db = AsyncMock()
        db.execute.side_effect = [
            _scalars_result([owned, co_owned]),
            _scalar_result(1),
            _scalar_result(2),
            _scalars_result([personal]),
            _scalar_result(4),
            _scalar_result(99),
            _scalar_result(5),
        ]

        preview = await compute_user_delete_preview("uid-leaving", 101, db)

        assert [kb.slug for kb in preview.org_kbs_created] == ["owned", "co-owned"]
        assert [kb.slug for kb in preview.personal_kbs] == ["personal"]
        assert preview.api_keys_count == 4
        assert preview.mcp_tokens_count == 5


class TestApplyDispositionsTransfer:
    """REQ-2.3 — transfer changes created_by, deletes old grant, upserts new owner row."""

    @pytest.mark.asyncio
    async def test_transfer_org_kb_updates_created_by_and_emits_audit(self) -> None:
        kb = _make_kb(kb_id=7, owner_type="org", created_by="uid-leaving")
        new_owner = _make_user(zitadel_id="uid-receiver", status="active")
        org = _make_org()

        db = AsyncMock()
        db.add = MagicMock()
        # Sequence of execute() calls inside apply_dispositions for ONE transfer:
        # 1. _load_kb_or_404 → kb
        # 2. SELECT new_owner → new_owner row
        # 3. DELETE old user_kb_access
        # 4. DELETE existing access for new owner (if any)
        # add() — synchronous insert of new owner row
        db.execute.side_effect = [
            _scalar_result(kb),
            _scalar_result(new_owner),
            MagicMock(),  # delete old grant
            MagicMock(),  # delete possible existing grant for new owner
        ]

        with patch("app.services.kb_offboarding.log_event", AsyncMock()) as mock_log:
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=7, action="transfer", transfer_to="uid-receiver")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )

        assert kb.created_by == "uid-receiver"
        # Audit event emitted with from/to user.
        transfer_calls = [c for c in mock_log.await_args_list if c.kwargs.get("action") == "kb.transferred"]
        assert len(transfer_calls) == 1
        details = transfer_calls[0].kwargs.get("details") or {}
        assert details.get("from_user") == "uid-leaving"
        assert details.get("to_user") == "uid-receiver"
        assert details.get("reason") == "offboarding"
        # New owner row was added.
        db.add.assert_called_once()
        added_row = db.add.call_args.args[0]
        assert added_row.user_id == "uid-receiver"
        assert added_row.role == "owner"
        assert added_row.granted_by == "uid-admin"


class TestApplyDispositionsTransferRejections:
    """REQ-2.4 + receiver-validation guards."""

    @pytest.mark.asyncio
    async def test_transfer_personal_kb_returns_400(self) -> None:
        kb = _make_kb(kb_id=11, owner_type="user", created_by="uid-leaving", slug="personal-uid-leaving")
        org = _make_org()

        db = AsyncMock()
        db.execute.side_effect = [_scalar_result(kb)]

        with pytest.raises(HTTPException) as exc_info:
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=11, action="transfer", transfer_to="uid-receiver")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )

        assert exc_info.value.status_code == 400
        assert "Personal knowledge bases cannot be transferred" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_transfer_to_unknown_user_returns_400(self) -> None:
        kb = _make_kb(kb_id=7, created_by="uid-leaving")
        org = _make_org()

        db = AsyncMock()
        db.execute.side_effect = [
            _scalar_result(kb),
            _scalar_result(None),  # new owner not found
        ]

        with pytest.raises(HTTPException) as exc_info:
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=7, action="transfer", transfer_to="uid-ghost")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )
        assert exc_info.value.status_code == 400
        assert "not a member" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_transfer_to_offboarded_user_returns_400(self) -> None:
        kb = _make_kb(kb_id=7, created_by="uid-leaving")
        new_owner = _make_user(zitadel_id="uid-also-leaving", status="offboarded")
        org = _make_org()

        db = AsyncMock()
        db.execute.side_effect = [
            _scalar_result(kb),
            _scalar_result(new_owner),
        ]

        with pytest.raises(HTTPException) as exc_info:
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=7, action="transfer", transfer_to="uid-also-leaving")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )
        assert exc_info.value.status_code == 400
        assert "not active" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_unknown_kb_id_returns_400(self) -> None:
        org = _make_org()
        db = AsyncMock()
        db.execute.side_effect = [_scalar_result(None)]

        with pytest.raises(HTTPException) as exc_info:
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=999, action="delete")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )
        assert exc_info.value.status_code == 400
        assert "kb_id=999" in str(exc_info.value.detail)


class TestApplyDispositionsDelete:
    """REQ-2.8 — delete pad: org KB or personal KB both purged immediately."""

    @pytest.mark.asyncio
    async def test_delete_personal_kb_emits_personal_purged_event(self) -> None:
        kb = _make_kb(kb_id=11, owner_type="user", created_by="uid-leaving", slug="personal-uid-leaving")
        org = _make_org()

        db = AsyncMock()
        db.delete = AsyncMock()
        db.execute.side_effect = [_scalar_result(kb)]

        with (
            patch("app.services.kb_offboarding.docs_client.deprovision_kb", AsyncMock()),
            patch("app.services.kb_offboarding.knowledge_ingest_client.delete_kb", AsyncMock()) as mock_ingest,
            patch("app.services.kb_offboarding.log_event", AsyncMock()) as mock_log,
        ):
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=11, action="delete")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )

        mock_ingest.assert_awaited_once_with(org.zitadel_org_id, kb.slug)
        db.delete.assert_awaited_once_with(kb)
        purge_calls = [
            c for c in mock_log.await_args_list if c.kwargs.get("action") == "kb.personal_purged_on_offboard"
        ]
        assert len(purge_calls) == 1

    @pytest.mark.asyncio
    async def test_delete_org_kb_emits_admin_deleted_with_offboarding_reason(self) -> None:
        kb = _make_kb(kb_id=8, owner_type="org", created_by="uid-leaving", slug="team-kb")
        org = _make_org()

        db = AsyncMock()
        db.delete = AsyncMock()
        db.execute.side_effect = [_scalar_result(kb)]

        with (
            patch("app.services.kb_offboarding.docs_client.deprovision_kb", AsyncMock()),
            patch("app.services.kb_offboarding.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.services.kb_offboarding.log_event", AsyncMock()) as mock_log,
        ):
            await apply_dispositions(
                target_user_id="uid-leaving",
                dispositions=[KbDisposition(kb_id=8, action="delete")],
                actor_user_id="uid-admin",
                org=org,
                db=db,
            )

        admin_calls = [c for c in mock_log.await_args_list if c.kwargs.get("action") == "kb.admin_deleted"]
        assert len(admin_calls) == 1
        details = admin_calls[0].kwargs.get("details") or {}
        assert details.get("reason") == "offboarding"

    @pytest.mark.asyncio
    async def test_docs_failure_aborts_before_db_delete(self) -> None:
        """REQ-1.5 / AC-10: failure in step-1 leaves the KB row intact."""
        kb = _make_kb(kb_id=8, owner_type="org", created_by="uid-leaving")
        kb.gitea_repo_slug = "team-kb"
        kb.docs_enabled = True
        org = _make_org()

        db = AsyncMock()
        db.delete = AsyncMock()
        db.execute.side_effect = [_scalar_result(kb)]

        with (
            patch(
                "app.services.kb_offboarding.docs_client.deprovision_kb",
                AsyncMock(side_effect=RuntimeError("docs-app down")),
            ),
            patch("app.services.kb_offboarding.knowledge_ingest_client.delete_kb", AsyncMock()) as mock_ingest,
            patch("app.services.kb_offboarding.log_event", AsyncMock()),
        ):
            with pytest.raises(RuntimeError):
                await apply_dispositions(
                    target_user_id="uid-leaving",
                    dispositions=[KbDisposition(kb_id=8, action="delete")],
                    actor_user_id="uid-admin",
                    org=org,
                    db=db,
                )

        mock_ingest.assert_not_awaited()
        db.delete.assert_not_awaited()


class TestRevokeUserCredentials:
    """REQ-2.7 — auto-revoke API keys + MCP tokens."""

    @pytest.mark.asyncio
    async def test_revoke_deletes_api_keys_and_soft_revokes_mcp_tokens(self) -> None:
        db = AsyncMock()
        # Sequence of execute() calls inside revoke_user_credentials:
        # 1. DELETE FROM partner_api_keys → rowcount=2
        # 2. SELECT portal_users.id → 99
        # 3. UPDATE portal_mcp_tokens → rowcount=3
        api_delete_result = MagicMock()
        api_delete_result.rowcount = 2
        user_pk_result = _scalar_result(99)
        mcp_update_result = MagicMock()
        mcp_update_result.rowcount = 3
        db.execute.side_effect = [api_delete_result, user_pk_result, mcp_update_result]

        api_deleted, mcp_revoked = await revoke_user_credentials(
            target_user_id="uid-leaving",
            org_id=101,
            db=db,
        )
        assert api_deleted == 2
        assert mcp_revoked == 3

    @pytest.mark.asyncio
    async def test_revoke_handles_missing_portal_user_row(self) -> None:
        """If the portal_users row is already gone (race or pre-cleanup),
        MCP token revoke is a no-op (no FK to update)."""
        db = AsyncMock()
        api_delete_result = MagicMock()
        api_delete_result.rowcount = 0
        user_pk_result = _scalar_result(None)
        db.execute.side_effect = [api_delete_result, user_pk_result]

        api_deleted, mcp_revoked = await revoke_user_credentials(
            target_user_id="uid-gone",
            org_id=101,
            db=db,
        )
        assert api_deleted == 0
        assert mcp_revoked == 0
