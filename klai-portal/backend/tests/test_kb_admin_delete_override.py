"""SPEC-PORTAL-KB-OWNERSHIP-001 Phase 2 — admin-override delete tests.

Covers REQ-1.1 .. REQ-1.5 + AC-1 .. AC-4 of the SPEC.

Direct invocation pattern: each test patches the external-call helpers
(docs_client, knowledge_ingest_client) and the role lookup, then calls
``delete_app_knowledge_base`` directly with a mock DB session and a
``Request``-like object that exposes the admin-override header.

The personal-firewall (REQ-3) is enforced by ``get_kb_with_access`` at the
route-level before the handler body runs; in direct-invocation tests the
dep does not run, so we assert REQ-1.3 (admin-override on personal KB →
404) via the route-level assertion in ``test_kb_personal_firewall.py``
instead. Here we focus on the handler-body branching.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from tests.conftest import make_perms

OVERRIDE_HEADER = "X-Admin-Override-Confirm"
OVERRIDE_VALUE = "I-WAS-NOT-CREATOR"


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a Starlette Request with the supplied headers (lowercase keys)."""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": "DELETE",
        "headers": raw_headers,
    }
    return Request(scope)


def _make_kb(*, owner_type: str = "org", created_by: str = "creator-uid", slug: str = "team-kb") -> MagicMock:
    kb = MagicMock()
    kb.id = 7
    kb.org_id = 101
    kb.name = "Team KB"
    kb.slug = slug
    kb.owner_type = owner_type
    kb.owner_user_id = None if owner_type == "org" else created_by
    kb.created_by = created_by
    kb.gitea_repo_slug = None
    kb.docs_enabled = False
    return kb


def _make_org() -> MagicMock:
    org = MagicMock()
    org.id = 101
    org.slug = "voys"
    org.zitadel_org_id = "zitadel-org-1"
    return org


def _db_with_kb(kb: MagicMock) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = kb
    db.execute.return_value = result
    return db


class TestOwnerDeletePathStillWorks:
    """AC-1: existing owner-pad behaviour is unchanged."""

    @pytest.mark.asyncio
    async def test_owner_can_delete_without_override_header(self) -> None:
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(created_by="uid-owner")
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value="owner")),
            patch("app.api.app_knowledge_bases.docs_client.deprovision_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()) as mock_log,
        ):
            # Owner = creator → no override header needed.
            await delete_app_knowledge_base(
                kb_slug="team-kb",
                request=_make_request(headers={}),
                perms=make_perms(role="admin", user_id="uid-owner", org_id=101),
                db=db,
            )

        db.delete.assert_awaited_once_with(kb)
        # AC-1 owner-pad does NOT emit kb.admin_deleted (no override fired).
        for call in mock_log.await_args_list:
            assert call.kwargs.get("action") != "kb.admin_deleted"


class TestAdminOverridePath:
    """AC-2 + REQ-1.4: admin can delete with explicit header + audit emit."""

    @pytest.mark.asyncio
    async def test_admin_with_override_header_succeeds_on_org_kb(self) -> None:
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(created_by="uid-other-creator")  # admin is not the creator
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            # Admin caller has no role on this KB — would normally get 403.
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value=None)),
            patch("app.api.app_knowledge_bases.docs_client.deprovision_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()) as mock_log,
        ):
            await delete_app_knowledge_base(
                kb_slug="team-kb",
                request=_make_request(headers={OVERRIDE_HEADER: OVERRIDE_VALUE}),
                perms=make_perms(role="admin", user_id="uid-admin", org_id=101),
                db=db,
            )

        db.delete.assert_awaited_once_with(kb)
        # REQ-1.4: audit event emitted with prev_owner = original created_by
        admin_delete_calls = [
            call for call in mock_log.await_args_list if call.kwargs.get("action") == "kb.admin_deleted"
        ]
        assert len(admin_delete_calls) == 1, "expected exactly one kb.admin_deleted event"
        meta = admin_delete_calls[0].kwargs.get("details") or {}
        assert meta.get("previous_owner") == "uid-other-creator"


class TestAdminOverridePathRejected:
    """AC-3: admin without header keeps 403."""

    @pytest.mark.asyncio
    async def test_admin_without_override_header_gets_403(self) -> None:
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(created_by="uid-other-creator")
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value=None)),
            patch("app.api.app_knowledge_bases.docs_client.deprovision_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_app_knowledge_base(
                    kb_slug="team-kb",
                    request=_make_request(headers={}),  # no override header
                    perms=make_perms(role="admin", user_id="uid-admin", org_id=101),
                    db=db,
                )

        assert exc_info.value.status_code == 403
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_admin_with_wrong_header_value_gets_403(self) -> None:
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(created_by="uid-other-creator")
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value=None)),
            patch("app.api.app_knowledge_bases.docs_client.deprovision_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_app_knowledge_base(
                    kb_slug="team-kb",
                    request=_make_request(headers={OVERRIDE_HEADER: "yes-please"}),
                    perms=make_perms(role="admin", user_id="uid-admin", org_id=101),
                    db=db,
                )

        assert exc_info.value.status_code == 403
        db.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_admin_with_override_header_still_gets_403(self) -> None:
        """Override only works for ProfileRole.ADMIN. A regular user
        sending the header gets 403 (not silent escalation)."""
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(created_by="uid-other-creator")
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value=None)),
            patch("app.api.app_knowledge_bases.docs_client.deprovision_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_app_knowledge_base(
                    kb_slug="team-kb",
                    request=_make_request(headers={OVERRIDE_HEADER: OVERRIDE_VALUE}),
                    perms=make_perms(role="personal", user_id="uid-regular", org_id=101),
                    db=db,
                )

        assert exc_info.value.status_code == 403
        db.delete.assert_not_awaited()


class TestAdminOverrideOnPersonalKbBlocked:
    """REQ-1.3: admin-override pad MUST refuse personal KBs.

    The route-level firewall (get_kb_with_access) catches this for real
    HTTP requests by returning 404 before the handler body runs. This test
    verifies the handler body itself ALSO refuses to override on personal
    KBs as belt-and-braces — if a future refactor accidentally moves the
    firewall, the body must still be safe.
    """

    @pytest.mark.asyncio
    async def test_admin_override_on_personal_kb_returns_404(self) -> None:
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(owner_type="user", created_by="uid-other-user", slug="personal-uid-other")
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value=None)),
            patch("app.api.app_knowledge_bases.docs_client.deprovision_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()),
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await delete_app_knowledge_base(
                    kb_slug="personal-uid-other",
                    request=_make_request(headers={OVERRIDE_HEADER: OVERRIDE_VALUE}),
                    perms=make_perms(role="admin", user_id="uid-admin", org_id=101),
                    db=db,
                )

        # 404 (not 403) — non-disclosure.
        assert exc_info.value.status_code == 404
        db.delete.assert_not_awaited()


class TestAdminOverrideFailureSemantics:
    """REQ-1.5: admin-override pad has identical failure semantics to owner pad.

    docs-app failure aborts BEFORE the portal-DB delete fires, leaving the
    KB row intact (same as owner-pad).
    """

    @pytest.mark.asyncio
    async def test_docs_failure_aborts_before_portal_db_delete(self) -> None:
        from app.api.app_knowledge_bases import delete_app_knowledge_base

        kb = _make_kb(created_by="uid-other-creator")
        kb.docs_enabled = True
        kb.gitea_repo_slug = "team-kb"
        db = _db_with_kb(kb)
        org = _make_org()

        with (
            patch("app.api.app_knowledge_bases._load_org_or_500", AsyncMock(return_value=org)),
            patch("app.api.app_knowledge_bases.get_user_role_for_kb", AsyncMock(return_value=None)),
            patch(
                "app.api.app_knowledge_bases.docs_client.deprovision_kb",
                AsyncMock(side_effect=RuntimeError("docs-app down")),
            ),
            patch("app.api.app_knowledge_bases.knowledge_ingest_client.delete_kb", AsyncMock()) as mock_ingest,
            patch("app.api.app_knowledge_bases.log_event", AsyncMock()),
        ):
            with pytest.raises(RuntimeError):
                await delete_app_knowledge_base(
                    kb_slug="team-kb",
                    request=_make_request(headers={OVERRIDE_HEADER: OVERRIDE_VALUE}),
                    perms=make_perms(role="admin", user_id="uid-admin", org_id=101),
                    db=db,
                )

        mock_ingest.assert_not_awaited()
        db.delete.assert_not_awaited()
