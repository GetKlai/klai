"""Tests for SPEC-INGEST-UNIQUE-ARTIFACT-001 (audit finding 7).

Pin the contract:
- ``create_artifact`` swallows ``UniqueViolationError`` originating from the
  ``uq_artifacts_active_path`` partial unique index, fetches the winning
  artifact_id, logs the race at error level, and returns the winning id.
- ``UniqueViolationError`` from any *other* constraint (e.g. PK collision)
  re-raises — only the active-path race is handled silently.
- If the winning row has vanished between INSERT and SELECT (concurrent
  soft-delete), the function logs and re-raises rather than returning None.

These tests exercise the error-handling branch directly via ``asyncpg``
mocking. The migration itself is not exercised (it requires a real PG
instance with the partial-unique-index applied) — manual smoke test on
deploy is acceptance criterion 2 of the SPEC.

SPEC-TI-003-FOLLOWUP-001: ``create_artifact`` now takes
``asyncpg.Connection`` as its first argument. Tests pass a mock conn
instead of patching pg_store.get_pool.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import structlog.testing


def _build_unique_violation(constraint_name: str) -> asyncpg.UniqueViolationError:
    """Construct a UniqueViolationError carrying the given constraint name."""
    err = asyncpg.UniqueViolationError("simulated unique violation")
    err.constraint_name = constraint_name  # type: ignore[attr-defined]
    return err


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=None)
    return conn


@pytest.mark.asyncio
async def test_happy_path_returns_new_artifact_id():
    """No race: INSERT succeeds, function returns the freshly-generated id."""
    conn = _make_mock_conn()

    from knowledge_ingest.pg_store import create_artifact

    artifact_id = await create_artifact(
        conn,
        org_id="org1",
        kb_slug="kb1",
        path="docs/page.md",
        provenance_type="extracted",
        assertion_mode="factual",
        synthesis_depth=0,
        confidence=None,
        belief_time_start=0,
        belief_time_end=253402300800,
    )

    assert isinstance(artifact_id, str)
    assert len(artifact_id) == 36  # uuid4 hex with dashes
    conn.execute.assert_awaited_once()
    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_race_returns_winning_artifact_id_and_logs_error():
    """UniqueViolation from uq_artifacts_active_path: SELECT winner, log, return."""
    winning_id = "11111111-2222-3333-4444-555555555555"

    conn = _make_mock_conn()
    conn.execute = AsyncMock(side_effect=_build_unique_violation("uq_artifacts_active_path"))
    conn.fetchval = AsyncMock(return_value=winning_id)

    from knowledge_ingest.pg_store import create_artifact

    with structlog.testing.capture_logs() as captured:
        returned = await create_artifact(
            conn,
            org_id="org1",
            kb_slug="kb1",
            path="docs/page.md",
            provenance_type="extracted",
            assertion_mode="factual",
            synthesis_depth=0,
            confidence=None,
            belief_time_start=0,
            belief_time_end=253402300800,
        )

    assert returned == winning_id

    # Race is logged at error level so obs-001 fires
    race_events = [e for e in captured if e.get("event") == "artifact_create_race_lost"]
    assert len(race_events) == 1
    evt = race_events[0]
    assert evt["log_level"] == "error", (
        f"race must log at error to fire obs-001 alert; got {evt.get('log_level')!r}"
    )
    assert evt["org_id"] == "org1"
    assert evt["kb_slug"] == "kb1"
    assert evt["path"] == "docs/page.md"
    assert evt["winning_artifact_id"] == winning_id
    # The losing attempt id is also captured for forensic correlation
    assert "my_attempt_id" in evt
    assert evt["my_attempt_id"] != winning_id


@pytest.mark.asyncio
async def test_unrelated_unique_violation_reraises():
    """A unique violation from a different constraint must NOT be swallowed."""
    conn = _make_mock_conn()
    conn.execute = AsyncMock(side_effect=_build_unique_violation("artifacts_pkey"))

    from knowledge_ingest.pg_store import create_artifact

    with pytest.raises(asyncpg.UniqueViolationError):
        await create_artifact(
            conn,
            org_id="org1",
            kb_slug="kb1",
            path="docs/page.md",
            provenance_type="extracted",
            assertion_mode="factual",
            synthesis_depth=0,
            confidence=None,
            belief_time_start=0,
            belief_time_end=253402300800,
        )

    conn.fetchval.assert_not_called()


@pytest.mark.asyncio
async def test_race_with_vanished_winner_logs_and_reraises():
    """Edge case: winning row was soft-deleted between INSERT and SELECT."""
    violation = _build_unique_violation("uq_artifacts_active_path")
    conn = _make_mock_conn()
    conn.execute = AsyncMock(side_effect=violation)
    conn.fetchval = AsyncMock(return_value=None)  # winner vanished

    from knowledge_ingest.pg_store import create_artifact

    with structlog.testing.capture_logs() as captured:
        with pytest.raises(asyncpg.UniqueViolationError):
            await create_artifact(
                conn,
                org_id="org1",
                kb_slug="kb1",
                path="docs/page.md",
                provenance_type="extracted",
                assertion_mode="factual",
                synthesis_depth=0,
                confidence=None,
                belief_time_start=0,
                belief_time_end=253402300800,
            )

    no_winner_events = [
        e for e in captured if e.get("event") == "artifact_create_race_lost_no_winner"
    ]
    assert len(no_winner_events) == 1
    assert no_winner_events[0]["log_level"] == "error"


@pytest.mark.asyncio
async def test_race_returns_string_not_uuid_object():
    """asyncpg can return a UUID object; ingest_document expects a str."""
    import uuid as _uuid

    winning_uuid = _uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

    conn = _make_mock_conn()
    conn.execute = AsyncMock(side_effect=_build_unique_violation("uq_artifacts_active_path"))
    conn.fetchval = AsyncMock(return_value=winning_uuid)

    from knowledge_ingest.pg_store import create_artifact

    returned = await create_artifact(
        conn,
        org_id="org1",
        kb_slug="kb1",
        path="docs/page.md",
        provenance_type="extracted",
        assertion_mode="factual",
        synthesis_depth=0,
        confidence=None,
        belief_time_start=0,
        belief_time_end=253402300800,
    )

    assert isinstance(returned, str), (
        "create_artifact must return str even when asyncpg returns UUID -- "
        "downstream code (extra_payload, JSON in Procrastinate args) "
        "depends on string identity."
    )
    assert returned == str(winning_uuid)
