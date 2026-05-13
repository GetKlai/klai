"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 2 — trigger changed_by propagation.

File-content regex checks on the Phase 2 migration that extends
``portal_users_seat_history_trg`` to read
``current_setting('klai.changed_by_user_id', true)`` and store it into
``portal_user_seat_history.changed_by``.

Also pins the matching change in ``permissions.py``: the authenticated
caller dependency MUST issue ``set_config('klai.changed_by_user_id', ...)``
right after ``set_tenant`` so the GUC is bound before any subsequent
UPDATE on portal_users fires the trigger.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "924465b9e0a6_pricing_trigger_changed_by.py"
)
PERMISSIONS_PATH = Path(__file__).resolve().parents[1] / "app" / "core" / "permissions.py"
DATABASE_PATH = Path(__file__).resolve().parents[1] / "app" / "core" / "database.py"


@pytest.fixture(scope="module")
def migration_src() -> str:
    assert MIGRATION_PATH.exists(), f"missing migration: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def permissions_src() -> str:
    assert PERMISSIONS_PATH.exists(), f"missing permissions.py: {PERMISSIONS_PATH}"
    return PERMISSIONS_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def database_src() -> str:
    assert DATABASE_PATH.exists(), f"missing database.py: {DATABASE_PATH}"
    return DATABASE_PATH.read_text(encoding="utf-8")


class TestMigrationChain:
    def test_revision_id(self, migration_src: str) -> None:
        assert re.search(r'^revision\s*=\s*"924465b9e0a6"\s*$', migration_src, re.M)

    def test_chains_off_phase1(self, migration_src: str) -> None:
        assert re.search(r'down_revision\s*=\s*"f66c546c12eb"', migration_src)


class TestTriggerBodyReadsActorGuc:
    """The Phase 2 trigger MUST read ``current_setting('klai.changed_by_user_id',
    true)`` with NULLIF + missing-OK semantics, then write the result into
    the ``changed_by`` column on both INSERT and UPDATE history rows.
    """

    def test_actor_local_variable_uses_nullif_missing_ok(self, migration_src: str) -> None:
        # NULLIF + true-flag (missing-OK) is the same shape as the Cat-A
        # tenant-context lookups elsewhere. Empty / unset GUC -> NULL ->
        # changed_by NULL — meaning "no acting admin" for system writes.
        assert re.search(
            r"actor\s+TEXT\s*:=\s*NULLIF\(\s*current_setting\(\s*'klai\.changed_by_user_id'\s*,\s*true\s*\)\s*,\s*''\s*\)",
            migration_src,
        ), "trigger must declare actor TEXT := NULLIF(current_setting(...), '')"

    def test_insert_branch_writes_actor_to_changed_by(self, migration_src: str) -> None:
        # INSERT path of the trigger MUST include the changed_by column
        # in its column-list AND ``actor`` in its VALUES tuple.
        insert_block = re.search(
            r"IF TG_OP = 'INSERT' THEN(.+?)RETURN NEW;\s+END IF;",
            migration_src,
            re.DOTALL,
        )
        assert insert_block is not None, "INSERT branch not found in trigger"
        body = insert_block.group(1)
        assert "changed_by" in body, "INSERT branch missing changed_by column"
        assert "actor" in body, "INSERT branch missing actor value"

    def test_update_branch_writes_actor_to_changed_by(self, migration_src: str) -> None:
        update_block = re.search(
            r"IF \(NEW\.seat_type IS DISTINCT FROM OLD\.seat_type\)(.+?)END IF;\s+RETURN NEW;\s+END;",
            migration_src,
            re.DOTALL,
        )
        assert update_block is not None, "UPDATE branch not found in trigger"
        body = update_block.group(1)
        assert "changed_by" in body, "UPDATE branch missing changed_by column"
        assert "actor" in body, "UPDATE branch missing actor value"

    def test_downgrade_restores_phase1_body(self, migration_src: str) -> None:
        # The downgrade restores the no-changed_by Phase 1 shape (so a
        # rollback doesn't leave the trigger half-evolved).
        assert "def downgrade() -> None:" in migration_src
        # Phase 1 body has no `actor` and no `klai.changed_by_user_id`.
        # Easiest assertion: TWO trigger-body strings exist in the file
        # (the Phase 1 + Phase 2), and the downgrade execs the Phase 1 one.
        assert migration_src.count("CREATE OR REPLACE FUNCTION portal_users_seat_history_trg()") == 2
        # The Phase 1 body sits below `_TRIGGER_BODY_PHASE1 = `.
        phase1_section = re.search(r"_TRIGGER_BODY_PHASE1\s*=\s*\"\"\"(.+?)\"\"\"", migration_src, re.DOTALL)
        assert phase1_section is not None
        assert "klai.changed_by_user_id" not in phase1_section.group(1), (
            "Phase 1 body must NOT reference klai.changed_by_user_id"
        )


class TestCallerDependencyBindsActorGuc:
    """The matching code side: ``_resolve_caller_with_options`` in
    permissions.py MUST issue ``set_config('klai.changed_by_user_id', ...)``
    right after ``set_tenant`` so the trigger sees the actor on the next
    portal_users UPDATE within the same request.
    """

    def test_permissions_sets_changed_by_user_id_guc(self, permissions_src: str) -> None:
        # The exact statement we expect:
        #   await db.execute(
        #       text("SELECT set_config('klai.changed_by_user_id', :uid, false)"),
        #       {"uid": zitadel_user_id},
        #   )
        assert re.search(
            r"set_config\('klai\.changed_by_user_id',\s*:uid,\s*false\)",
            permissions_src,
        ), (
            "permissions.py must bind klai.changed_by_user_id on the request "
            "connection — without this the trigger writes NULL changed_by "
            "for every admin action."
        )


class TestDatabaseResetsActorGuc:
    """The connection-release path in database.py MUST clear the actor GUC
    so a pooled connection never carries one user's identity into a
    different user's next request.
    """

    def test_reset_clears_changed_by_user_id(self, database_src: str) -> None:
        assert re.search(
            r"set_config\('klai\.changed_by_user_id',\s*''\s*,\s*false\)",
            database_src,
        ), (
            "database.py _reset_tenant_context must clear "
            "klai.changed_by_user_id at connection-release. A leaked value "
            "here would attribute writes to the previous request's user."
        )
