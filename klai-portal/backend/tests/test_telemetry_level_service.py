"""SPEC-PRIVACY-QUERY-SHADOW-001 — service-layer tests for set_telemetry_level.

Pure unit tests with mocked AsyncSession + Redis + audit. The service
function is the shared core used by both the internal-admin endpoint
(REQ-11) and the tenant self-service endpoint (REQ-15).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeOrg:
    """Mimics PortalOrg with the fields the service touches."""

    def __init__(self, level: str = "shadow") -> None:
        self.id = 42
        # The LiteLLM hook keys kb_ver:{zitadel_org_id}:* on the Zitadel
        # string, so the service must invalidate using THIS, not self.id.
        self.zitadel_org_id = "zitadel-org-42"
        self.telemetry_level = level


def _scalar_result(value: object) -> MagicMock:
    """Build a mock that quacks like SQLAlchemy's `Result`."""
    res = MagicMock()
    res.scalar_one_or_none.return_value = value
    return res


@pytest.mark.asyncio
async def test_set_telemetry_level_happy_path(monkeypatch):
    """shadow → full updates the column, invalidates cache, writes audit."""
    from app.services.telemetry_level import set_telemetry_level

    org = _FakeOrg(level="shadow")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))
    db.commit = AsyncMock()

    invalidate_mock = AsyncMock()
    audit_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.telemetry_level.invalidate_kb_cache",
        invalidate_mock,
    )
    monkeypatch.setattr(
        "app.services.telemetry_level.log_event",
        audit_mock,
    )

    old, new = await set_telemetry_level(
        db,
        org_id=42,
        new_level="full",
        operator_kind="operator",
        operator_user_id="internal-admin",
        reason="Investigating ticket #1234",
    )

    assert old == "shadow"
    assert new == "full"
    assert org.telemetry_level == "full"
    db.commit.assert_awaited_once()
    invalidate_mock.assert_awaited_once_with("zitadel-org-42")
    audit_mock.assert_awaited_once()
    audit_call = audit_mock.await_args
    assert audit_call.kwargs["action"] == "telemetry_level_changed"
    assert audit_call.kwargs["details"]["old_level"] == "shadow"
    assert audit_call.kwargs["details"]["new_level"] == "full"
    assert audit_call.kwargs["details"]["operator_kind"] == "operator"
    assert audit_call.kwargs["details"]["reason"] == "Investigating ticket #1234"


@pytest.mark.asyncio
async def test_set_telemetry_level_idempotent_noop(monkeypatch, support_purge):
    """Setting the same level skips commit but still audits + invalidates."""
    from app.services.telemetry_level import set_telemetry_level

    org = _FakeOrg(level="shadow")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(org))
    db.commit = AsyncMock()

    invalidate_mock = AsyncMock()
    audit_mock = AsyncMock()
    monkeypatch.setattr(
        "app.services.telemetry_level.invalidate_kb_cache",
        invalidate_mock,
    )
    monkeypatch.setattr(
        "app.services.telemetry_level.log_event",
        audit_mock,
    )

    old, new = await set_telemetry_level(
        db,
        org_id=42,
        new_level="shadow",
        operator_kind="tenant_admin",
        operator_user_id="zitadel-user-1",
        reason="re-applying same level via UI",
    )

    assert (old, new) == ("shadow", "shadow")
    db.commit.assert_not_awaited()  # no-op skip
    invalidate_mock.assert_awaited_once()  # always invalidates (escape hatch)
    audit_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_telemetry_level_rejects_invalid_level(monkeypatch):
    """Defensive: invalid level raises ValueError before DB query."""
    from app.services.telemetry_level import set_telemetry_level

    db = AsyncMock()
    db.execute = AsyncMock()

    with pytest.raises(ValueError, match="invalid telemetry_level"):
        await set_telemetry_level(
            db,
            org_id=42,
            new_level="bogus",  # type: ignore[arg-type]
            operator_kind="operator",
            operator_user_id="internal-admin",
            reason="anything",
        )

    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_set_telemetry_level_rejects_empty_reason(monkeypatch):
    from app.services.telemetry_level import set_telemetry_level

    db = AsyncMock()

    with pytest.raises(ValueError, match="reason must be non-empty"):
        await set_telemetry_level(
            db,
            org_id=42,
            new_level="full",
            operator_kind="operator",
            operator_user_id="op",
            reason="   ",
        )


@pytest.mark.asyncio
async def test_set_telemetry_level_rejects_overlong_reason(monkeypatch):
    from app.services.telemetry_level import set_telemetry_level

    db = AsyncMock()

    with pytest.raises(ValueError, match="500"):
        await set_telemetry_level(
            db,
            org_id=42,
            new_level="full",
            operator_kind="operator",
            operator_user_id="op",
            reason="x" * 501,
        )


@pytest.mark.asyncio
async def test_set_telemetry_level_org_not_found(monkeypatch):
    from app.services.telemetry_level import set_telemetry_level

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))

    monkeypatch.setattr(
        "app.services.telemetry_level.invalidate_kb_cache",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.telemetry_level.log_event",
        AsyncMock(),
    )

    with pytest.raises(LookupError):
        await set_telemetry_level(
            db,
            org_id=999,
            new_level="full",
            operator_kind="operator",
            operator_user_id="op",
            reason="missing org",
        )


@pytest.fixture
def support_purge(monkeypatch):
    context = MagicMock()
    context.__aenter__.return_value = AsyncMock()
    monkeypatch.setattr("app.core.database.tenant_scoped_session", MagicMock(return_value=context))
    purge = AsyncMock(return_value=0)
    monkeypatch.setattr("app.services.support_cases.purge_support_cases_for_org", purge)
    return purge


@pytest.mark.asyncio
async def test_failed_opt_out_cleanup_runs_again_on_retry(monkeypatch, support_purge):
    from app.services.telemetry_level import set_telemetry_level

    org = _FakeOrg(level="full")
    db = AsyncMock()
    db.execute.return_value = _scalar_result(org)
    monkeypatch.setattr("app.services.telemetry_level.invalidate_kb_cache", AsyncMock())
    monkeypatch.setattr("app.services.telemetry_level.log_event", AsyncMock())
    support_purge.side_effect = [RuntimeError("cleanup unavailable"), 0]
    kwargs = dict(
        org_id=42, new_level="shadow", operator_kind="operator", operator_user_id="internal-admin", reason="Opt out"
    )

    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        await set_telemetry_level(db, **kwargs)
    assert org.telemetry_level == "shadow"

    assert await set_telemetry_level(db, **kwargs) == ("shadow", "shadow")
    assert support_purge.await_count == 2
