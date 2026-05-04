"""Unit tests for SPEC-INFRA-TENANT-DELETE-001 R2 state-machine extension.

Verifies the new deprovisioning state-machine constants without touching the
database — the contract is purely about the constant values and their
relationship to the existing constants.

The database-side verification (CHECK constraint accepts new values) happens
in the integration tests under tests/integration/ that hit a real Postgres.
"""

from __future__ import annotations

from app.services.provisioning.state_machine import (
    DEPROVISION_ENTRY_STATES,
    DEPROVISION_TERMINAL_STATES,
    ENTRY_STATES,
    STUCK_CANDIDATE_STATES,
    TERMINAL_STATES,
)


class TestDeprovisionStates:
    """The three new deprovisioning states are well-formed and do not collide."""

    def test_deprovision_entry_states_contain_expected_values(self) -> None:
        assert DEPROVISION_ENTRY_STATES == frozenset({"ready", "failed_rollback_complete", "failed_deprovisioning"})

    def test_deprovision_terminal_states_contain_expected_values(self) -> None:
        assert DEPROVISION_TERMINAL_STATES == frozenset({"deprovisioned", "failed_deprovisioning"})

    def test_provision_entry_states_disjoint_from_deprovision_entry_states(self) -> None:
        # ENTRY_STATES is for `provision_tenant`; DEPROVISION_ENTRY_STATES is for
        # `deprovision_tenant`. They MUST NOT overlap — a row in `pending`/`queued`
        # is mid-provisioning and cannot be deprovisioned.
        assert ENTRY_STATES.isdisjoint(DEPROVISION_ENTRY_STATES)

    def test_failed_deprovisioning_is_both_entry_and_terminal(self) -> None:
        # Admin retry semantics: a row in `failed_deprovisioning` can be
        # re-entered for another deprovision attempt (entry), but absent that
        # retry it is a terminal failure observable to the operator (terminal).
        assert "failed_deprovisioning" in DEPROVISION_ENTRY_STATES
        assert "failed_deprovisioning" in DEPROVISION_TERMINAL_STATES

    def test_deprovisioned_is_terminal_only(self) -> None:
        # `deprovisioned` is the pre-hard-delete checkpoint. Never an entry —
        # the next state after `deprovisioned` is row-gone.
        assert "deprovisioned" not in DEPROVISION_ENTRY_STATES
        assert "deprovisioned" in DEPROVISION_TERMINAL_STATES


class TestStuckCandidateStatesIncludesDeprovisioning:
    """The startup stuck-detector must flag crashed deprovisioning runs."""

    def test_stuck_candidate_states_includes_deprovisioning(self) -> None:
        # A deprovisioning run that crashes mid-flight leaves the row in
        # `deprovisioning` state. The stuck-detector must pick it up so an
        # operator can retry via the admin endpoint.
        assert "deprovisioning" in STUCK_CANDIDATE_STATES

    def test_stuck_candidate_states_excludes_terminal_states(self) -> None:
        # Terminal states are by definition not stuck — they may persist
        # forever (e.g. a tenant in `failed_deprovisioning` awaiting a
        # support decision). Reconciliation would be wrong.
        for terminal in TERMINAL_STATES:
            assert terminal not in STUCK_CANDIDATE_STATES
        for terminal in DEPROVISION_TERMINAL_STATES:
            assert terminal not in STUCK_CANDIDATE_STATES

    def test_stuck_candidate_states_excludes_entry_states(self) -> None:
        # Entry states are pre-orchestrator and have no orchestrator to be
        # stuck on. `pending` is a signup transient; `queued` is awaiting
        # BackgroundTask start.
        for entry in ENTRY_STATES:
            assert entry not in STUCK_CANDIDATE_STATES


class TestNoStateOverlap:
    """The three new state strings do not appear in any pre-existing constant."""

    def test_new_states_not_in_provision_entry(self) -> None:
        new_states = {"deprovisioning", "deprovisioned"}  # failed_deprovisioning is intentionally entry
        assert new_states.isdisjoint(ENTRY_STATES)

    def test_new_states_not_in_provision_terminal(self) -> None:
        new_states = {"deprovisioning", "deprovisioned", "failed_deprovisioning"}
        assert new_states.isdisjoint(TERMINAL_STATES)
