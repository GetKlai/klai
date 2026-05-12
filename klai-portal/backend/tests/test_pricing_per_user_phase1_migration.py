"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 1 — migration + post-deploy shape.

Pure file-content regex checks, no live PostgreSQL. Same pattern as
``test_rls_join_requests.py`` and ``test_rls_hygiene.py``. The point is
to guard against a future refactor accidentally reverting the structural
shape (trigger body, partial-unique, RLS DDL) — drift here lands as a
silent billing audit gap.

Live behaviour is exercised by:
  - The pytest suite that imports the migration's trigger SQL string and
    checks structural branches (this file, ``test_trigger_function_body``).
  - The staging smoke test (``scripts/rls-smoke-test.sh`` if extended) that
    verifies the policy resolves against ``pg_policies`` after deploy.
  - A future integration test against a real Postgres if/when the suite
    gains that fixture (Phase 5 will need one for the prorate query).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "f66c546c12eb_pricing_per_user_phase1.py"
)
POST_DEPLOY_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_f66c546c12eb.sql"
RLS_GUARD_PATH = Path(__file__).resolve().parents[1] / "app" / "core" / "rls_guard.py"


@pytest.fixture(scope="module")
def migration_src() -> str:
    assert MIGRATION_PATH.exists(), f"missing migration: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def post_deploy_src() -> str:
    assert POST_DEPLOY_PATH.exists(), f"missing post-deploy SQL: {POST_DEPLOY_PATH}"
    return POST_DEPLOY_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Migration chains correctly
# ---------------------------------------------------------------------------


class TestMigrationChain:
    def test_revision_id_matches_filename(self, migration_src: str) -> None:
        assert re.search(r'^revision\s*=\s*"f66c546c12eb"\s*$', migration_src, re.M)

    def test_chains_off_tenant_lifecycle_platform_features(self, migration_src: str) -> None:
        # Originally chained off e0ad7c2b1e80 (extensions-unify, the prod
        # head when this PR opened). Rebased to c0d5e2a7b9f3
        # (tenant-lifecycle platform-features fix) after that landed on
        # main mid-review and created an alembic head-split — see
        # alembic-multi-pr-head-split pitfall. Drift in either direction
        # means a new split.
        assert re.search(r'down_revision\s*=\s*"c0d5e2a7b9f3"', migration_src)


# ---------------------------------------------------------------------------
# Schema additions on portal_users
# ---------------------------------------------------------------------------


class TestPortalUsersSeatTypeAdd:
    def test_add_column_seat_type_string16(self, migration_src: str) -> None:
        assert re.search(
            r'op\.add_column\(\s*"portal_users"\s*,\s*sa\.Column\(\s*"seat_type"\s*,\s*sa\.String\(length=16\)',
            migration_src,
        )

    def test_backfill_case_handles_all_five_roles(self, migration_src: str) -> None:
        # The CASE must explicitly enumerate every member of the
        # 5-rung profile ladder — a future ladder change without a
        # matching migration tweak would silently fall through to ELSE
        # and over-bill or under-bill.
        backfill_block = re.search(
            r"UPDATE portal_users\s+SET seat_type = CASE(.+?)END",
            migration_src,
            re.DOTALL,
        )
        assert backfill_block is not None, "missing seat_type backfill CASE"
        body = backfill_block.group(1)
        for role in ("kb_manager", "group_manager", "admin"):
            assert role in body, f"backfill CASE missing role {role!r}"
        for role in ("personal", "company"):
            assert role in body, f"backfill CASE missing role {role!r}"
        # Default branch goes to 'chat' (cheapest non-zero). Viewer must
        # never be a backfill outcome — that's a free billing tier for
        # nobody.
        assert "ELSE 'chat'" in body or "ELSE 'chat'\n" in body

    def test_alter_seat_type_to_not_null(self, migration_src: str) -> None:
        assert re.search(
            r'op\.alter_column\(\s*"portal_users"\s*,\s*"seat_type"\s*,'
            r".*nullable=False",
            migration_src,
            re.DOTALL,
        )

    def test_check_constraint_locks_three_values(self, migration_src: str) -> None:
        assert re.search(
            r'create_check_constraint\(\s*"ck_portal_users_seat_type"\s*,\s*"portal_users"\s*,\s*"seat_type IN \(\'viewer\', \'chat\', \'knowledge\'\)"',
            migration_src,
        )


# ---------------------------------------------------------------------------
# portal_user_seat_history table
# ---------------------------------------------------------------------------


class TestSeatHistoryTable:
    def test_table_has_all_eleven_columns(self, migration_src: str) -> None:
        # Match the create_table block and confirm every column appears.
        # Order doesn't matter; presence does.
        create_block = re.search(
            r'op\.create_table\(\s*"portal_user_seat_history"(.+?)\)\s*\n\s*op\.create_index',
            migration_src,
            re.DOTALL,
        )
        assert create_block is not None, "create_table for portal_user_seat_history not found"
        body = create_block.group(1)
        for col in (
            "id",
            "user_id",
            "org_id",
            "seat_type",
            "role",
            "status",
            "valid_from",
            "valid_to",
            "changed_by",
            "change_reason",
            "created_at",
        ):
            assert f'"{col}"' in body, f"column {col!r} missing from create_table"

    def test_user_id_cascades_on_delete(self, migration_src: str) -> None:
        # Removing a portal_users row must cascade to history (audit-only
        # data, no FK we want to dangle).
        assert re.search(
            r'ForeignKey\(\s*"portal_users\.id"\s*,\s*ondelete="CASCADE"',
            migration_src,
        )

    def test_status_column_is_required(self, migration_src: str) -> None:
        # v0.4.0: status is the snapshot column Phase 5 prorate scopes on.
        # Must be NOT NULL — a backfill / trigger row without status
        # breaks the billable-period query.
        status_decl = re.search(r'sa\.Column\(\s*"status"\s*,\s*sa\.Text\(\)\s*,\s*nullable=False', migration_src)
        assert status_decl is not None, "status column must be NOT NULL"

    def test_seat_type_check_constraint_in_table(self, migration_src: str) -> None:
        assert re.search(
            r'sa\.CheckConstraint\(\s*"seat_type IN \(\'viewer\', \'chat\', \'knowledge\'\)"\s*,\s*name="ck_pu_seat_hist_seat_type"',
            migration_src,
        )

    def test_three_indexes_created(self, migration_src: str) -> None:
        for name in (
            "idx_pu_seat_hist_user_validto",
            "idx_pu_seat_hist_org_validfrom",
            "idx_pu_seat_hist_one_open_per_user",
        ):
            assert re.search(rf'create_index\(\s*"{name}"', migration_src), f"missing index {name}"

    def test_partial_unique_uses_valid_to_is_null_predicate(self, migration_src: str) -> None:
        # The structural guarantee — at most one OPEN history row per user.
        # Without this, the trigger races into overlapping rows.
        assert re.search(
            r'create_index\(\s*"idx_pu_seat_hist_one_open_per_user"\s*,\s*"portal_user_seat_history"\s*,\s*\["user_id"\]\s*,\s*unique=True\s*,\s*postgresql_where=sa\.text\(\s*"valid_to IS NULL"',
            migration_src,
        )

    def test_history_backfill_uses_created_at(self, migration_src: str) -> None:
        # Every existing user gets ONE history row with valid_from=created_at,
        # valid_to=NULL, change_reason='backfill'. The change_reason value
        # is read by Phase 5 prorate to know "this user has been on this
        # seat since the dawn of time, not a real seat change".
        assert re.search(
            r"INSERT INTO portal_user_seat_history\s+\(user_id, org_id, seat_type, role, status, valid_from, change_reason\)\s+SELECT id, org_id, seat_type, role::text, status::text, created_at, 'backfill'\s+FROM portal_users",
            migration_src,
        )


# ---------------------------------------------------------------------------
# Trigger function — the structural guard against ORM-bypass and races
# ---------------------------------------------------------------------------


class TestTriggerFunctionBody:
    def test_function_is_created_in_migration(self, migration_src: str) -> None:
        assert "CREATE OR REPLACE FUNCTION portal_users_seat_history_trg()" in migration_src

    def test_trigger_fires_after_insert_or_update(self, migration_src: str) -> None:
        # AFTER (not BEFORE) so NEW.id is populated for INSERT inserts.
        assert re.search(
            r"CREATE TRIGGER portal_users_seat_history\s+AFTER INSERT OR UPDATE ON portal_users",
            migration_src,
        )

    def test_insert_branch_writes_invite_reason(self, migration_src: str) -> None:
        # New users land via INSERT — first history row is tagged 'invite'.
        assert re.search(
            r"IF TG_OP = 'INSERT' THEN\s+INSERT INTO portal_user_seat_history.+?'invite'\s*\)\s*;",
            migration_src,
            re.DOTALL,
        )

    def test_update_branch_uses_is_distinct_from(self, migration_src: str) -> None:
        # IS DISTINCT FROM is the NULL-safe comparison Postgres requires
        # — `<>` would let a NULL -> non-NULL transition slip through.
        for col in ("seat_type", "role", "status"):
            assert re.search(rf"NEW\.{col}\s+IS DISTINCT FROM\s+OLD\.{col}", migration_src), (
                f"trigger missing IS DISTINCT FROM check for {col}"
            )

    def test_update_branch_closes_previous_open_row(self, migration_src: str) -> None:
        # The trigger MUST close the previous open row before inserting
        # the new one. The partial-unique index would otherwise reject
        # the INSERT.
        assert re.search(
            r"UPDATE portal_user_seat_history\s+SET valid_to = NOW\(\)\s+WHERE user_id = NEW\.id\s+AND valid_to IS NULL",
            migration_src,
        )

    def test_change_reason_precedence_seat_role_status(self, migration_src: str) -> None:
        # CASE order matters: a combined PATCH that bumps seat AND status
        # tags as 'seat_change' (the more interesting / billable change).
        # The pattern is permissive — match the WHEN clauses in order.
        case_block = re.search(
            r"CASE\s+WHEN NEW\.seat_type IS DISTINCT FROM OLD\.seat_type\s+THEN 'seat_change'\s+WHEN NEW\.role\s+IS DISTINCT FROM OLD\.role\s+THEN 'role_change'\s+ELSE 'status_change'\s+END",
            migration_src,
        )
        assert case_block is not None, "change_reason CASE precedence missing"


# ---------------------------------------------------------------------------
# Downgrade reverses everything (rollback safety)
# ---------------------------------------------------------------------------


class TestDowngradeReversesUpgrade:
    def test_drops_trigger_and_function(self, migration_src: str) -> None:
        assert "DROP TRIGGER IF EXISTS portal_users_seat_history" in migration_src
        assert "DROP FUNCTION IF EXISTS portal_users_seat_history_trg()" in migration_src

    def test_drops_three_indexes(self, migration_src: str) -> None:
        for name in (
            "idx_pu_seat_hist_one_open_per_user",
            "idx_pu_seat_hist_org_validfrom",
            "idx_pu_seat_hist_user_validto",
        ):
            assert f'drop_index(\n        "{name}"' in migration_src or re.search(
                rf'drop_index\(\s*"{name}"', migration_src
            ), f"downgrade missing drop_index({name!r})"

    def test_drops_table_and_constraint(self, migration_src: str) -> None:
        assert 'op.drop_table("portal_user_seat_history")' in migration_src
        assert 'op.drop_constraint("ck_portal_users_seat_type", "portal_users", type_="check")' in migration_src

    def test_drops_seat_type_column(self, migration_src: str) -> None:
        assert 'op.drop_column("portal_users", "seat_type")' in migration_src


# ---------------------------------------------------------------------------
# Post-deploy SQL — RLS Cat-D shape
# ---------------------------------------------------------------------------


class TestPostDeployRls:
    def test_creates_billing_schema(self, post_deploy_src: str) -> None:
        assert "CREATE SCHEMA IF NOT EXISTS billing" in post_deploy_src

    def test_helper_function_schema_qualified(self, post_deploy_src: str) -> None:
        # Schema-qualified to avoid the postgres-no-return-type-overload
        # collision with portal-api's public._rls_current_org_id().
        assert re.search(
            r"CREATE OR REPLACE FUNCTION billing\._rls_current_org_id\(\)\s+RETURNS integer",
            post_deploy_src,
        )

    def test_helper_uses_nullif_pattern(self, post_deploy_src: str) -> None:
        # NULLIF + ::integer coerce matches the Cat-A/Cat-D shape used
        # elsewhere in the portal. Direct subscript would raise on
        # missing-context — fine for Cat-D, but the NULLIF form
        # short-circuits cleanly to NULL for the RLS USING clause.
        assert re.search(
            r"NULLIF\(\s*current_setting\(\s*'app\.current_org_id'\s*,\s*true\s*\)\s*,\s*''\s*\)\s*::integer",
            post_deploy_src,
        )

    def test_enables_and_forces_rls(self, post_deploy_src: str) -> None:
        # Both required: ENABLE turns it on, FORCE applies it to the table
        # owner too (portal_api). Without FORCE, portal_api reads bypass
        # the policy — defeating the cross-tenant guarantee.
        assert "ALTER TABLE portal_user_seat_history ENABLE ROW LEVEL SECURITY" in post_deploy_src
        assert (
            "ALTER TABLE portal_user_seat_history FORCE  ROW LEVEL SECURITY" in post_deploy_src
            or "ALTER TABLE portal_user_seat_history FORCE ROW LEVEL SECURITY" in post_deploy_src
        )

    def test_drop_policy_before_create(self, post_deploy_src: str) -> None:
        # CREATE POLICY doesn't support IF NOT EXISTS — re-running the
        # post-deploy SQL on a partially-applied DB must not error.
        assert "DROP POLICY IF EXISTS tenant_isolation ON portal_user_seat_history" in post_deploy_src

    def test_policy_uses_helper_in_using_and_with_check(self, post_deploy_src: str) -> None:
        assert re.search(
            r"CREATE POLICY tenant_isolation ON portal_user_seat_history\s+USING\s+\(\s*org_id = billing\._rls_current_org_id\(\)\s*\)\s+WITH CHECK \(\s*org_id = billing\._rls_current_org_id\(\)\s*\)",
            post_deploy_src,
        )

    def test_runs_in_a_transaction(self, post_deploy_src: str) -> None:
        # BEGIN/COMMIT wrap so partial failures roll back cleanly.
        assert "BEGIN;" in post_deploy_src
        assert "COMMIT;" in post_deploy_src

    def test_portal_api_receives_explicit_schema_and_function_grants(self, post_deploy_src: str) -> None:
        """portal_api must be able to USAGE the ``billing`` schema AND
        EXECUTE the helper. Postgres' default ACL grants both to PUBLIC,
        but a future cluster-wide hardening pass that does
        ``REVOKE EXECUTE ON ALL FUNCTIONS ... FROM PUBLIC`` would silently
        break the RLS USING clause (returns NULL -> zero rows visible).
        Explicit grants make portal_api's read path immune to that drift.
        """
        assert "GRANT USAGE ON SCHEMA billing TO portal_api" in post_deploy_src, (
            "missing GRANT USAGE for portal_api on billing schema"
        )
        assert "GRANT EXECUTE ON FUNCTION billing._rls_current_org_id() TO portal_api" in post_deploy_src, (
            "missing GRANT EXECUTE for portal_api on billing._rls_current_org_id()"
        )


# ---------------------------------------------------------------------------
# RLS guard allow-list updated
# ---------------------------------------------------------------------------


class TestRlsGuardAllowlist:
    def test_history_table_listed_in_rls_dml_tables(self) -> None:
        from app.core.rls_guard import RLS_DML_TABLES

        assert "portal_user_seat_history" in RLS_DML_TABLES
