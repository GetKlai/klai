"""
Shared test configuration.

Sets required env vars before any app module is imported.
"""

import os
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

# ---------------------------------------------------------------------------
# Suppress 'coroutine was never awaited' from mocked-asyncio tests.
#
# When a test patches asyncio.create_task, the coroutine passed to it is
# created but never started.  Python emits this warning via sys.unraisablehook
# during GC — which happens after pytest fixtures have already cleaned up, so
# a fixture-scoped override arrives too late.  A module-level hook installed
# at import time persists for the full session including interpreter shutdown.
#
# Python 3.13 no longer exposes sys.UnraisableHookArgs as a runtime attribute,
# so we annotate the hook argument as Any.
# ---------------------------------------------------------------------------
_original_unraisablehook = sys.unraisablehook


def _hook(unraisable: Any) -> None:
    if isinstance(unraisable.exc_value, RuntimeWarning) and "was never awaited" in str(unraisable.exc_value):
        return
    _original_unraisablehook(unraisable)


sys.unraisablehook = _hook

# ---------------------------------------------------------------------------
# Env vars for pydantic-settings validation (read at module import time)
# ---------------------------------------------------------------------------
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("ZITADEL_PAT", "test-pat")
os.environ.setdefault("SSO_COOKIE_KEY", "R1c1-s96uO9Yz7k1E0kN6qz52gzd9PwNbAeZaks_PIc=")
os.environ.setdefault("PORTAL_SECRETS_KEY", "0" * 64)  # 64-char hex; test placeholder only
os.environ.setdefault("ENCRYPTION_KEY", "1" * 64)  # 64-char hex; test placeholder only
os.environ.setdefault("VEXA_WEBHOOK_SECRET", "test-vexa-webhook-secret")  # SEC-013 F-033
os.environ.setdefault("MONEYBIRD_WEBHOOK_TOKEN", "test-moneybird-webhook-token")  # SPEC-SEC-WEBHOOK-001 REQ-3
os.environ.setdefault("MCP_OAUTH_ISSUER_BASE_URL", "https://my.test.local")  # SPEC-MCP-AUTH-001 REQ-7
os.environ.setdefault("MCP_OAUTH_RESOURCE_URL", "https://mcp.test.local")  # SPEC-MCP-AUTH-001 REQ-8
os.environ.setdefault("ZITADEL_IDP_GOOGLE_ID", "test-google-idp-id")  # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.6
os.environ.setdefault("ZITADEL_IDP_MICROSOFT_ID", "test-microsoft-idp-id")  # SPEC-SEC-AUTH-COVERAGE-001 REQ-2.6
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret-x" * 2)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-1
os.environ.setdefault(
    "KLAI_CONNECTOR_SECRET", "test-klai-connector-secret" * 2
)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-2
os.environ.setdefault(
    "KNOWLEDGE_INGEST_SECRET", "test-knowledge-ingest-secret" * 2
)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-3
os.environ.setdefault(
    "RETRIEVAL_API_INTERNAL_SECRET", "test-retrieval-secret" * 2
)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-4
os.environ.setdefault("DOCS_INTERNAL_SECRET", "test-docs-internal-secret" * 2)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-5
os.environ.setdefault(
    "ZITADEL_PORTAL_CLIENT_SECRET", "test-zitadel-portal-client-secret"
)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-6
# Audit 2026-05-05 finding 4: BFF_SESSION_KEY and SSO_COOKIE_KEY MUST be
# distinct test values so a bug where code accidentally reads the wrong
# key does not silently decrypt successfully (both Fernet keys produce
# valid ciphertext for any payload). Use a generated-different second
# Fernet key here.
os.environ.setdefault(
    "BFF_SESSION_KEY", "aBcDeFgHiJkLmNoPqRsTuVwXyZ012345678901234="
)  # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-10

# ---------------------------------------------------------------------------
# Auto-discoverable fixtures (SPEC-SEC-AUTH-COVERAGE-001 REQ-5.6)
#
# Re-export the respx_zitadel fixture from auth_test_helpers so all auth
# endpoint test modules pick it up via pytest's conftest discovery without
# needing to import + alias it (the import-style triggers F811 redefinition
# warnings when test functions take it as a parameter).
# ---------------------------------------------------------------------------
from auth_test_helpers import respx_zitadel  # noqa: E402, F401

# ---------------------------------------------------------------------------
# Shared fakeredis fixture for SPEC-SEC-SESSION-001 + future Redis-backed code
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[Any]:
    """In-memory ``fakeredis.aioredis.FakeRedis`` swapped into the singleton
    pool for the duration of one test.

    Matches the production ``get_redis_pool()`` contract:
    - ``decode_responses=True`` so HSET / HGETALL / GET return ``str`` not bytes.
    - Same instance returned across all ``get_redis_pool()`` calls in the test.

    Tests can directly inspect the fake via the yielded handle (e.g.
    ``await fake_redis.hgetall("totp_pending:T")``).
    """
    import fakeredis.aioredis

    from app.services import redis_client

    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original = redis_client._pool_holder["pool"]
    redis_client._pool_holder["pool"] = fake
    try:
        yield fake
    finally:
        redis_client._pool_holder["pool"] = original
        await fake.aclose()


# ---------------------------------------------------------------------------
# SPEC-SEC-HYGIENE-001 REQ-20: pre-populate the tenant-slug allowlist cache
# so existing login/audit tests that exercise `_validate_callback_url`
# don't trigger a real DB load via `_load_tenant_slugs_from_db`. The set
# below covers every callback hostname referenced in the test suite.
# Tests that specifically exercise the cache invalidate it via
# `auth_module.invalidate_tenant_slug_cache()` in their own fixtures.
# ---------------------------------------------------------------------------
import math  # noqa: E402

from app.api import auth as _auth_module  # noqa: E402

_auth_module._tenant_slug_cache = {
    "chat",  # test_auth_security login flows
    "voys",  # test_validate_callback_url + general portal tests
    "getklai",
    "alpha",  # test_widget_jwt_per_tenant (REQ-24, future test)
    "bravo",
    "test",
    "acme",
    "portal",  # test_idp_callback_provision uses portal.getklai.com as the IDP-finalised callback host
}
_auth_module._tenant_slug_cache_expiry = math.inf


# ---------------------------------------------------------------------------
# Phase 1 test factories — SPEC-PORTAL-RBAC-REFACTOR-001 REQ-1F
#
# Replaces the temporary helpers in tests/role_matrix_helpers.py for any new
# Phase 2+ test that wants a typed PortalUser / PortalOrg mock. The legacy
# helpers continue to work alongside these until Phase 2 migrates the
# 132 characterization tests.
# ---------------------------------------------------------------------------


def make_user(
    *,
    role: object = "admin",
    zitadel_user_id: str = "uid-test",
    org_id: int = 101,
    user_pk: int = 9001,
    email: str | None = None,
):
    """Synthetic PortalUser mock with the chosen role.

    Accepts either ``ProfileRole.X`` (preferred for new tests) or the bare
    string form (for tests that have not migrated yet). Both resolve via
    ``str(role)`` to the underlying enum value.
    """
    from unittest.mock import MagicMock

    from app.models.portal import PortalUser

    role_str = role if isinstance(role, str) else str(role)
    user = MagicMock(spec=PortalUser)
    user.role = role_str
    user.zitadel_user_id = zitadel_user_id
    user.org_id = org_id
    user.id = user_pk
    user.email = email or f"{role_str}@example.com"
    user.first_name = role_str.capitalize()
    user.last_name = "Tester"
    user.status = "active"
    user.preferred_language = "nl"
    return user


def make_org(
    *,
    org_id: int = 101,
    slug: str = "voys",
    plan: str = "knowledge",
    enabled_addons: list[str] | None = None,
    platform_unlocked_features: list[str] | None = None,
    provisioning_status: str = "active",
    name: str | None = None,
):
    """Synthetic PortalOrg mock with sensible defaults for permissions tests."""
    from unittest.mock import MagicMock

    from app.models.portal import PortalOrg

    org = MagicMock(spec=PortalOrg)
    org.id = org_id
    org.slug = slug
    org.plan = plan
    org.zitadel_org_id = f"zitadel-org-{org_id}"
    org.enabled_addons = enabled_addons or []
    org.platform_unlocked_features = platform_unlocked_features or []
    org.provisioning_status = provisioning_status
    org.name = name or slug.capitalize()
    org.moneybird_subscription_id = "ms-test-123"
    org.moneybird_contact_id = "mc-test-123"
    org.billing_cycle = "monthly"
    org.seats = 5
    org.billing_status = "active"
    org.mcp_servers = []
    return org


def make_perms(
    *,
    role: object = "admin",
    user_id: str = "uid-test",
    org_id: int = 101,
    org_slug: str = "voys",
    plan: str = "knowledge",
    enabled_addons: list[str] | None = None,
    platform_unlocked_features: list[str] | None = None,
    is_platform_admin: bool = False,
    provisioning_status: str = "active",
):
    """Synthetic UserPermissions for endpoints that take ``perms=`` directly.

    Replaces the legacy ``patch("..._get_caller_org", return_value=(uid, org,
    user))`` test pattern. Produces a frozen ``UserPermissions`` with derived
    capabilities + products consistent with the other inputs.

    SPEC-PORTAL-EXTENSIONS-UNIFY-001 (2026-05-12): ``UserPermissions.enabled_addons``
    is gone. The ``enabled_addons=`` parameter is kept here as a back-compat
    alias for existing call-sites — its value is merged into
    ``platform_unlocked_features`` so callers do not need to rewrite all at once.
    Prefer ``platform_unlocked_features=`` in new tests.
    """
    from app.core.features import derive_user_products
    from app.core.permissions import UserPermissions
    from app.core.plan_limits import get_plan_limits
    from app.core.profiles import PROFILE_CAPABILITIES, Capability, ProfileRole

    role_enum = role if isinstance(role, ProfileRole) else ProfileRole(str(role))
    # Back-compat: legacy enabled_addons param merges into platform_unlocked_features.
    plat_features = frozenset((platform_unlocked_features or []) + (enabled_addons or []))

    if role_enum == ProfileRole.ADMIN:
        knowledge_caps = get_plan_limits("knowledge").capabilities
        eff_caps = frozenset(Capability(c) for c in knowledge_caps)
    else:
        role_caps = PROFILE_CAPABILITIES.get(role_enum.value, frozenset())
        plan_caps = get_plan_limits(plan).capabilities
        eff_caps = frozenset(Capability(c) for c in (set(role_caps) & set(plan_caps)))

    eff_products = frozenset(derive_user_products(role_enum.value, plan, list(plat_features)))

    return UserPermissions(
        user_id=user_id,
        org_id=org_id,
        org_slug=org_slug,
        role=role_enum,
        plan=plan,
        platform_unlocked_features=plat_features,
        effective_role=role_enum,
        effective_capabilities=eff_caps,
        effective_products=eff_products,
        effective_kb_limits=get_plan_limits(plan),
        is_platform_admin=is_platform_admin,
        provisioning_status=provisioning_status,
    )
