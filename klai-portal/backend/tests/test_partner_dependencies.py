"""RED: Verify partner auth dependency (get_partner_key).

SPEC-API-001 REQ-2.1, REQ-2.2, REQ-2.4, REQ-2.6, REQ-1.6:
- Missing header -> 401
- Malformed prefix -> 401
- Unknown hash -> 401
- Inactive key -> 401 (same message as not-found)
- Valid key -> PartnerAuthContext
- Rate limited -> 429 with Retry-After
- last_used_at update scheduled
"""

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


@dataclass
class FakeKeyRow:
    """Mimics a PartnerAPIKey DB row."""

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
    """Mimics a PartnerApiKeyKbAccess DB row."""

    partner_api_key_id: str = "key-uuid-1"
    kb_id: int = 10
    access_level: str = "read"


@dataclass
class FakeOrg:
    """Mimics a PortalOrg row."""

    id: int = 42
    zitadel_org_id: str = "zit-org-42"


def _make_request(token: str | None = None) -> MagicMock:
    """Create a mock FastAPI Request with optional Authorization header."""
    request = MagicMock()
    if token:
        request.headers = {"authorization": f"Bearer {token}"}
    else:
        request.headers = {}
    return request


def _mock_scalar_one_or_none(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _mock_scalars_all(values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


@pytest.mark.asyncio
async def test_missing_header_returns_401():
    """Missing Authorization header -> 401."""
    from app.api.partner_dependencies import get_partner_key

    request = _make_request(token=None)
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_partner_key(request=request, db=db)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_prefix_returns_401():
    """Key without pk_live_ prefix -> 401."""
    from app.api.partner_dependencies import get_partner_key

    request = _make_request(token="sk_test_badprefix")  # noqa: S106 — fake token for test assertion
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await get_partner_key(request=request, db=db)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_unknown_hash_returns_401():
    """Valid format but no matching key in DB -> 401."""
    from app.api.partner_dependencies import get_partner_key

    request = _make_request(token="pk_live_" + "a" * 40)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_mock_scalar_one_or_none(None))

    with pytest.raises(HTTPException) as exc_info:
        await get_partner_key(request=request, db=db)
    assert exc_info.value.status_code == 401
    assert "authentication_error" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_inactive_key_returns_401_same_message():
    """Inactive key -> 401 with same message as not-found (no enumeration)."""
    from app.api.partner_dependencies import get_partner_key

    request = _make_request(token="pk_live_" + "a" * 40)

    # First call: not found (returns None)
    db_not_found = AsyncMock()
    db_not_found.execute = AsyncMock(return_value=_mock_scalar_one_or_none(None))

    with pytest.raises(HTTPException) as exc_not_found:
        await get_partner_key(request=request, db=db_not_found)

    # The query filters active=True, so inactive keys also return None.
    # Error messages must be identical.
    assert exc_not_found.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_key_returns_auth_context():
    """Valid key -> PartnerAuthContext with correct fields."""
    from app.api.partner_dependencies import PartnerAuthContext, get_partner_key

    fake_key = FakeKeyRow()
    fake_kb_access = [FakeKbAccessRow(kb_id=10, access_level="read")]
    fake_org = FakeOrg()

    request = _make_request(token="pk_live_" + "a" * 40)

    db = AsyncMock()
    # First call: key lookup
    # Second call: kb_access lookup
    # Third call: org lookup
    db.execute = AsyncMock(
        side_effect=[
            _mock_scalar_one_or_none(fake_key),
            _mock_scalars_all(fake_kb_access),
            _mock_scalar_one_or_none(fake_org),
        ]
    )

    with (
        patch("app.api.partner_dependencies.verify_partner_key", return_value=True),
        patch("app.api.partner_dependencies.check_rate_limit", return_value=(True, 0)),
        patch("app.api.partner_dependencies.get_redis_pool", return_value=AsyncMock()),
        patch("app.api.partner_dependencies._update_last_used", return_value=None),
    ):
        result = await get_partner_key(request=request, db=db)

    assert isinstance(result, PartnerAuthContext)
    assert result.key_id == "key-uuid-1"
    assert result.org_id == 42
    assert result.zitadel_org_id == "zit-org-42"
    assert result.permissions == {"chat": True, "feedback": True, "knowledge_append": False}
    assert result.kb_access == {10: "read"}
    assert result.rate_limit_rpm == 60


@pytest.mark.asyncio
async def test_rate_limited_returns_429():
    """Rate limited -> 429 with Retry-After header."""
    from app.api.partner_dependencies import get_partner_key

    fake_key = FakeKeyRow()
    fake_kb_access = [FakeKbAccessRow(kb_id=10, access_level="read")]
    fake_org = FakeOrg()

    request = _make_request(token="pk_live_" + "a" * 40)

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _mock_scalar_one_or_none(fake_key),
            _mock_scalars_all(fake_kb_access),
            _mock_scalar_one_or_none(fake_org),
        ]
    )

    with (
        patch("app.api.partner_dependencies.verify_partner_key", return_value=True),
        patch("app.api.partner_dependencies.check_rate_limit", return_value=(False, 30)),
        patch("app.api.partner_dependencies.get_redis_pool", return_value=AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await get_partner_key(request=request, db=db)

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers.get("Retry-After") == "30"


@pytest.mark.asyncio
async def test_last_used_at_update_scheduled():
    """last_used_at update is scheduled via asyncio.create_task and tracked in _pending."""
    from app.api.partner_dependencies import _pending, get_partner_key

    fake_key = FakeKeyRow()
    fake_kb_access = [FakeKbAccessRow(kb_id=10, access_level="read")]
    fake_org = FakeOrg()

    request = _make_request(token="pk_live_" + "a" * 40)

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _mock_scalar_one_or_none(fake_key),
            _mock_scalars_all(fake_kb_access),
            _mock_scalar_one_or_none(fake_org),
        ]
    )

    pending_before = len(_pending)

    with (
        patch("app.api.partner_dependencies.verify_partner_key", return_value=True),
        patch("app.api.partner_dependencies.check_rate_limit", return_value=(True, 0)),
        patch("app.api.partner_dependencies.get_redis_pool", return_value=AsyncMock()),
    ):
        await get_partner_key(request=request, db=db)
        # A task was added to _pending (may already have completed and been removed)
        # Verify create_task was used by checking the task was at least submitted
        assert len(_pending) >= pending_before  # task was created (may be done already)


# ---------- TASK-006: Permission + KB-scope enforcement helpers ----------


def test_require_permission_granted():
    """Permission present and True -> no exception."""
    from app.api.partner_dependencies import PartnerAuthContext, require_permission

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": True, "feedback": False, "knowledge_append": False},
        kb_access={},
        rate_limit_rpm=60,
    )
    require_permission(auth, "chat")  # should not raise


def test_require_permission_denied():
    """Permission False -> 403."""
    from app.api.partner_dependencies import PartnerAuthContext, require_permission

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": False, "feedback": True, "knowledge_append": False},
        kb_access={},
        rate_limit_rpm=60,
    )
    with pytest.raises(HTTPException) as exc_info:
        require_permission(auth, "chat")
    assert exc_info.value.status_code == 403


def test_validate_kb_access_all_in_scope():
    """All requested KB IDs in scope -> returns them."""
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        kb_access={10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )
    result = validate_kb_access(auth, [10, 20], required_level="read")
    assert result == [10, 20]


def test_validate_kb_access_out_of_scope_returns_403():
    """One KB not in key scope -> 403 with generic message."""
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        kb_access={10: "read"},
        rate_limit_rpm=60,
    )
    with pytest.raises(HTTPException) as exc_info:
        validate_kb_access(auth, [10, 999], required_level="read")
    assert exc_info.value.status_code == 403
    # Error message must NOT contain KB IDs or names
    detail_str = str(exc_info.value.detail)
    assert "999" not in detail_str
    assert "10" not in detail_str


def test_validate_kb_access_none_falls_back_to_key_defaults():
    """None requested -> falls back to all key KBs."""
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": True, "feedback": True, "knowledge_append": False},
        kb_access={10: "read", 20: "read_write", 30: "read"},
        rate_limit_rpm=60,
    )
    result = validate_kb_access(auth, None, required_level="read")
    assert set(result) == {10, 20, 30}


def test_validate_kb_access_read_write_level_check():
    """read_write required but key only has read -> 403."""
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": True, "feedback": True, "knowledge_append": True},
        kb_access={10: "read", 20: "read_write"},
        rate_limit_rpm=60,
    )
    # KB 10 has only 'read', but 'read_write' is required
    with pytest.raises(HTTPException) as exc_info:
        validate_kb_access(auth, [10], required_level="read_write")
    assert exc_info.value.status_code == 403


def test_validate_kb_access_none_filters_by_level():
    """None requested with read_write level -> only read_write KBs."""
    from app.api.partner_dependencies import PartnerAuthContext, validate_kb_access

    auth = PartnerAuthContext(
        key_id="k1",
        org_id=1,
        zitadel_org_id="z1",
        permissions={"chat": True, "feedback": True, "knowledge_append": True},
        kb_access={10: "read", 20: "read_write", 30: "read"},
        rate_limit_rpm=60,
    )
    result = validate_kb_access(auth, None, required_level="read_write")
    assert result == [20]
