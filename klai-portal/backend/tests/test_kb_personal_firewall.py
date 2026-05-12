"""SPEC-PORTAL-KB-OWNERSHIP-001 — Personal-KB firewall tests.

REQ-3.1 — central FastAPI dependency `get_kb_with_access` enforces:
  - returns the KB when caller is org-member AND (kb is org-owned OR kb is personal-and-caller-owned)
  - raises HTTPException(404) when kb is personal AND owned by someone else
    (NOT 403 — never leak existence of a personal KB)
  - applies regardless of caller role (admin gets 404 too)

REQ-3.3 — invariant introspection test: every route under
  `/api/app/knowledge-bases/{kb_slug}/...` uses `get_kb_with_access` in its
  dependency tree. A new route that forgets the gate fails this test.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _kb(*, owner_type: str, owner_user_id: str | None, org_id: int = 101, slug: str = "kb") -> MagicMock:
    """Build a PortalKnowledgeBase-shaped mock."""
    kb = MagicMock()
    kb.id = 42
    kb.org_id = org_id
    kb.name = "Test KB"
    kb.slug = slug
    kb.owner_type = owner_type
    kb.owner_user_id = owner_user_id
    kb.created_by = owner_user_id or "creator-uid"
    return kb


def _db_with_kb(kb: MagicMock | None) -> AsyncMock:
    """Mock AsyncSession that yields the given kb (or None) for the first SELECT."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = kb
    db.execute.return_value = result
    return db


class TestIsPersonalKb:
    """is_personal_kb is the single-source-of-truth helper for owner_type discrimination."""

    def test_personal_kb_returns_true(self) -> None:
        from app.services.access import is_personal_kb

        kb = _kb(owner_type="user", owner_user_id="uid-A")
        assert is_personal_kb(kb) is True

    def test_org_kb_returns_false(self) -> None:
        from app.services.access import is_personal_kb

        kb = _kb(owner_type="org", owner_user_id=None)
        assert is_personal_kb(kb) is False


class TestGetKbWithAccess:
    """get_kb_with_access is the firewall + 404-resolver dependency."""

    @pytest.mark.asyncio
    async def test_org_kb_is_returned_for_any_org_member(self) -> None:
        from app.api.dependencies import get_kb_with_access

        kb = _kb(owner_type="org", owner_user_id=None)
        db = _db_with_kb(kb)
        perms = make_perms(user_id="uid-member", org_id=101, role="personal")

        result = await get_kb_with_access(kb_slug="some-kb", perms=perms, db=db)
        assert result is kb

    @pytest.mark.asyncio
    async def test_personal_kb_is_returned_to_owner(self) -> None:
        from app.api.dependencies import get_kb_with_access

        kb = _kb(owner_type="user", owner_user_id="uid-owner", slug="personal-uid-owner")
        db = _db_with_kb(kb)
        perms = make_perms(user_id="uid-owner", org_id=101, role="personal")

        result = await get_kb_with_access(kb_slug="personal-uid-owner", perms=perms, db=db)
        assert result is kb

    @pytest.mark.asyncio
    async def test_personal_kb_of_another_user_returns_404_for_admin(self) -> None:
        """Firewall: admin gets 404 (NOT 403) — existence-non-disclosure."""
        from app.api.dependencies import get_kb_with_access

        kb = _kb(owner_type="user", owner_user_id="uid-A", slug="personal-uid-A")
        db = _db_with_kb(kb)
        perms = make_perms(user_id="uid-admin", org_id=101, role="admin")

        with pytest.raises(HTTPException) as exc_info:
            await get_kb_with_access(kb_slug="personal-uid-A", perms=perms, db=db)

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_personal_kb_of_another_user_returns_404_for_regular_user(self) -> None:
        from app.api.dependencies import get_kb_with_access

        kb = _kb(owner_type="user", owner_user_id="uid-A", slug="personal-uid-A")
        db = _db_with_kb(kb)
        perms = make_perms(user_id="uid-B", org_id=101, role="personal")

        with pytest.raises(HTTPException) as exc_info:
            await get_kb_with_access(kb_slug="personal-uid-A", perms=perms, db=db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_kb_returns_404(self) -> None:
        from app.api.dependencies import get_kb_with_access

        db = _db_with_kb(None)
        perms = make_perms(user_id="uid", org_id=101, role="personal")

        with pytest.raises(HTTPException) as exc_info:
            await get_kb_with_access(kb_slug="nope", perms=perms, db=db)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_kb_in_different_org_returns_404(self) -> None:
        """Tenant-isolation interaction: query is org-scoped, so cross-tenant kb_slug returns None → 404."""
        from app.api.dependencies import get_kb_with_access

        # _db_with_kb(None) simulates the org-scoped SELECT returning no rows because
        # the kb actually belongs to org_id != perms.org_id.
        db = _db_with_kb(None)
        perms = make_perms(user_id="uid", org_id=999, role="admin")

        with pytest.raises(HTTPException) as exc_info:
            await get_kb_with_access(kb_slug="some-kb", perms=perms, db=db)

        assert exc_info.value.status_code == 404


class TestRouteFirewallInvariant:
    """REQ-3.3: every KB-route with `{kb_slug}` uses `get_kb_with_access` in its dep tree."""

    def test_every_kb_slug_route_uses_firewall_dependency(self) -> None:
        """Static introspection: scan FastAPI routes and assert the firewall dependency is wired.

        New routes that take `{kb_slug}` as a path-param without going through
        `get_kb_with_access` fail this test. The dependency does both the
        org-scoped SELECT and the personal-firewall check; bypassing it skips
        both and re-introduces SPEC-PORTAL-KB-OWNERSHIP-001 P3.
        """
        from fastapi.dependencies.utils import get_flat_dependant

        from app.api.dependencies import get_kb_with_access
        from app.main import app

        # Routes intentionally exempt from the firewall dependency:
        # - create_app_knowledge_base: kb_slug is in REQUEST BODY, not path. Quota check
        #   handles personal-vs-org. New KB has no existence to firewall.
        # - /internal/knowledge-bases/{kb_slug}/metadata: X-Internal-Secret-authenticated
        #   service-to-service endpoint. No `perms` to check against; trusted callers.
        # - /kb-images/{zitadel_org_id}/images/{kb_slug}/{filename}: public image fetch
        #   gated by image-hash + zitadel_org_id, not by user identity. kb_slug is
        #   informational in the routing path.
        exempt_paths = {
            "/api/app/knowledge-bases",  # POST create
            "/internal/knowledge-bases/{kb_slug}/metadata",
            "/kb-images/{zitadel_org_id}/images/{kb_slug}/{filename}",
        }

        from app.core.permissions import get_caller

        offenders: list[tuple[str, list[str]]] = []
        for route in app.routes:
            path = getattr(route, "path", None)
            if not path:
                continue
            if "{kb_slug}" not in path:
                continue
            if path in exempt_paths:
                continue
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            flat = get_flat_dependant(dependant)
            call_chain = [d.call for d in flat.dependencies if d.call is not None]
            # Skip internal-only routes (X-Internal-Secret authenticated; no
            # `perms`/`get_caller` in the dep chain). These cannot use
            # `get_kb_with_access` because it depends on `get_caller`.
            # Trusted-caller endpoints carry their own auth model and do not
            # leak personal-KB existence to end users.
            if get_caller not in call_chain:
                continue
            if get_kb_with_access not in call_chain:
                methods = sorted(getattr(route, "methods", []) or [])
                offenders.append(
                    (f"{','.join(methods)} {path}", [getattr(c, "__qualname__", repr(c)) for c in call_chain])
                )

        assert not offenders, (
            "Routes with {kb_slug} that don't use get_kb_with_access "
            "(SPEC-PORTAL-KB-OWNERSHIP-001 REQ-3.3 firewall):\n"
            + "\n".join(f"  - {route}\n    deps: {deps}" for route, deps in offenders)
        )
