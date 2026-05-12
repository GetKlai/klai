"""Tests for `app/core/permissions.py` — SPEC-PORTAL-RBAC-REFACTOR-001 Phase 1.

Covers:
- `UserPermissions` dataclass shape (REQ-1, AC-1)
- `resolve_user_permissions()` single-query contract (AC-1)
- `ProfileRole(StrEnum)` (REQ-3)
- Declarative gates (REQ-1D): `get_caller`, `get_caller_at_least`,
  `require_product`, `require_capability`, `require_platform_admin`,
  `require_platform_unlocked`
- Behavioural snapshots that line up with AC-8 (admin-on-core sees
  complete-tier capabilities) and AC-13/14/15 stubs (platform-unlocked
  features default empty until Phase 5 ships the column).

Pure unit tests: no real DB, mocks the SQLAlchemy AsyncSession and the
Zitadel userinfo call. Same pattern as `tests/test_admin_users.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from app.core.permissions import (
    ProfileRole,
    UserPermissions,
    get_caller,
    get_caller_at_least,
    require_capability,
    require_platform_admin,
    require_platform_unlocked,
    require_product,
    resolve_user_permissions,
)
from app.core.profiles import Capability

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _row(
    *,
    role: str,
    plan: str,
    enabled_addons: list[str] | None = None,
    platform_unlocked_features: list[str] | None = None,
    org_id: int = 101,
    slug: str = "voys",
    provisioning_status: str = "active",
) -> tuple[MagicMock, MagicMock]:
    """Build a (PortalOrg, PortalUser) tuple as `db.execute(...).one_or_none()` would
    return for the resolver query.

    SPEC-PORTAL-EXTENSIONS-UNIFY-001: `enabled_addons=` kept as back-compat alias —
    its value is merged into `platform_unlocked_features` for the mock org so older
    tests can still express their setup without rewriting.
    """
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    org.plan = plan
    org.platform_unlocked_features = list({*(platform_unlocked_features or []), *(enabled_addons or [])})
    org.provisioning_status = provisioning_status

    user = MagicMock()
    user.role = role
    user.zitadel_user_id = "uid-test"
    user.org_id = org_id
    user.id = 42
    return org, user


def _db_with_row(row: tuple[MagicMock, MagicMock] | None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.one_or_none = MagicMock(return_value=row)
    db.execute = AsyncMock(return_value=result)
    db.scalar = AsyncMock(return_value=None)
    return db


# ---------------------------------------------------------------------------
# Section 1 — ProfileRole(StrEnum) [REQ-3]
# ---------------------------------------------------------------------------


def test_profile_role_is_str_enum() -> None:
    """ProfileRole values are equal to their string forms (StrEnum contract)."""
    assert ProfileRole.ADMIN == "admin"
    assert ProfileRole.PERSONAL == "personal"
    assert ProfileRole.COMPANY == "company"
    assert ProfileRole.KB_MANAGER == "kb_manager"
    assert ProfileRole.GROUP_MANAGER == "group_manager"


def test_profile_role_set_membership() -> None:
    """ProfileRole values can be checked against frozenset[str] containers."""
    elevated: frozenset[str] = frozenset({"kb_manager", "group_manager", "admin"})
    assert ProfileRole.ADMIN in elevated
    assert ProfileRole.PERSONAL not in elevated


def test_profile_role_ladder_ordering() -> None:
    """Five-rung ladder: personal < company < kb_manager < group_manager < admin."""
    from app.core.permissions import PROFILE_RANK

    assert PROFILE_RANK[ProfileRole.PERSONAL] == 0
    assert PROFILE_RANK[ProfileRole.ADMIN] == 4
    assert PROFILE_RANK[ProfileRole.COMPANY] < PROFILE_RANK[ProfileRole.KB_MANAGER]


# ---------------------------------------------------------------------------
# Section 2 — resolve_user_permissions() [REQ-1, AC-1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unknown_user() -> None:
    db = _db_with_row(None)
    perms = await resolve_user_permissions("uid-unknown", db)
    assert perms is None


@pytest.mark.asyncio
async def test_resolve_uses_single_query() -> None:
    """AC-1: resolver MUST issue exactly one SELECT against the database."""
    db = _db_with_row(_row(role="admin", plan="knowledge"))
    await resolve_user_permissions("uid-test", db)
    assert db.execute.call_count == 1, (
        f"resolve_user_permissions issued {db.execute.call_count} queries; AC-1 requires exactly 1"
    )


@pytest.mark.asyncio
async def test_resolve_returns_user_permissions_dataclass() -> None:
    db = _db_with_row(_row(role="admin", plan="knowledge", slug="voys", org_id=101))
    perms = await resolve_user_permissions("uid-test", db)
    assert isinstance(perms, UserPermissions)
    # All 12 fields populated
    assert perms.user_id == "uid-test"
    assert perms.org_id == 101
    assert perms.org_slug == "voys"
    assert perms.role == ProfileRole.ADMIN
    assert perms.plan == "knowledge"
    # SPEC-PORTAL-EXTENSIONS-UNIFY-001: enabled_addons folded into
    # platform_unlocked_features; UserPermissions no longer carries
    # enabled_addons as a separate field.
    assert isinstance(perms.platform_unlocked_features, frozenset)
    assert perms.effective_role == ProfileRole.ADMIN
    assert isinstance(perms.effective_capabilities, frozenset)
    assert isinstance(perms.effective_products, frozenset)
    assert perms.effective_kb_limits is not None
    assert isinstance(perms.is_platform_admin, bool)


@pytest.mark.asyncio
async def test_resolve_admin_on_core_gets_complete_capabilities() -> None:
    """AC-8: admin on `core` sees all complete-tier capabilities (admin bypass).

    Mirrors the existing `dependencies.get_effective_capabilities` semantics —
    admin-bypass is intentional per SPEC-PORTAL-PROFILES-001 v0.2.0/v0.3.0.
    """
    db = _db_with_row(_row(role="admin", plan="chat"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    expected = frozenset(
        {
            Capability.KB_CONNECTORS,
            Capability.KB_CONNECTORS_EXTERNAL,
            Capability.KB_CREATE_ORG,
            Capability.KB_MEMBERS,
            Capability.KB_TAXONOMY,
            Capability.KB_GAPS,
        }
    )
    assert perms.effective_capabilities == expected


@pytest.mark.asyncio
async def test_resolve_personal_on_complete_gets_kb_connectors_only() -> None:
    """AC-8: personal on `complete` → `{kb.connectors}`."""
    db = _db_with_row(_row(role="personal", plan="knowledge"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.effective_capabilities == frozenset({Capability.KB_CONNECTORS})


@pytest.mark.asyncio
async def test_resolve_personal_on_free_gets_no_capabilities() -> None:
    """AC-8: personal on `free` → empty set (free has no plan-tier caps)."""
    db = _db_with_row(_row(role="personal", plan="free"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.effective_capabilities == frozenset()


@pytest.mark.asyncio
async def test_resolve_products_for_admin_with_addons() -> None:
    """Effective products = plan_features union enabled_addons (subject to floor)."""
    db = _db_with_row(_row(role="admin", plan="knowledge", enabled_addons=["scribe", "docs"]))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.effective_products == frozenset({"chat", "knowledge", "scribe", "docs"})


@pytest.mark.asyncio
async def test_resolve_products_for_personal_excludes_addons() -> None:
    """`scribe`/`docs` floor = company; personal does NOT get them even when enabled."""
    db = _db_with_row(_row(role="personal", plan="knowledge", enabled_addons=["scribe", "docs"]))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.effective_products == frozenset({"chat", "knowledge"})


@pytest.mark.asyncio
async def test_resolve_platform_admin_flag_for_getklai_org() -> None:
    """Org with slug == settings.platform_org_slug → is_platform_admin=True."""
    db = _db_with_row(_row(role="admin", plan="knowledge", slug="getklai"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.is_platform_admin is True


@pytest.mark.asyncio
async def test_resolve_platform_admin_flag_for_tenant_org() -> None:
    db = _db_with_row(_row(role="admin", plan="knowledge", slug="voys"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.is_platform_admin is False


@pytest.mark.asyncio
async def test_resolve_platform_unlocked_features_default_empty() -> None:
    """Phase 5 prep: column default is empty list. Resolver wraps as frozenset."""
    db = _db_with_row(_row(role="admin", plan="knowledge"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.platform_unlocked_features == frozenset()


@pytest.mark.asyncio
async def test_resolve_platform_unlocked_features_populated() -> None:
    db = _db_with_row(_row(role="admin", plan="knowledge", platform_unlocked_features=["partner_api", "widgets"]))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    assert perms.platform_unlocked_features == frozenset({"partner_api", "widgets"})


@pytest.mark.asyncio
async def test_resolve_dataclass_is_frozen() -> None:
    """UserPermissions is frozen — no caller can mutate it after construction."""
    db = _db_with_row(_row(role="admin", plan="knowledge"))
    perms = await resolve_user_permissions("uid-test", db)
    assert perms is not None
    with pytest.raises((AttributeError, TypeError)):
        perms.role = ProfileRole.PERSONAL  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Section 3 — get_caller dependency (REQ-1D)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_caller_returns_user_permissions() -> None:
    db = _db_with_row(_row(role="admin", plan="knowledge"))
    creds = MagicMock(credentials="tok-admin")

    with (
        patch("app.core.permissions.zitadel.get_userinfo", new=AsyncMock(return_value={"sub": "uid-test"})),
        patch("app.core.permissions.set_tenant", new=AsyncMock()),
    ):
        perms = await get_caller(credentials=creds, db=db)

    assert isinstance(perms, UserPermissions)
    assert perms.role == ProfileRole.ADMIN


@pytest.mark.asyncio
async def test_get_caller_401_on_invalid_token() -> None:
    db = _db_with_row(None)
    creds = MagicMock(credentials="tok-bad")

    with patch(
        "app.core.permissions.zitadel.get_userinfo",
        new=AsyncMock(side_effect=RuntimeError("zitadel rejected")),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_caller(credentials=creds, db=db)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_caller_404_on_no_portal_user() -> None:
    db = _db_with_row(None)
    creds = MagicMock(credentials="tok-orphan")

    with patch(
        "app.core.permissions.zitadel.get_userinfo",
        new=AsyncMock(return_value={"sub": "uid-orphan"}),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_caller(credentials=creds, db=db)
    assert exc.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_caller_403_during_deprovisioning() -> None:
    """Mirror admin/__init__::_get_caller_org SPEC-INFRA-TENANT-DELETE-001 R1.

    A caller whose org is in `deprovisioning` must be blocked at the gate with
    error_code=`tenant_deleting`."""
    db = _db_with_row(_row(role="admin", plan="knowledge", provisioning_status="deprovisioning"))
    creds = MagicMock(credentials="tok-admin")

    with (
        patch("app.core.permissions.zitadel.get_userinfo", new=AsyncMock(return_value={"sub": "uid-test"})),
        patch("app.core.permissions.set_tenant", new=AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_caller(credentials=creds, db=db)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Section 4 — get_caller_at_least(role) (REQ-1D)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_caller_at_least_admin_passes_for_admin() -> None:
    perms = UserPermissions(
        user_id="uid",
        org_id=101,
        org_slug="voys",
        role=ProfileRole.ADMIN,
        plan="knowledge",
        platform_unlocked_features=frozenset(),
        effective_role=ProfileRole.ADMIN,
        effective_capabilities=frozenset(),
        effective_products=frozenset(),
        effective_kb_limits=None,  # type: ignore[arg-type]
        is_platform_admin=False,
    )
    dep = get_caller_at_least(ProfileRole.ADMIN)
    # The dependency body is a coroutine that takes the resolved perms
    result = await dep.__wrapped__(perms=perms) if hasattr(dep, "__wrapped__") else await dep(perms=perms)
    assert result is perms


@pytest.mark.asyncio
async def test_get_caller_at_least_admin_blocks_company() -> None:
    perms = UserPermissions(
        user_id="uid",
        org_id=101,
        org_slug="voys",
        role=ProfileRole.COMPANY,
        plan="knowledge",
        platform_unlocked_features=frozenset(),
        effective_role=ProfileRole.COMPANY,
        effective_capabilities=frozenset(),
        effective_products=frozenset(),
        effective_kb_limits=None,  # type: ignore[arg-type]
        is_platform_admin=False,
    )
    dep = get_caller_at_least(ProfileRole.ADMIN)
    with pytest.raises(HTTPException) as exc:
        await dep(perms=perms)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    "caller,required,expected_pass",
    [
        (ProfileRole.PERSONAL, ProfileRole.PERSONAL, True),
        (ProfileRole.COMPANY, ProfileRole.PERSONAL, True),
        (ProfileRole.PERSONAL, ProfileRole.COMPANY, False),
        (ProfileRole.KB_MANAGER, ProfileRole.GROUP_MANAGER, False),
        (ProfileRole.GROUP_MANAGER, ProfileRole.GROUP_MANAGER, True),
        (ProfileRole.ADMIN, ProfileRole.GROUP_MANAGER, True),
    ],
)
@pytest.mark.asyncio
async def test_get_caller_at_least_role_matrix(caller: ProfileRole, required: ProfileRole, expected_pass: bool) -> None:
    perms = UserPermissions(
        user_id="uid",
        org_id=101,
        org_slug="voys",
        role=caller,
        plan="knowledge",
        platform_unlocked_features=frozenset(),
        effective_role=caller,
        effective_capabilities=frozenset(),
        effective_products=frozenset(),
        effective_kb_limits=None,  # type: ignore[arg-type]
        is_platform_admin=False,
    )
    dep = get_caller_at_least(required)
    if expected_pass:
        assert await dep(perms=perms) is perms
    else:
        with pytest.raises(HTTPException) as exc:
            await dep(perms=perms)
        assert exc.value.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Section 5 — require_product / require_capability with UserPermissions
# ---------------------------------------------------------------------------


def _perms_with(
    *,
    role: ProfileRole = ProfileRole.ADMIN,
    products: frozenset[str] = frozenset(),
    caps: frozenset[Capability] = frozenset(),
    platform_features: frozenset[str] = frozenset(),
    is_platform_admin: bool = False,
) -> UserPermissions:
    return UserPermissions(
        user_id="uid",
        org_id=101,
        org_slug="voys",
        role=role,
        plan="knowledge",
        platform_unlocked_features=platform_features,
        effective_role=role,
        effective_capabilities=caps,
        effective_products=products,
        effective_kb_limits=None,  # type: ignore[arg-type]
        is_platform_admin=is_platform_admin,
    )


@pytest.mark.asyncio
async def test_require_product_passes_when_product_present() -> None:
    perms = _perms_with(products=frozenset({"chat", "scribe"}))
    dep = require_product("scribe")
    assert await dep(perms=perms) is perms


@pytest.mark.asyncio
async def test_require_product_403_when_missing() -> None:
    perms = _perms_with(products=frozenset({"chat"}))
    dep = require_product("scribe")
    with pytest.raises(HTTPException) as exc:
        await dep(perms=perms)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_require_capability_passes() -> None:
    perms = _perms_with(caps=frozenset({Capability.KB_TAXONOMY}))
    dep = require_capability(Capability.KB_TAXONOMY)
    assert await dep(perms=perms) is perms


@pytest.mark.asyncio
async def test_require_capability_403_when_missing() -> None:
    perms = _perms_with(caps=frozenset({Capability.KB_CONNECTORS}))
    dep = require_capability(Capability.KB_TAXONOMY)
    with pytest.raises(HTTPException) as exc:
        await dep(perms=perms)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# Section 6 — require_platform_admin / require_platform_unlocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_platform_admin_passes_for_admin_in_platform_org() -> None:
    perms = _perms_with(role=ProfileRole.ADMIN, is_platform_admin=True)
    dep = require_platform_admin()
    assert await dep(perms=perms) is perms


@pytest.mark.asyncio
async def test_require_platform_admin_403_for_tenant_org() -> None:
    perms = _perms_with(role=ProfileRole.ADMIN, is_platform_admin=False)
    dep = require_platform_admin()
    with pytest.raises(HTTPException) as exc:
        await dep(perms=perms)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    "role",
    [ProfileRole.PERSONAL, ProfileRole.COMPANY, ProfileRole.KB_MANAGER, ProfileRole.GROUP_MANAGER],
)
@pytest.mark.asyncio
async def test_require_platform_admin_403_for_non_admin_in_platform_org(role: ProfileRole) -> None:
    """Phase 1 follow-up: the imperative pattern that this gate replaces is
    `_require_admin(caller_user) + _require_platform_admin(caller_org)`.
    Both must hold. A `company`-rol user IN the platform org must NOT pass
    this gate — that would be permissiver dan vandaag."""
    perms = _perms_with(role=role, is_platform_admin=True)
    dep = require_platform_admin()
    with pytest.raises(HTTPException) as exc:
        await dep(perms=perms)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_require_platform_unlocked_passes_when_in_set() -> None:
    perms = _perms_with(platform_features=frozenset({"partner_api"}))
    dep = require_platform_unlocked("partner_api")
    assert await dep(perms=perms) is perms


@pytest.mark.asyncio
async def test_require_platform_unlocked_403_when_locked() -> None:
    perms = _perms_with(platform_features=frozenset())
    dep = require_platform_unlocked("partner_api")
    with pytest.raises(HTTPException) as exc:
        await dep(perms=perms)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert isinstance(exc.value.detail, dict)
    assert exc.value.detail.get("error_code") == "feature_not_unlocked"
    assert exc.value.detail.get("feature") == "partner_api"
