"""Tests for partner auth dependency (get_partner_key).

SPEC-API-001 REQ-2.1, REQ-2.2, REQ-2.4, REQ-2.6, REQ-1.6.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from helpers import FakeResult, setup_db

# ---------------------------------------------------------------------------
# Domain fakes (specific to this module's tests)
# ---------------------------------------------------------------------------


@dataclass
class FakeKeyRow:
    id: str = "key-uuid-1"
    org_id: int = 42
    name: str = "Test Key"
    key_hash: str = "abc123"
    permissions: dict | None = None
    rate_limit_rpm: int = 60
    active: bool = True
    last_used_at: datetime | None = None

    def __post_init__(self):
        if self.permissions is None:
            self.permissions = {"chat": True, "feedback": True, "knowledge_append": False}


@dataclass
class FakeKbAccessRow:
    partner_api_key_id: str = "key-uuid-1"
    kb_id: int = 10
    access_level: str = "read"


@dataclass
class FakeOrg:
    id: int = 42
    zitadel_org_id: str = "zit-org-42"
    # SPEC-SEC-HYGIENE-001 REQ-24: per-tenant widget JWT key derives from
    # tenant slug, so partner_dependencies needs `org.slug` available on
    # the row. Hard-coded value mirrors the populated allowlist set in
    # conftest.py so any downstream callback validation passes too.
    slug: str = "acme"
    # SPEC-PORTAL-RBAC-REFACTOR-001 Phase 5C: partner_api must be unlocked
    # for happy-path tests that exercise the full get_partner_key flow.
    platform_unlocked_features: list = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.platform_unlocked_features is None:
            # SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-1 (Finding B-1): chat-path
            # `_auth_via_session_token` calls assert_platform_unlocked(org, "widgets").
            # Default both features unlocked so partner_api + widget chat-flow
            # happy-paths pass; tests that exercise the locked-tenant path
            # override per-instance.
            self.platform_unlocked_features = ["partner_api", "widgets"]


def _make_request(token: str | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"} if token else {}
    return request


def _partner_patches(**overrides):
    """Context manager patching the external dependencies of get_partner_key."""
    defaults = {
        "verify_partner_key": True,
        "check_rate_limit": (True, 0),
        "get_redis_pool": AsyncMock(),
        "_update_last_used": None,
    }
    defaults.update(overrides)
    return (
        patch("app.api.partner_dependencies.verify_partner_key", return_value=defaults["verify_partner_key"]),
        patch("app.api.partner_dependencies.check_rate_limit", return_value=defaults["check_rate_limit"]),
        patch("app.api.partner_dependencies.get_redis_pool", return_value=defaults["get_redis_pool"]),
        patch("app.api.partner_dependencies._update_last_used", return_value=defaults["_update_last_used"]),
    )


# ---------------------------------------------------------------------------
# Auth: error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_header_returns_401():
    from app.api.partner_dependencies import get_partner_key

    with pytest.raises(HTTPException) as exc:
        await get_partner_key(request=_make_request(), db=AsyncMock())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_prefix_returns_401():
    from app.api.partner_dependencies import get_partner_key

    with pytest.raises(HTTPException) as exc:
        await get_partner_key(request=_make_request(token="sk_test_bad"), db=AsyncMock())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_hash_returns_401():
    from app.api.partner_dependencies import get_partner_key

    db = AsyncMock()
    setup_db(db, [FakeResult()])  # key not found

    with pytest.raises(HTTPException) as exc:
        await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_inactive_key_returns_401_same_message():
    """Inactive key returns same error as not-found (no enumeration)."""
    from app.api.partner_dependencies import get_partner_key

    db = AsyncMock()
    setup_db(db, [FakeResult()])  # active=True filter means inactive returns None

    with pytest.raises(HTTPException) as exc:
        await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Auth: happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_key_returns_auth_context():
    from app.api.partner_dependencies import PartnerAuthContext, get_partner_key

    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeKeyRow()]),  # key lookup
            FakeResult([FakeKbAccessRow()]),  # kb_access
            FakeResult([FakeOrg()]),  # org lookup
            FakeResult(),  # set_tenant + any further calls
        ],
    )

    patches = _partner_patches()
    with patches[0], patches[1], patches[2], patches[3]:
        result = await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)

    assert isinstance(result, PartnerAuthContext)
    assert result.key_id == "key-uuid-1"
    assert result.org_id == 42
    assert result.zitadel_org_id == "zit-org-42"
    assert result.kb_access == {10: "read"}


@pytest.mark.asyncio
async def test_rate_limited_returns_429():
    from app.api.partner_dependencies import get_partner_key

    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeKeyRow()]),
            FakeResult([FakeKbAccessRow()]),
            FakeResult([FakeOrg()]),
            FakeResult(),
        ],
    )

    patches = _partner_patches(check_rate_limit=(False, 30))
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)

    assert exc.value.status_code == 429
    assert exc.value.headers is not None
    assert exc.value.headers.get("Retry-After") == "30"


@pytest.mark.asyncio
async def test_last_used_at_update_scheduled():
    from app.api.partner_dependencies import _pending, get_partner_key

    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeKeyRow()]),
            FakeResult([FakeKbAccessRow()]),
            FakeResult([FakeOrg()]),
            FakeResult(),
        ],
    )

    pending_before = len(_pending)

    patches = _partner_patches()
    # Don't mock _update_last_used — let it schedule the real task
    with patches[0], patches[1], patches[2]:
        await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)
        assert len(_pending) >= pending_before


# ---------------------------------------------------------------------------
# Permission + KB-scope helpers (pure functions, no DB)
# ---------------------------------------------------------------------------


def test_require_permission_granted():
    from app.api.partner_dependencies import PartnerAuthContext, require_permission

    auth = PartnerAuthContext(
        key_id="k", org_id=1, zitadel_org_id="z", permissions={"chat": True}, kb_access={}, rate_limit_rpm=60
    )
    require_permission(auth, "chat")  # no exception


def test_require_permission_denied():
    from app.api.partner_dependencies import PartnerAuthContext, require_permission

    auth = PartnerAuthContext(
        key_id="k", org_id=1, zitadel_org_id="z", permissions={"chat": False}, kb_access={}, rate_limit_rpm=60
    )
    with pytest.raises(HTTPException) as exc:
        require_permission(auth, "chat")
    assert exc.value.status_code == 403


def test_validate_kb_access_all_in_scope():
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k",
        org_id=1,
        zitadel_org_id="z",
        permissions={},
        kb_access={10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )
    assert validate_kb_access(auth, [10, 20], required_level="read") == [10, 20]


def test_validate_kb_access_out_of_scope_returns_403():
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k", org_id=1, zitadel_org_id="z", permissions={}, kb_access={10: "read"}, rate_limit_rpm=60
    )
    with pytest.raises(HTTPException) as exc:
        validate_kb_access(auth, [10, 999], required_level="read")
    assert exc.value.status_code == 403
    assert "999" not in str(exc.value.detail)


def test_validate_kb_access_none_falls_back_to_key_defaults():
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k",
        org_id=1,
        zitadel_org_id="z",
        permissions={},
        kb_access={10: "read", 20: "read_write", 30: "read"},
        rate_limit_rpm=60,
    )
    assert set(validate_kb_access(auth, None, required_level="read")) == {10, 20, 30}


def test_validate_kb_access_read_write_level_check():
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k",
        org_id=1,
        zitadel_org_id="z",
        permissions={},
        kb_access={10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )
    with pytest.raises(HTTPException) as exc:
        validate_kb_access(auth, [10], required_level="read_write")
    assert exc.value.status_code == 403


def test_validate_kb_access_none_filters_by_level():
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k",
        org_id=1,
        zitadel_org_id="z",
        permissions={},
        kb_access={10: "read", 20: "read_write", 30: "read"},
        rate_limit_rpm=60,
    )
    assert validate_kb_access(auth, None, required_level="read_write") == [20]


# ---------------------------------------------------------------------------
# SPEC-SEC-006: Widget JWT revocation via DB cross-check of widget_kb_access
# ---------------------------------------------------------------------------


@dataclass
class FakeWidget:
    id: str = "widget-uuid-1"
    widget_id: str = "wgt_abcdef0123456789"
    org_id: int = 42


_TEST_WIDGET_SECRET = "test-widget-secret-at-least-32-bytes-long"


def _make_jwt(
    wgt_id: str = "wgt_abcdef0123456789",
    org_id: int = 42,
    kb_ids: list[int] | None = None,
    tenant_slug: str = "acme",
) -> str:
    """Encode a widget session JWT using the production helper.

    SPEC-SEC-HYGIENE-001 REQ-24: tokens are signed with an HKDF-derived
    per-tenant key, so the test must use the same `generate_session_token`
    code path as production. `tenant_slug` defaults to ``"acme"`` to match
    `FakeOrg.slug`, so `partner_dependencies._auth_via_session_token` (which
    looks up `org.slug` and re-derives the same key) verifies successfully.
    """
    from app.services.widget_auth import generate_session_token

    return generate_session_token(
        wgt_id=wgt_id,
        org_id=org_id,
        kb_ids=kb_ids if kb_ids is not None else [1, 2],
        secret=_TEST_WIDGET_SECRET,
        tenant_slug=tenant_slug,
    )


def _make_legacy_jwt(
    wgt_id: str = "wgt_abcdef0123456789",
    org_id: int = 42,
    kb_ids: list[int] | None = None,
    tenant_slug: str = "acme",
) -> str:
    """Encode a pre-kid widget session JWT for deploy-window fallback tests."""
    from app.services.widget_auth import _derive_tenant_key

    payload = {
        "wgt_id": wgt_id,
        "org_id": org_id,
        "kb_ids": kb_ids if kb_ids is not None else [1, 2],
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
        "jti": "legacy-test-jti",
    }
    return jwt.encode(payload, _derive_tenant_key(_TEST_WIDGET_SECRET, tenant_slug), algorithm="HS256")


def _session_patches(widget_secret: str = _TEST_WIDGET_SECRET):
    """Patch settings + redis pool + set_tenant for session-token auth tests.

    Returns a tuple of context managers; callers enter them in a `with`.
    """
    return (
        patch("app.api.partner_dependencies.settings.widget_jwt_secret", widget_secret),
        patch("app.api.partner_dependencies.get_redis_pool", AsyncMock(return_value=None)),
        patch("app.api.partner_dependencies.set_tenant", AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_session_token_all_kbs_valid_returns_full_scope():
    """JWT with all kb_ids still present in widget_kb_access → kb_access has all."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt(kb_ids=[1, 2, 3])
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),  # org lookup
            FakeResult([FakeWidget()]),  # widget lookup (set_tenant is patched)
            FakeResult(rows=[1, 2, 3]),  # widget_kb_access kb_ids
        ],
    )

    patches = _session_patches()
    with patches[0], patches[1], patches[2]:
        result = await get_partner_key(request=_make_request(token=token), db=db)

    assert result.kb_access == {1: "read", 2: "read", 3: "read"}
    assert result.org_id == 42
    assert result.key_id == "wgt_abcdef0123456789"


@pytest.mark.asyncio
async def test_session_token_uses_kid_header_without_legacy_payload_decode():
    """New JWTs must select the tenant key via kid, not unverified payload."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt(kb_ids=[1, 2, 3])
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),
            FakeResult([FakeWidget()]),
            FakeResult(rows=[1, 2, 3]),
        ],
    )

    patches = _session_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patch(
            "app.api.partner_dependencies._legacy_org_id_from_unsigned_session_payload",
            side_effect=AssertionError("legacy payload fallback should not run for kid JWTs"),
        ),
    ):
        result = await get_partner_key(request=_make_request(token=token), db=db)

    assert result.org_id == 42
    assert result.kb_access == {1: "read", 2: "read", 3: "read"}


@pytest.mark.asyncio
async def test_session_token_without_kid_uses_legacy_fallback_during_deploy_window():
    """Old in-flight JWTs without kid remain valid for one deploy window."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_legacy_jwt(kb_ids=[1, 2])
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),
            FakeResult([FakeWidget()]),
            FakeResult(rows=[1, 2]),
        ],
    )

    patches = _session_patches()
    with patches[0], patches[1], patches[2]:
        result = await get_partner_key(request=_make_request(token=token), db=db)

    assert result.org_id == 42
    assert result.kb_access == {1: "read", 2: "read"}


@pytest.mark.asyncio
async def test_session_token_rejects_verified_org_mismatch_after_kid_lookup():
    """kid-selected org and verified payload org_id must match exactly."""
    from app.api.partner_dependencies import get_partner_key
    from app.services.widget_auth import _derive_tenant_key, session_token_key_id

    secret = _TEST_WIDGET_SECRET
    token = jwt.encode(
        {
            "wgt_id": "wgt_abcdef0123456789",
            "org_id": 999,
            "kb_ids": [1],
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "jti": "org-mismatch-test",
        },
        _derive_tenant_key(secret, "acme"),
        algorithm="HS256",
        headers={"kid": session_token_key_id(42)},
    )
    db = AsyncMock()
    setup_db(db, [FakeResult([FakeOrg(id=42)])])
    set_tenant = AsyncMock()

    with (
        patch("app.api.partner_dependencies.settings.widget_jwt_secret", secret),
        patch("app.api.partner_dependencies.set_tenant", set_tenant),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token=token), db=db)

    assert exc.value.status_code == 401
    set_tenant.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_token_rejects_malformed_kid_without_legacy_fallback():
    """Malformed kid is a hard auth failure, not a reason to read payload claims."""
    from app.api.partner_dependencies import get_partner_key
    from app.services.widget_auth import _derive_tenant_key

    secret = _TEST_WIDGET_SECRET
    token = jwt.encode(
        {
            "wgt_id": "wgt_abcdef0123456789",
            "org_id": 42,
            "kb_ids": [1],
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "jti": "bad-kid-test",
        },
        _derive_tenant_key(secret, "acme"),
        algorithm="HS256",
        headers={"kid": "not-an-org-kid"},
    )
    db = AsyncMock()

    with (
        patch("app.api.partner_dependencies.settings.widget_jwt_secret", secret),
        patch(
            "app.api.partner_dependencies._legacy_org_id_from_unsigned_session_payload",
            side_effect=AssertionError("legacy fallback should not run when kid is present"),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token=token), db=db)

    assert exc.value.status_code == 401
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_session_token_partial_revocation_narrows_scope():
    """One of three KBs revoked → kb_access dict has remaining two entries."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt(kb_ids=[1, 2, 3])
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),
            FakeResult([FakeWidget()]),
            FakeResult(rows=[1, 3]),  # kb_id 2 revoked
        ],
    )

    patches = _session_patches()
    with patches[0], patches[1], patches[2]:
        result = await get_partner_key(request=_make_request(token=token), db=db)

    assert result.kb_access == {1: "read", 3: "read"}
    assert 2 not in result.kb_access


@pytest.mark.asyncio
async def test_session_token_full_revocation_returns_401():
    """All JWT kb_ids revoked (empty intersection) → 401 opaque."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt(kb_ids=[1, 2])
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),
            FakeResult([FakeWidget()]),
            FakeResult(rows=[]),  # all access revoked
        ],
    )

    patches = _session_patches()
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token=token), db=db)

    assert exc.value.status_code == 401
    assert exc.value.detail == {"error": {"type": "authentication_error", "message": "Invalid API key"}}


@pytest.mark.asyncio
async def test_session_token_disjoint_kbs_returns_401():
    """JWT kb_ids disjoint from DB (e.g. stale JWT after KB swap) → 401."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt(kb_ids=[1, 2])
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),
            FakeResult([FakeWidget()]),
            FakeResult(rows=[99, 100]),  # DB has different kb_ids
        ],
    )

    patches = _session_patches()
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token=token), db=db)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_session_token_widget_deleted_returns_401():
    """Widget row gone (deleted entirely) → 401 opaque, same shape."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt()
    db = AsyncMock()
    setup_db(
        db,
        [
            FakeResult([FakeOrg()]),
            FakeResult(rows=[]),  # widget not found
        ],
    )

    patches = _session_patches()
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token=token), db=db)

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_session_token_missing_secret_returns_401():
    """WIDGET_JWT_SECRET not configured → 401 (pre-existing behaviour, regression guard)."""
    from app.api.partner_dependencies import get_partner_key

    token = _make_jwt()
    db = AsyncMock()

    patches = _session_patches(widget_secret="")
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token=token), db=db)

    assert exc.value.status_code == 401
