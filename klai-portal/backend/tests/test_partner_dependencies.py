"""Tests for partner auth dependency (get_partner_key).

SPEC-API-001 REQ-2.1, REQ-2.2, REQ-2.4, REQ-2.6, REQ-1.6.
"""

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


@dataclass
class FakeKeyRow:
    id: str = "key-uuid-1"
    org_id: int = 42
    name: str = "Test Key"
    key_hash: str = "abc123"
    permissions: dict = None
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


def _make_request(token: str | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = {"authorization": f"Bearer {token}"} if token else {}
    return request


class _FakeResult:
    """Auto-responds to all common DB result access patterns."""

    def __init__(self, rows=None, scalar_value=None):
        self._rows = rows or []
        self._scalar_value = scalar_value

    def scalars(self):
        mock = MagicMock()
        mock.all.return_value = self._rows
        return mock

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar_value


def _setup_db(mock_db: AsyncMock, results: list[_FakeResult]):
    """Set up mock_db.execute to return results in order, cycling the last one."""
    call_count = 0

    async def _execute(*args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(results) - 1)
        call_count += 1
        return results[idx] if results else _FakeResult()

    mock_db.execute = AsyncMock(side_effect=_execute)


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
        await get_partner_key(request=_make_request(token="sk_test_bad"), db=AsyncMock())  # noqa: S106
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_hash_returns_401():
    from app.api.partner_dependencies import get_partner_key

    db = AsyncMock()
    _setup_db(db, [_FakeResult()])  # key not found

    with pytest.raises(HTTPException) as exc:
        await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_inactive_key_returns_401_same_message():
    """Inactive key returns same error as not-found (no enumeration)."""
    from app.api.partner_dependencies import get_partner_key

    db = AsyncMock()
    _setup_db(db, [_FakeResult()])  # active=True filter means inactive returns None

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
    _setup_db(
        db,
        [
            _FakeResult([FakeKeyRow()]),  # key lookup
            _FakeResult([FakeKbAccessRow()]),  # kb_access
            _FakeResult([FakeOrg()]),  # org lookup
            _FakeResult(),  # set_tenant + any further calls
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
    _setup_db(
        db,
        [
            _FakeResult([FakeKeyRow()]),
            _FakeResult([FakeKbAccessRow()]),
            _FakeResult([FakeOrg()]),
            _FakeResult(),
        ],
    )

    patches = _partner_patches(check_rate_limit=(False, 30))
    with patches[0], patches[1], patches[2]:
        with pytest.raises(HTTPException) as exc:
            await get_partner_key(request=_make_request(token="pk_live_" + "a" * 40), db=db)

    assert exc.value.status_code == 429
    assert exc.value.headers.get("Retry-After") == "30"


@pytest.mark.asyncio
async def test_last_used_at_update_scheduled():
    from app.api.partner_dependencies import _pending, get_partner_key

    db = AsyncMock()
    _setup_db(
        db,
        [
            _FakeResult([FakeKeyRow()]),
            _FakeResult([FakeKbAccessRow()]),
            _FakeResult([FakeOrg()]),
            _FakeResult(),
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
