"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 Phase 2 — tenant_lifecycle audit helper.

Tests run without a real database. The AsyncSession is mocked with AsyncMock
so db.execute() is awaitable. db.add() is overridden to MagicMock per the
testing.md rule (AsyncMock makes all methods async, including the synchronous
Session.add).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.audit.tenant_lifecycle import (
    _VALID_ACTOR_TYPES,
    _VALID_EVENT_TYPES,
    emit_lifecycle_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db() -> AsyncMock:
    """Return a mock AsyncSession with synchronous db.add()."""
    db = AsyncMock()
    db.add = MagicMock()  # SQLAlchemy Session.add() is synchronous — override AsyncMock default
    return db


# ---------------------------------------------------------------------------
# Happy-path: correct row is inserted
# ---------------------------------------------------------------------------


class TestEmitLifecycleEventHappyPath:
    @pytest.mark.asyncio
    async def test_inserts_row_with_correct_event_type(self) -> None:
        db = _make_db()
        await emit_lifecycle_event(
            db,
            event_type="deprovisioned",
            org_id_snapshot=42,
            org_slug_snapshot="acme",
            org_name_snapshot="ACME Corp",
            actor_user_id="user-123",
            actor_type="owner",
        )
        db.execute.assert_awaited_once()
        # The positional arg is the text() object; the second positional arg is the params dict.
        args = db.execute.call_args.args
        params = args[1]
        assert params["event_type"] == "deprovisioned"

    @pytest.mark.asyncio
    async def test_inserts_row_with_correct_snapshot_values(self) -> None:
        db = _make_db()
        await emit_lifecycle_event(
            db,
            event_type="deprovisioned",
            org_id_snapshot=99,
            org_slug_snapshot="my-org",
            org_name_snapshot="My Organisation",
            actor_user_id="actor-abc",
            actor_type="platform_admin",
        )
        args = db.execute.call_args.args
        params = args[1]
        assert params["org_id"] == 99
        assert params["slug"] == "my-org"
        assert params["name"] == "My Organisation"
        assert params["actor"] == "actor-abc"
        assert params["actor_type"] == "platform_admin"

    @pytest.mark.asyncio
    async def test_properties_none_becomes_empty_json_object(self) -> None:
        db = _make_db()
        await emit_lifecycle_event(
            db,
            event_type="deprovisioned",
            org_id_snapshot=1,
            org_slug_snapshot="slug",
            org_name_snapshot="Name",
            actor_user_id=None,
            actor_type="system",
            properties=None,
        )
        args = db.execute.call_args.args
        params = args[1]
        assert params["props"] == json.dumps({})

    @pytest.mark.asyncio
    async def test_properties_dict_is_json_serialized(self) -> None:
        db = _make_db()
        props = {"step": "finalize", "attempt": 3}
        await emit_lifecycle_event(
            db,
            event_type="failed_deprovisioning",
            org_id_snapshot=7,
            org_slug_snapshot="beta-corp",
            org_name_snapshot="Beta Corp",
            actor_user_id=None,
            actor_type="system",
            properties=props,
        )
        args = db.execute.call_args.args
        params = args[1]
        assert json.loads(params["props"]) == props

    @pytest.mark.asyncio
    async def test_actor_user_id_none_is_passed_as_none(self) -> None:
        db = _make_db()
        await emit_lifecycle_event(
            db,
            event_type="deprovisioned",
            org_id_snapshot=5,
            org_slug_snapshot="x",
            org_name_snapshot="X",
            actor_user_id=None,
            actor_type="system",
        )
        args = db.execute.call_args.args
        params = args[1]
        assert params["actor"] is None

    @pytest.mark.asyncio
    async def test_all_valid_event_types_are_accepted(self) -> None:
        for event_type in _VALID_EVENT_TYPES:
            db = _make_db()
            await emit_lifecycle_event(
                db,
                event_type=event_type,
                org_id_snapshot=1,
                org_slug_snapshot="slug",
                org_name_snapshot="Name",
                actor_user_id=None,
                actor_type="system",
            )
            db.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_valid_actor_types_are_accepted(self) -> None:
        for actor_type in _VALID_ACTOR_TYPES:
            db = _make_db()
            await emit_lifecycle_event(
                db,
                event_type="deprovisioned",
                org_id_snapshot=1,
                org_slug_snapshot="slug",
                org_name_snapshot="Name",
                actor_user_id=None,
                actor_type=actor_type,
            )
            db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Validation: invalid inputs raise ValueError BEFORE db.execute is called
# ---------------------------------------------------------------------------


class TestEmitLifecycleEventValidation:
    @pytest.mark.asyncio
    async def test_invalid_event_type_raises_value_error(self) -> None:
        db = _make_db()
        with pytest.raises(ValueError, match="Invalid event_type"):
            await emit_lifecycle_event(
                db,
                event_type="suspended",  # not in CHECK constraint
                org_id_snapshot=1,
                org_slug_snapshot="slug",
                org_name_snapshot="Name",
                actor_user_id=None,
                actor_type="system",
            )
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_actor_type_raises_value_error(self) -> None:
        db = _make_db()
        with pytest.raises(ValueError, match="Invalid actor_type"):
            await emit_lifecycle_event(
                db,
                event_type="deprovisioned",
                org_id_snapshot=1,
                org_slug_snapshot="slug",
                org_name_snapshot="Name",
                actor_user_id=None,
                actor_type="robot",  # not in allowed set
            )
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_string_event_type_raises_value_error(self) -> None:
        db = _make_db()
        with pytest.raises(ValueError, match="Invalid event_type"):
            await emit_lifecycle_event(
                db,
                event_type="",
                org_id_snapshot=1,
                org_slug_snapshot="slug",
                org_name_snapshot="Name",
                actor_user_id=None,
                actor_type="system",
            )

    @pytest.mark.asyncio
    async def test_db_execute_error_propagates(self) -> None:
        """A DB error propagates up so the caller's transaction rolls back."""
        db = _make_db()
        db.execute.side_effect = RuntimeError("DB unavailable")
        with pytest.raises(RuntimeError, match="DB unavailable"):
            await emit_lifecycle_event(
                db,
                event_type="deprovisioned",
                org_id_snapshot=1,
                org_slug_snapshot="slug",
                org_name_snapshot="Name",
                actor_user_id=None,
                actor_type="system",
            )
