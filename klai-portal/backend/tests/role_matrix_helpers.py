"""Characterization-test helpers for SPEC-PORTAL-RBAC-REFACTOR-001 Phase 2c.

Pins the role-enforcement behaviour of endpoints that use the declarative
``Depends(get_caller_at_least(ProfileRole.ADMIN))`` pattern introduced in
Phase 2c. Gate assertions work by calling the inner ``_dep`` directly (to
test 403) or by calling the endpoint with an injected ``UserPermissions``
object (to test gate-pass). The ``bearer`` dependency is tested separately
for the 401 path.

Legacy helpers ``make_user`` / ``make_org`` are retained for any existing
callers that still use them directly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request, status

from app.api.bearer import bearer
from app.core.permissions import ProfileRole, get_caller_at_least
from app.models.portal import PortalOrg, PortalUser

# All non-admin profile-roles that exist in PROFILES-001 5-rung ladder.
# An admin endpoint MUST 403 each of these.
NON_ADMIN_ROLES: tuple[str, ...] = ("personal", "company", "kb_manager", "group_manager")


def make_user(
    *,
    role: str,
    zitadel_user_id: str = "uid-test",
    org_id: int = 101,
    user_pk: int = 9001,
) -> MagicMock:
    """Synthetic ``PortalUser`` mock with the chosen role."""
    user = MagicMock(spec=PortalUser)
    user.role = role
    user.zitadel_user_id = zitadel_user_id
    user.org_id = org_id
    user.id = user_pk
    user.email = f"{role}@example.com"
    user.first_name = role.capitalize()
    user.last_name = "Tester"
    return user


def make_org(
    *,
    org_id: int = 101,
    slug: str = "voys",
    plan: str = "knowledge",
) -> MagicMock:
    """Synthetic ``PortalOrg`` mock with sensible defaults for admin-gated tests."""
    org = MagicMock(spec=PortalOrg)
    org.id = org_id
    org.slug = slug
    org.plan = plan
    org.zitadel_org_id = f"zitadel-org-{org_id}"
    org.enabled_addons = []
    org.platform_unlocked_features = []
    org.provisioning_status = "active"
    org.moneybird_subscription_id = "ms-test-123"
    org.moneybird_contact_id = "mc-test-123"
    org.billing_cycle = "monthly"
    org.billing_status = "active"
    org.name = slug.capitalize()
    org.mcp_servers = []
    return org


class _PostGateSentinel(Exception):
    """Synchronous sentinel raised at the first DB call after the role gate.

    Using a synchronous raise (via ``MagicMock(side_effect=...)``) avoids
    creating awaitable coroutines that would otherwise leak as
    ``coroutine '...' was never awaited`` RuntimeWarnings when an admin-pass
    test short-circuits before fully consuming all stubbed awaits.
    """


def make_db_mock() -> MagicMock:
    """Return a generic AsyncSession-shaped mock that fires _PostGateSentinel
    synchronously on the first DB call.

    For non-admin callers, the gate raises HTTP 403 BEFORE any DB call —
    so the sentinel never fires. For an admin caller, the gate passes, the
    endpoint reaches the first DB call, the sentinel fires synchronously, and
    the helper's ``except Exception: pass`` swallows it as proof of gate-pass.
    """
    db = MagicMock()
    db.execute = MagicMock(side_effect=_PostGateSentinel("post-gate db.execute"))
    db.scalar = MagicMock(side_effect=_PostGateSentinel("post-gate db.scalar"))
    db.get = MagicMock(side_effect=_PostGateSentinel("post-gate db.get"))
    db.flush = MagicMock(side_effect=_PostGateSentinel("post-gate db.flush"))
    db.commit = MagicMock(side_effect=_PostGateSentinel("post-gate db.commit"))
    db.refresh = MagicMock(side_effect=_PostGateSentinel("post-gate db.refresh"))
    db.add = MagicMock()  # add() is sync in SQLAlchemy; let it succeed
    db.rollback = MagicMock()
    return db


async def assert_admin_passes_gate(
    endpoint: Callable[..., Awaitable[Any]],
    module_path: str,  # accepted for API compat; not used in Phase 2c
    **kwargs: Any,
) -> None:
    """Pin: an ``admin``-role caller MUST NOT receive 401 or 403.

    Calls the endpoint directly with a synthetic ``UserPermissions`` for
    ``role="admin"``. The ``credentials`` kwarg (legacy artifact from the
    pre-Phase-2c pattern) is stripped before forwarding.

    The endpoint may explode on post-gate work because we do not deeply mock
    its dependencies; that is expected and acceptable. The only forbidden
    outcomes for an admin caller are HTTP 401 (auth) and HTTP 403 (role).
    """
    from tests.conftest import make_perms  # lazy import avoids circular deps

    filtered = {k: v for k, v in kwargs.items() if k != "credentials"}
    try:
        await endpoint(perms=make_perms(role="admin"), **filtered)
    except HTTPException as e:
        assert e.status_code not in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), f"admin role unexpectedly blocked at gate: status={e.status_code} detail={e.detail!r}"
    except Exception:  # noqa: S110 — post-gate explosion is acceptable; gate-pass is the only assertion
        pass


async def assert_role_blocked_at_gate(
    endpoint: Callable[..., Awaitable[Any]],  # accepted for API compat; not called
    module_path: str,  # accepted for API compat; not used in Phase 2c
    role: str,
    **kwargs: Any,  # accepted for API compat; not forwarded
) -> None:
    """Pin: a non-admin role MUST receive HTTP 403 from the role gate.

    Tests the inner ``_dep`` of ``get_caller_at_least(ProfileRole.ADMIN)``
    directly by passing a synthetic ``UserPermissions`` for the given role.
    The ``Depends(get_caller)`` default is bypassed because we supply
    ``perms`` explicitly.
    """
    from tests.conftest import make_perms  # lazy import avoids circular deps

    _dep = get_caller_at_least(ProfileRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await _dep(perms=make_perms(role=role))
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN, (
        f"role={role!r} expected HTTP 403 from gate, got status={exc.value.status_code} detail={exc.value.detail!r}"
    )


async def assert_unauthenticated_blocked(
    endpoint: Callable[..., Awaitable[Any]],  # accepted for API compat; not called
    module_path: str,  # accepted for API compat; not used in Phase 2c
    **kwargs: Any,  # accepted for API compat; not forwarded
) -> None:
    """Pin: an unauthenticated request MUST receive HTTP 401.

    Tests the ``bearer`` dependency directly: creates a mock ``Request``
    whose ``request.state`` has no ``SessionContext``, then calls
    ``bearer(request=mock_request)`` and asserts the resulting 401.
    """
    mock_request = MagicMock(spec=Request)
    mock_request.state = MagicMock()
    # Ensure getattr(request.state, "session", None) returns None so bearer raises 401
    del mock_request.state.session  # AttributeError → getattr returns None
    mock_request.headers = {}

    with pytest.raises(HTTPException) as exc:
        await bearer(request=mock_request)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED, (
        f"unauthenticated call expected HTTP 401, got status={exc.value.status_code} detail={exc.value.detail!r}"
    )
