"""SPEC-PROV-001 M2 — state machine helper unit tests.

Covers happy-path transition, from_state mismatch detection, FORWARD_SEQUENCE
shape, and product_event emission.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# FORWARD_SEQUENCE structural assertions
# ---------------------------------------------------------------------------


def test_forward_sequence_matches_spec_r8_order() -> None:
    """SPEC-PROV-001 R8 defines the step order. If this test fails, either the
    sequence or the spec drifted — update both together."""
    from app.services.provisioning.state_machine import FORWARD_SEQUENCE

    expected_states = [
        "creating_zitadel_app",
        "creating_litellm_team",
        "creating_mongo_user",
        "writing_env_file",
        "creating_personal_kb",
        "creating_portal_kbs",
        "starting_container",
        "writing_caddyfile",
        "reloading_caddy",
        "creating_system_groups",
    ]
    assert [state for _, state in FORWARD_SEQUENCE] == expected_states


def test_stuck_candidate_states_excludes_terminal_and_entry_states() -> None:
    """The stuck-detector (M7) must only reconcile intermediate states —
    never `ready`, `queued`, `pending`, or `failed_rollback_*`."""
    from app.services.provisioning.state_machine import (
        STUCK_CANDIDATE_STATES,
        TERMINAL_STATES,
    )

    assert "pending" not in STUCK_CANDIDATE_STATES
    assert "queued" not in STUCK_CANDIDATE_STATES
    for terminal in TERMINAL_STATES:
        assert terminal not in STUCK_CANDIDATE_STATES


# ---------------------------------------------------------------------------
# transition_state behaviour
# ---------------------------------------------------------------------------


def _mock_db_with_org(current_state: str, org_id: int = 1, slug: str = "acme"):
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    org.provisioning_status = current_state

    result = MagicMock()
    result.scalar_one_or_none.return_value = org

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db, org


@pytest.mark.asyncio
async def test_happy_path_writes_new_status_and_commits() -> None:
    from app.services.provisioning.state_machine import transition_state

    db, org = _mock_db_with_org(current_state="queued")

    with patch("app.services.provisioning.state_machine.emit_event") as mock_emit:
        await transition_state(
            db,
            org_id=1,
            from_state="queued",
            to_state="creating_zitadel_app",
            step="zitadel_oidc_app",
        )

    assert org.provisioning_status == "creating_zitadel_app"
    db.commit.assert_awaited_once()

    # product_event fired with correct schema
    mock_emit.assert_called_once()
    kwargs = mock_emit.call_args.kwargs
    assert kwargs["event_type"] == "provisioning.state_transition"
    assert kwargs["org_id"] == 1
    assert kwargs["properties"]["from_state"] == "queued"
    assert kwargs["properties"]["to_state"] == "creating_zitadel_app"
    assert kwargs["properties"]["step"] == "zitadel_oidc_app"


@pytest.mark.asyncio
async def test_from_state_mismatch_raises_conflict() -> None:
    """When the caller expects from_state='X' but the row says 'Y', a
    StateTransitionConflict must be raised — this signals a concurrent run."""
    from app.services.provisioning.state_machine import (
        StateTransitionConflict,
        transition_state,
    )

    db, _ = _mock_db_with_org(current_state="creating_mongo_user")

    with pytest.raises(StateTransitionConflict) as excinfo:
        await transition_state(
            db,
            org_id=1,
            from_state="queued",
            to_state="creating_zitadel_app",
            step="zitadel_oidc_app",
        )

    assert "queued" in str(excinfo.value)
    assert "creating_mongo_user" in str(excinfo.value)
    # No commit on conflict
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_from_state_accepts_iterable_of_allowed_states() -> None:
    """``from_state`` may be a set/frozenset/list — used for the initial
    transition that accepts either ``pending`` or ``queued``."""
    from app.services.provisioning.state_machine import (
        ENTRY_STATES,
        transition_state,
    )

    # Current state is 'pending' and allowed set includes 'pending' → OK.
    db, org = _mock_db_with_org(current_state="pending")
    with patch("app.services.provisioning.state_machine.emit_event"):
        await transition_state(db, org_id=1, from_state=ENTRY_STATES, to_state="queued", step="begin")
    assert org.provisioning_status == "queued"

    # Current state is 'queued' and allowed set includes 'queued' → OK.
    db2, org2 = _mock_db_with_org(current_state="queued")
    with patch("app.services.provisioning.state_machine.emit_event"):
        await transition_state(db2, org_id=1, from_state=ENTRY_STATES, to_state="queued", step="begin")
    assert org2.provisioning_status == "queued"


@pytest.mark.asyncio
async def test_from_state_iterable_rejects_state_not_in_set() -> None:
    """Any state outside the allowed iterable raises StateTransitionConflict."""
    from app.services.provisioning.state_machine import (
        ENTRY_STATES,
        StateTransitionConflict,
        transition_state,
    )

    db, _ = _mock_db_with_org(current_state="creating_mongo_user")
    with pytest.raises(StateTransitionConflict):
        await transition_state(db, org_id=1, from_state=ENTRY_STATES, to_state="queued", step="begin")
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_from_state_none_skips_precondition_check() -> None:
    """The initial transition (pending/queued → first step) passes from_state=None
    so both legacy and new entry values are accepted."""
    from app.services.provisioning.state_machine import transition_state

    db, org = _mock_db_with_org(current_state="pending")

    with patch("app.services.provisioning.state_machine.emit_event"):
        await transition_state(
            db,
            org_id=1,
            from_state=None,
            to_state="queued",
            step="begin",
        )

    assert org.provisioning_status == "queued"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_org_raises_lookup_error() -> None:
    from app.services.provisioning.state_machine import transition_state

    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    with pytest.raises(LookupError):
        await transition_state(
            db,
            org_id=99999,
            from_state=None,
            to_state="queued",
            step="begin",
        )

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_for_update_is_used() -> None:
    """SPEC-PROV-001 R2/R14 require row-level locking via SELECT ... FOR UPDATE.
    Assert the executed statement compiles with FOR UPDATE."""
    from app.services.provisioning.state_machine import transition_state

    captured_stmts: list[str] = []

    async def fake_execute(stmt, *args, **kwargs):
        captured_stmts.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        org = MagicMock()
        org.provisioning_status = "queued"
        org.slug = "acme"
        result = MagicMock()
        result.scalar_one_or_none.return_value = org
        return result

    db = AsyncMock()
    db.execute = fake_execute
    db.commit = AsyncMock()

    with patch("app.services.provisioning.state_machine.emit_event"):
        await transition_state(
            db,
            org_id=1,
            from_state="queued",
            to_state="creating_zitadel_app",
            step="zitadel_oidc_app",
        )

    assert captured_stmts, "Expected at least one db.execute call"
    assert any("FOR UPDATE" in s.upper() for s in captured_stmts), (
        f"SPEC-PROV-001 R2/R14: transition_state must use SELECT ... FOR UPDATE. Got: {captured_stmts}"
    )


@pytest.mark.asyncio
async def test_duration_ms_measured_when_mark_step_start_called() -> None:
    """mark_step_start → transition_state records a non-negative duration_ms."""
    from app.services.provisioning.state_machine import (
        mark_step_start,
        transition_state,
    )

    db, _ = _mock_db_with_org(current_state="queued")

    with patch("app.services.provisioning.state_machine.emit_event") as mock_emit:
        mark_step_start(1, "zitadel_oidc_app")
        await transition_state(
            db,
            org_id=1,
            from_state="queued",
            to_state="creating_zitadel_app",
            step="zitadel_oidc_app",
        )

    duration = mock_emit.call_args.kwargs["properties"]["duration_ms"]
    assert duration is not None and duration >= 0


@pytest.mark.asyncio
async def test_duration_ms_none_when_no_mark_step_start() -> None:
    """Without a preceding mark_step_start, duration_ms is None rather than 0 —
    makes it easier to spot missing instrumentation in Grafana."""
    from app.services.provisioning.state_machine import transition_state

    db, _ = _mock_db_with_org(current_state="queued")

    with patch("app.services.provisioning.state_machine.emit_event") as mock_emit:
        await transition_state(
            db,
            org_id=2,
            from_state="queued",
            to_state="creating_zitadel_app",
            step="zitadel_oidc_app",
        )

    assert mock_emit.call_args.kwargs["properties"]["duration_ms"] is None
