"""Characterization-test helpers for SPEC-PORTAL-RBAC-REFACTOR-001 Pre-phase.

Pins the current behaviour of imperative `_require_admin` gates as
HTTP-status snapshots (200/403/401) so the Phase 1+2 refactor to declarative
`Depends(get_caller_at_least(...))` cannot silently change role-enforcement.

# TEMPORARY: vervangen door Phase 1 fixture
SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1 ships a definitive
`make_user(role=...)` factory in `tests/conftest.py` that yields a real
`PortalUser` with a typed `ProfileRole` enum + a tenant-scoped helper.
This module is intentionally minimal — it is replaced by that helper, and
the snapshot tests below stay (they become the regression-suite for the
uniform gate-laag).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, status

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
    """Synthetic `PortalUser` mock with the chosen role.

    Phase 1 will replace this with a typed `ProfileRole` enum + real DB row.
    """
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
    plan: str = "complete",
) -> MagicMock:
    """Synthetic `PortalOrg` mock with sensible defaults for admin-gated tests."""
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
    org.seats = 5
    org.billing_status = "active"
    org.name = slug.capitalize()
    org.mcp_servers = []
    return org


@contextmanager
def patch_caller(module_path: str, *, role: str | None):
    """Patch `_get_caller_org` in the endpoint module's namespace.

    role=None  → simulate unauthenticated (`_get_caller_org` raises 401).
    role=str   → simulate authenticated user with that role.
    """
    target = f"{module_path}._get_caller_org"
    if role is None:
        with patch(
            target,
            side_effect=HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
            ),
        ) as mock_:
            yield mock_
    else:
        org = make_org()
        user = make_user(role=role)
        with patch(target, return_value=("uid-test", org, user)) as mock_:
            yield mock_


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

    For non-admin / unauthenticated callers, the gate raises HTTP 401/403
    BEFORE any DB call — so the sentinel never fires. For an admin caller,
    the gate passes, the endpoint reaches the first DB call, the sentinel
    fires synchronously, and the helper's ``except Exception: pass`` swallows
    it as proof of gate-pass.
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
    module_path: str,
    **kwargs: Any,
) -> None:
    """Pin: an `admin`-role caller MUST NOT receive 401 or 403.

    The endpoint may explode on post-gate work because we do not deeply mock
    its dependencies; that is expected and acceptable. The only forbidden
    outcomes for an admin caller are HTTP 401 (auth) and HTTP 403 (role).
    """
    with patch_caller(module_path, role="admin"):
        try:
            await endpoint(**kwargs)
        except HTTPException as e:
            assert e.status_code not in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ), f"admin role unexpectedly blocked at gate: status={e.status_code} detail={e.detail!r}"
        except Exception:  # noqa: S110 — see docstring; gate-pass is the only assertion
            # Post-gate explosion (TypeError, AttributeError on under-mocked DB,
            # custom sentinel, …) is acceptable — the gate let admin through.
            pass


async def assert_role_blocked_at_gate(
    endpoint: Callable[..., Awaitable[Any]],
    module_path: str,
    role: str,
    **kwargs: Any,
) -> None:
    """Pin: a non-admin role MUST receive HTTP 403 from the role gate."""
    with patch_caller(module_path, role=role):
        with pytest.raises(HTTPException) as exc:
            await endpoint(**kwargs)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN, (
            f"role={role!r} expected HTTP 403 from gate, got status={exc.value.status_code} detail={exc.value.detail!r}"
        )


async def assert_unauthenticated_blocked(
    endpoint: Callable[..., Awaitable[Any]],
    module_path: str,
    **kwargs: Any,
) -> None:
    """Pin: an unauthenticated caller MUST receive HTTP 401.

    The 401 comes from `_get_caller_org` itself before any role check runs.
    """
    with patch_caller(module_path, role=None):
        with pytest.raises(HTTPException) as exc:
            await endpoint(**kwargs)
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED, (
            f"unauthenticated call expected HTTP 401, got status={exc.value.status_code} detail={exc.value.detail!r}"
        )
