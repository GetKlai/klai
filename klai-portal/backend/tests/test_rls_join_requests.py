"""SPEC-SEC-PORTAL-RLS-001 — regression tests for the new RLS policy.

``portal_join_requests`` was created with an ``org_id`` column (per
SPEC-AUTH-006) but never received a ``CREATE POLICY``, leaving it
outside the DB-layer tenant-isolation regime that protects every other
portal table. Migration ``2f7d1eae1198`` closes that gap.

Note: ``portal_org_allowed_domains`` was originally part of the audit
TP-2 finding, but SPEC-AUTH-009 R2 (migration ``ed5b78b296f5``) replaced
that table with ``portal_orgs.primary_domain`` and DROPS it in
production via ``post_deploy_ed5b78b296f5.sql``. Adding RLS to a table
on the deletion path would pollute the migration history without
functional benefit. ``test_r2_removal.py`` is the regression-guard for
its absence.

Pytest does not have a live PostgreSQL backend in CI (the suite runs
against SQLite for speed; full integration uses staging). These tests
therefore exercise the two layers we CAN exercise without postgres:

1. ``RLS_DML_TABLES`` in ``app/core/rls_guard.py`` includes the new
   table — the silent-filter guard now covers it.
2. The migration file ships the canonical Category-A policy shape
   (regex-checked against the file content) so a future refactor that
   accidentally drops the policy is caught at CI time, before it can
   land on staging.

Live policy-against-pg_policies coverage is delivered by the staging
smoke test ``scripts/rls-smoke-test.sh``; AC-2 of the SPEC.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.rls_guard import RLS_DML_TABLES

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "2f7d1eae1198_add_rls_join_requests_and_allowed_domains.py"
)

# DDL was moved to a post-deploy SQL file (run as klai superuser) because
# `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` and `CREATE POLICY` require
# the table-owner role, not the migration role (`portal_api`). See pitfall
# `alembic-cannot-drop-non-portal_api-tables` in process-rules.md and the
# header of post_deploy_2f7d1eae1198.sql for context.
POST_DEPLOY_SQL_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_2f7d1eae1198.sql"


# ---------------------------------------------------------------------------
# RLS_DML_TABLES regression — silent-filter guard must cover the new table
# ---------------------------------------------------------------------------


def test_rls_dml_tables_includes_portal_join_requests() -> None:
    """``portal_join_requests`` must be in the silent-filter guard set.

    Otherwise an UPDATE/DELETE that hits zero rows because RLS filtered
    them would slip past the rls_guard listener with no error log.
    """
    assert "portal_join_requests" in RLS_DML_TABLES, (
        "portal_join_requests was added to the database with an RLS policy "
        "but is missing from RLS_DML_TABLES in app/core/rls_guard.py. "
        "The silent-filter guard would not warn on rowcount=0 DML "
        "against this table. See SPEC-SEC-PORTAL-RLS-001."
    )


def test_rls_dml_tables_does_not_include_portal_org_allowed_domains() -> None:
    """SPEC-AUTH-009 R2 dropped ``portal_org_allowed_domains``. It must
    NOT be in RLS_DML_TABLES because the silent-filter guard expects every
    listed table to actually exist in the schema. ``test_r2_removal.py``
    is the canonical regression-guard for the absence of the table.
    """
    assert "portal_org_allowed_domains" not in RLS_DML_TABLES, (
        "portal_org_allowed_domains was dropped by SPEC-AUTH-009 R2 "
        "(migration ed5b78b296f5). It must not be re-added to "
        "RLS_DML_TABLES — the silent-filter guard expects every listed "
        "table to exist in the live schema."
    )


# ---------------------------------------------------------------------------
# Migration shape — DDL strings must contain the canonical patterns
# ---------------------------------------------------------------------------


def _read_migration() -> str:
    assert MIGRATION_PATH.exists(), f"SPEC-SEC-PORTAL-RLS-001 migration file is missing: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


def _read_post_deploy_sql() -> str:
    assert POST_DEPLOY_SQL_PATH.exists(), (
        f"SPEC-SEC-PORTAL-RLS-001 post-deploy SQL file is missing: {POST_DEPLOY_SQL_PATH}. "
        "DDL for portal_join_requests RLS lives there because the "
        "migration role (portal_api) cannot run owner-required DDL "
        "(see pitfall alembic-cannot-drop-non-portal_api-tables)."
    )
    return POST_DEPLOY_SQL_PATH.read_text(encoding="utf-8")


def test_post_deploy_enables_force_rls() -> None:
    """Both ENABLE and FORCE row-level security must fire on the table.

    Without FORCE, the table owner role bypasses the policy — fine in
    dev but operationally risky if the migration role and the
    application role ever drift.
    """
    src = _read_post_deploy_sql()
    assert re.search(r"ENABLE ROW LEVEL SECURITY", src), (
        "post_deploy_2f7d1eae1198.sql must call ALTER TABLE ... ENABLE ROW LEVEL SECURITY"
    )
    assert re.search(r"FORCE ROW LEVEL SECURITY", src), (
        "post_deploy_2f7d1eae1198.sql must call ALTER TABLE ... FORCE ROW LEVEL SECURITY"
    )
    assert "portal_join_requests" in src


def test_migration_body_is_noop() -> None:
    """The migration file's upgrade() and downgrade() MUST be no-ops.

    The owner-required DDL was moved out of the migration to
    post_deploy_2f7d1eae1198.sql to avoid the
    `alembic-cannot-drop-non-portal_api-tables` crash-loop. If a future
    refactor accidentally moves the DDL back into upgrade(), portal-api
    will crash-loop on every fresh deploy.
    """
    src = _read_migration()
    # Both upgrade() and downgrade() should be `pass`-only bodies. We
    # assert the absence of `op.execute(`, which is the load-bearing
    # API for raw DDL.
    assert "op.execute(" not in src, (
        "Migration file contains op.execute(...) — owner-required DDL "
        "must live in post_deploy_2f7d1eae1198.sql instead. See pitfall "
        "alembic-cannot-drop-non-portal_api-tables in process-rules.md."
    )


def test_post_deploy_does_not_touch_portal_org_allowed_domains() -> None:
    """SPEC-AUTH-009 R2 dropped the table. Post-deploy SQL must NOT
    re-introduce DDL on it — that would conflict with the drop on
    upgrade() and confuse downgrade()."""
    src = _read_post_deploy_sql()
    # Header docstring may mention the table as historical context.
    # Strip SQL line-comments before searching for the table name in
    # active DDL.
    active_ddl = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("--"))
    assert "portal_org_allowed_domains" not in active_ddl, (
        "post_deploy_2f7d1eae1198.sql must not run DDL against "
        "portal_org_allowed_domains — SPEC-AUTH-009 R2 dropped that "
        "table."
    )


def _extract_policy_block(source: str, table: str) -> str:
    """Return the source-text run that contains the ``CREATE POLICY`` for
    ``table``, up to the closing semicolon.
    """
    match = re.search(
        rf"CREATE POLICY tenant_isolation ON {table}.*?;",
        source,
        re.DOTALL,
    )
    assert match, f"CREATE POLICY block for {table} not found in post-deploy SQL"
    return match.group(0)


def test_post_deploy_creates_permissive_policy_on_join_requests() -> None:
    """Category-A pre-auth pattern: portal_join_requests MUST include
    the ``IS NULL`` permissive branch in its USING clause.

    Without that branch, the admin token-based approve flow in
    ``auth_join.py`` cannot resolve the join request before tenant
    context exists. See migration docstring + SPEC-SEC-PORTAL-RLS-001
    Risks table.
    """
    src = _read_post_deploy_sql()
    block = _extract_policy_block(src, "portal_join_requests")
    assert "IS NULL" in block, (
        "portal_join_requests policy must include the `IS NULL` "
        "permissive branch (Category-A auth-seed pattern). Without it "
        "the admin approve_join flow cannot resolve the row before "
        f"tenant context. Policy block:\n{block}"
    )


def test_post_deploy_with_check_clause_present() -> None:
    """The policy must include WITH CHECK so INSERTs from a wrong tenant
    context fail, not just SELECTs filter."""
    src = _read_post_deploy_sql()
    check_count = len(re.findall(r"WITH CHECK", src))
    assert check_count >= 1, (
        f"Expected at least 1 WITH CHECK clause in post-deploy SQL; "
        f"found {check_count}. Without WITH CHECK an attacker could "
        "INSERT a row with someone else's org_id."
    )


# ---------------------------------------------------------------------------
# Idempotency — re-running on an existing DB must not fail.
# ---------------------------------------------------------------------------


def test_post_deploy_uses_drop_if_exists_for_idempotency() -> None:
    """``CREATE POLICY`` does not support ``IF NOT EXISTS``. The
    post-deploy SQL must wrap each CREATE in a DROP IF EXISTS so
    re-applying on a partially migrated database (rare but possible
    during staging recovery) succeeds.
    """
    src = _read_post_deploy_sql()
    drop_count = len(re.findall(r"DROP POLICY IF EXISTS tenant_isolation", src))
    create_count = len(re.findall(r"CREATE POLICY tenant_isolation", src))
    assert drop_count >= create_count, (
        f"Post-deploy SQL has {create_count} CREATE POLICY statements "
        f"but only {drop_count} DROP POLICY IF EXISTS guards. "
        "Idempotency is broken: re-running on a partially migrated DB "
        "will fail."
    )
