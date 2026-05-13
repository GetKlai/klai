"""SPEC-PORTAL-PRICING-PER-USER-001 Phase 1 — migration + post-deploy shape.

Pure file-content regex checks, no live PostgreSQL. Same pattern as
``test_rls_join_requests.py`` and ``test_rls_hygiene.py``. The point is
to guard against a future refactor accidentally reverting the structural
shape — drift here lands as a silent billing audit gap.

Hotfix (2026-05-12): the initial Phase 1 migration shape had the
backfill UPDATE on portal_users inside ``alembic upgrade()``. portal_users
has FORCE RLS with a Cat-A inline-NULLIF policy whose WITH CHECK clause
requires ``app.current_org_id`` per row. The migration runs without a
tenant context — every WITH CHECK predicate evaluated to NULL — every
row rejected — portal-api crashlooped on prod. See the
``rls-with-check-blocks-migration-update`` pitfall added in this hotfix
PR. Tests below pin the new split: portal_api-safe DDL stays in alembic,
all UPDATE/INSERT/trigger/RLS work moves to post-deploy SQL (klai
superuser path).

Live behaviour is exercised by:
  - The staging smoke test (``scripts/rls-smoke-test.sh`` if extended)
    that verifies the policy resolves against ``pg_policies`` after
    deploy.
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
        # Originally chained off e0ad7c2b1e80 (extensions-unify). Rebased
        # to c0d5e2a7b9f3 after that landed on main mid-review.
        assert re.search(r'down_revision\s*=\s*"c0d5e2a7b9f3"', migration_src)


# ---------------------------------------------------------------------------
# Hotfix invariant: alembic upgrade() does NO writes to portal_users.
# ---------------------------------------------------------------------------


class TestUpgradeDoesNoPortalUsersWrites:
    """RLS guard: alembic runs as portal_api with no tenant context. Any
    UPDATE / INSERT on portal_users (or any other FORCE-RLS table with a
    strict WITH CHECK) fails with ``new row violates row-level security
    policy`` and crashlooops the container. Mechanical regression check
    that the offending shapes never reappear in this migration.
    """

    def test_no_update_on_portal_users(self, migration_src: str) -> None:
        # The whole class of failure is: any "UPDATE portal_users" inside
        # op.execute(...). The string match is intentionally broad — we
        # never want this migration to write tenant-scoped rows, period.
        assert not re.search(r"UPDATE\s+portal_users", migration_src, re.IGNORECASE), (
            "Phase 1 migration must NOT contain `UPDATE portal_users` — "
            "FORCE RLS WITH CHECK blocks it. Move the backfill to "
            "post_deploy_f66c546c12eb.sql (klai-superuser path)."
        )

    def test_no_insert_into_portal_users(self, migration_src: str) -> None:
        # Same class. INSERT also fires WITH CHECK.
        assert not re.search(r"INSERT\s+INTO\s+portal_users\b", migration_src, re.IGNORECASE), (
            "Phase 1 migration must NOT contain `INSERT INTO portal_users` — FORCE RLS WITH CHECK blocks it."
        )


# ---------------------------------------------------------------------------
# Schema additions on portal_users (alembic-side, DDL only)
# ---------------------------------------------------------------------------


class TestPortalUsersSeatTypeAdd:
    def test_add_column_seat_type_is_not_null_default_chat(self, migration_src: str) -> None:
        """ADD COLUMN with NOT NULL + DEFAULT 'chat' is a metadata-only
        op in PG 11+ — no row-write happens, so the FORCE RLS WITH CHECK
        clause does NOT fire. This is the single safe shape for adding a
        backfilled column to a strict-RLS table."""
        # Permissive about ruff's trailing-comma + multi-line formatting.
        # Required pieces: ADD COLUMN on portal_users naming seat_type as
        # String(16), nullable=False, server_default="chat".
        decl = re.search(
            r'op\.add_column\(\s*"portal_users"\s*,\s*sa\.Column\(\s*"seat_type"\s*,\s*sa\.String\(length=16\)\s*,\s*nullable=False\s*,\s*server_default="chat"\s*,?\s*\)\s*,?\s*\)',
            migration_src,
        )
        assert decl is not None, "expected ADD COLUMN seat_type String(16) NOT NULL DEFAULT 'chat'"

    def test_check_constraint_locks_three_values(self, migration_src: str) -> None:
        assert re.search(
            r'create_check_constraint\(\s*"ck_portal_users_seat_type"\s*,\s*"portal_users"\s*,\s*"seat_type IN \(\'viewer\', \'chat\', \'knowledge\'\)"',
            migration_src,
        )


# ---------------------------------------------------------------------------
# portal_user_seat_history table (alembic-side: empty table + indexes only)
# ---------------------------------------------------------------------------


class TestSeatHistoryTable:
    def test_table_has_all_eleven_columns(self, migration_src: str) -> None:
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
        assert re.search(
            r'ForeignKey\(\s*"portal_users\.id"\s*,\s*ondelete="CASCADE"',
            migration_src,
        )

    def test_status_column_is_required(self, migration_src: str) -> None:
        status_decl = re.search(
            r'sa\.Column\(\s*"status"\s*,\s*sa\.Text\(\)\s*,\s*nullable=False',
            migration_src,
        )
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
        assert re.search(
            r'create_index\(\s*"idx_pu_seat_hist_one_open_per_user"\s*,\s*"portal_user_seat_history"\s*,\s*\["user_id"\]\s*,\s*unique=True\s*,\s*postgresql_where=sa\.text\(\s*"valid_to IS NULL"',
            migration_src,
        )


# ---------------------------------------------------------------------------
# Downgrade reverses everything (rollback safety)
# ---------------------------------------------------------------------------


class TestDowngradeReversesUpgrade:
    def test_drops_three_indexes(self, migration_src: str) -> None:
        for name in (
            "idx_pu_seat_hist_one_open_per_user",
            "idx_pu_seat_hist_org_validfrom",
            "idx_pu_seat_hist_user_validto",
        ):
            assert re.search(rf'drop_index\(\s*"{name}"', migration_src), f"downgrade missing drop_index({name!r})"

    def test_drops_table_and_constraint(self, migration_src: str) -> None:
        assert 'op.drop_table("portal_user_seat_history")' in migration_src
        assert 'op.drop_constraint("ck_portal_users_seat_type", "portal_users", type_="check")' in migration_src

    def test_drops_seat_type_column(self, migration_src: str) -> None:
        assert 'op.drop_column("portal_users", "seat_type")' in migration_src


# ---------------------------------------------------------------------------
# Post-deploy SQL — the klai-path that does the actual data + trigger work
# ---------------------------------------------------------------------------


class TestPostDeployBackfillAndTrigger:
    """The data-side of Phase 1 lives entirely in the post-deploy SQL
    (klai-superuser path). Everything that can't run as portal_api goes
    here: the role-driven UPDATE backfill, the history-table INSERT,
    the trigger function + trigger, RLS DDL.
    """

    def test_role_based_update_promotes_kms_to_knowledge(self, post_deploy_src: str) -> None:
        # KMs / group-managers / admins get bumped from the column-DEFAULT
        # 'chat' to 'knowledge'. Idempotent via WHERE seat_type='chat'.
        assert re.search(
            r"UPDATE portal_users\s+SET seat_type = 'knowledge'\s+WHERE role IN \('kb_manager', 'group_manager', 'admin'\)\s+AND seat_type = 'chat'",
            post_deploy_src,
        ), "missing role->knowledge UPDATE backfill"

    def test_history_backfill_uses_one_row_per_user_idempotent(self, post_deploy_src: str) -> None:
        # Every existing user gets ONE history row. NOT EXISTS guards
        # idempotent re-application.
        assert re.search(
            r"INSERT INTO portal_user_seat_history\s+\(user_id, org_id, seat_type, role, status, valid_from, change_reason\)\s+SELECT u\.id, u\.org_id, u\.seat_type, u\.role::text, u\.status::text, u\.created_at, 'backfill'\s+FROM portal_users u\s+WHERE NOT EXISTS",
            post_deploy_src,
        ), "missing one-row-per-user history backfill INSERT with NOT EXISTS guard"

    def test_trigger_function_defined(self, post_deploy_src: str) -> None:
        assert "CREATE OR REPLACE FUNCTION portal_users_seat_history_trg()" in post_deploy_src

    def test_trigger_function_owner_handed_to_portal_api(self, post_deploy_src: str) -> None:
        # Future alembic-managed CREATE OR REPLACE FUNCTION needs to run
        # as portal_api (e.g. Phase 2's changed_by propagation). Owner
        # transfer here keeps the function in portal_api's hands so the
        # operator doesn't need klai-superuser again.
        assert "ALTER FUNCTION portal_users_seat_history_trg() OWNER TO portal_api" in post_deploy_src

    def test_trigger_fires_after_insert_or_update(self, post_deploy_src: str) -> None:
        assert re.search(
            r"CREATE TRIGGER portal_users_seat_history\s+AFTER INSERT OR UPDATE ON portal_users",
            post_deploy_src,
        )

    def test_trigger_uses_is_distinct_from_for_all_audited_cols(self, post_deploy_src: str) -> None:
        for col in ("seat_type", "role", "status"):
            assert re.search(rf"NEW\.{col}\s+IS DISTINCT FROM\s+OLD\.{col}", post_deploy_src), (
                f"trigger missing IS DISTINCT FROM check for {col}"
            )

    def test_trigger_closes_previous_open_row(self, post_deploy_src: str) -> None:
        assert re.search(
            r"UPDATE portal_user_seat_history\s+SET valid_to = NOW\(\)\s+WHERE user_id = NEW\.id\s+AND valid_to IS NULL",
            post_deploy_src,
        )

    def test_change_reason_case_precedence(self, post_deploy_src: str) -> None:
        assert re.search(
            r"CASE\s+WHEN NEW\.seat_type IS DISTINCT FROM OLD\.seat_type\s+THEN 'seat_change'\s+WHEN NEW\.role\s+IS DISTINCT FROM OLD\.role\s+THEN 'role_change'\s+ELSE 'status_change'\s+END",
            post_deploy_src,
        )


class TestPostDeployRls:
    def test_creates_billing_schema(self, post_deploy_src: str) -> None:
        assert "CREATE SCHEMA IF NOT EXISTS billing" in post_deploy_src

    def test_helper_function_schema_qualified(self, post_deploy_src: str) -> None:
        assert re.search(
            r"CREATE OR REPLACE FUNCTION billing\._rls_current_org_id\(\)\s+RETURNS integer",
            post_deploy_src,
        )

    def test_helper_uses_nullif_pattern(self, post_deploy_src: str) -> None:
        assert re.search(
            r"NULLIF\(\s*current_setting\(\s*'app\.current_org_id'\s*,\s*true\s*\)\s*,\s*''\s*\)\s*::integer",
            post_deploy_src,
        )

    def test_enables_and_forces_rls(self, post_deploy_src: str) -> None:
        assert "ALTER TABLE portal_user_seat_history ENABLE ROW LEVEL SECURITY" in post_deploy_src
        assert (
            "ALTER TABLE portal_user_seat_history FORCE  ROW LEVEL SECURITY" in post_deploy_src
            or "ALTER TABLE portal_user_seat_history FORCE ROW LEVEL SECURITY" in post_deploy_src
        )

    def test_drop_policy_before_create(self, post_deploy_src: str) -> None:
        assert "DROP POLICY IF EXISTS tenant_isolation ON portal_user_seat_history" in post_deploy_src

    def test_policy_uses_helper_in_using_and_with_check(self, post_deploy_src: str) -> None:
        assert re.search(
            r"CREATE POLICY tenant_isolation ON portal_user_seat_history\s+USING\s+\(\s*org_id = billing\._rls_current_org_id\(\)\s*\)\s+WITH CHECK \(\s*org_id = billing\._rls_current_org_id\(\)\s*\)",
            post_deploy_src,
        )

    def test_runs_in_a_transaction(self, post_deploy_src: str) -> None:
        assert "BEGIN;" in post_deploy_src
        assert "COMMIT;" in post_deploy_src

    def test_portal_api_receives_explicit_schema_and_function_grants(self, post_deploy_src: str) -> None:
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
