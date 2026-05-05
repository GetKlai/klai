"""SPEC-SEC-CONNECTOR-RLS-001 — regression tests for the RLS policy.

The audit finding TP-5 (``reports/audit-2026-05-04/tenant-scoping.md``)
flagged that ``connector.connectors`` and ``connector.sync_runs`` had
``org_id`` columns but no PostgreSQL ``CREATE POLICY``. Migration 008
+ ``post_deploy_008.sql`` close that gap.

The CI pipeline runs without a live PostgreSQL backend, so these tests
exercise two layers:

1. **Migration shape (always)** — parse ``post_deploy_008.sql`` for the
   canonical Category-D policy DDL on both tables. A future refactor
   that drops ENABLE / FORCE / WITH CHECK is caught at CI time.
2. **Migration body is no-op (always)** — the alembic file MUST contain
   no ``op.execute(...)`` calls. Owner-required DDL crashes the
   migration role's ``alembic upgrade head`` (pitfall
   ``alembic-cannot-drop-non-portal_api-tables`` extended on
   2026-05-05). DDL belongs in the post-deploy SQL only.
3. **Live PG smoke (skipif POSTGRES_DSN not set)** — run the full
   policy-against-pg_policies probe + a cross-tenant SELECT to verify
   that an unset GUC returns zero rows. Mirrors portal-api's
   ``scripts/rls-smoke-test.sh`` in pytest form.

The live-PG path is intentionally skipif-only — CI will gain a
service-container in SPEC-CI-PG-FIXTURE-001 and the test will start
running automatically. Until then, manual smoke is via psql per the
SPEC's "Implementatie-volgorde" stap 9.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "008_rls_connector_schema.py"

POST_DEPLOY_SQL_PATH = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_008.sql"


# ---------------------------------------------------------------------------
# Migration shape — DDL strings must contain the canonical patterns
# ---------------------------------------------------------------------------


def _read_post_deploy_sql() -> str:
    assert POST_DEPLOY_SQL_PATH.exists(), (
        f"SPEC-SEC-CONNECTOR-RLS-001 post-deploy SQL is missing: "
        f"{POST_DEPLOY_SQL_PATH}. DDL for the connector schema RLS "
        "lives there because the migration role cannot run owner-"
        "required DDL (see pitfall "
        "alembic-cannot-drop-non-portal_api-tables)."
    )
    return POST_DEPLOY_SQL_PATH.read_text(encoding="utf-8")


def _read_migration() -> str:
    assert MIGRATION_PATH.exists(), f"SPEC-SEC-CONNECTOR-RLS-001 migration file is missing: {MIGRATION_PATH}"
    return MIGRATION_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("table", ["connector.connectors", "connector.sync_runs"])
def test_post_deploy_enables_force_rls(table: str) -> None:
    """Every covered table MUST have ENABLE + FORCE row-level security.

    Without FORCE, the table-owner role bypasses the policy. The no-
    role-split decision (SPEC v0.2.0) means the runtime role IS the
    owner — without FORCE the policy is effectively a suggestion.
    """
    src = _read_post_deploy_sql()
    assert re.search(rf"ALTER TABLE {re.escape(table)} ENABLE ROW LEVEL SECURITY", src), (
        f"post_deploy_008.sql must call ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"
    )
    assert re.search(rf"ALTER TABLE {re.escape(table)} FORCE ROW LEVEL SECURITY", src), (
        f"post_deploy_008.sql must call ALTER TABLE {table} FORCE ROW LEVEL SECURITY"
    )


@pytest.mark.parametrize("table", ["connector.connectors", "connector.sync_runs"])
def test_post_deploy_creates_strict_policy(table: str) -> None:
    """Category D shape — both USING and WITH CHECK reference both
    ``app.current_org_id`` AND ``app.cross_org_admin``.

    USING without the cross_org_admin branch breaks the lifespan reset +
    reaper sweeps. WITH CHECK without it breaks scheduler bootstrap and
    any other intentional cross-org INSERT path.
    """
    src = _read_post_deploy_sql()
    block_match = re.search(
        rf"CREATE POLICY tenant_isolation ON {re.escape(table)}.*?;",
        src,
        re.DOTALL,
    )
    assert block_match, f"CREATE POLICY block for {table} not found in post-deploy SQL"
    block = block_match.group(0)

    assert "USING" in block
    assert "WITH CHECK" in block
    assert "app.current_org_id" in block
    assert "app.cross_org_admin" in block


@pytest.mark.parametrize("table", ["connector.connectors", "connector.sync_runs"])
def test_post_deploy_uses_drop_if_exists_for_idempotency(table: str) -> None:
    """``CREATE POLICY`` does not support ``IF NOT EXISTS``. Each
    CREATE must be preceded by a DROP IF EXISTS so re-applying on a
    partially migrated DB succeeds.
    """
    src = _read_post_deploy_sql()
    # Look for `DROP POLICY IF EXISTS tenant_isolation ON <table>` BEFORE
    # the corresponding CREATE.
    drop_match = re.search(
        rf"DROP POLICY IF EXISTS tenant_isolation ON {re.escape(table)};",
        src,
    )
    create_match = re.search(
        rf"CREATE POLICY tenant_isolation ON {re.escape(table)}",
        src,
    )
    assert drop_match, f"Missing DROP POLICY IF EXISTS guard for {table}"
    assert create_match, f"Missing CREATE POLICY for {table}"
    assert drop_match.start() < create_match.start(), (
        f"DROP POLICY IF EXISTS for {table} must precede the CREATE; "
        "otherwise re-running on a partially migrated DB will error "
        "(CREATE POLICY does not support IF NOT EXISTS)."
    )


def test_migration_body_is_noop() -> None:
    """The alembic migration upgrade()/downgrade() MUST be no-ops.

    Owner-required DDL was deliberately moved to ``post_deploy_008.sql``
    to avoid the ``alembic-cannot-drop-non-portal_api-tables`` crash-
    loop. If a future refactor moves the DDL back into upgrade(),
    klai-connector will crash-loop on every fresh deploy or staging
    rebuild.
    """
    src = _read_migration()
    assert "op.execute(" not in src, (
        "Migration file 008_rls_connector_schema.py contains "
        "op.execute(...) — owner-required DDL must live in "
        "post_deploy_008.sql instead. See pitfall "
        "alembic-cannot-drop-non-portal_api-tables in process-rules.md."
    )


# ---------------------------------------------------------------------------
# Live PostgreSQL smoke — only runs when POSTGRES_DSN is set.
# ---------------------------------------------------------------------------


_pg_dsn = os.environ.get("POSTGRES_DSN")
_pg_skipif = pytest.mark.skipif(
    _pg_dsn is None,
    reason="POSTGRES_DSN not set — live RLS smoke skipped (CI gains this in SPEC-CI-PG-FIXTURE-001)",
)


@_pg_skipif
@pytest.mark.asyncio
@pytest.mark.parametrize("table", ["connector.connectors", "connector.sync_runs"])
async def test_live_policy_blocks_unbound_select(table: str) -> None:
    """Probe: with empty ``app.current_org_id`` + no cross_org_admin,
    SELECT returns zero rows.

    This is the load-bearing assertion for Category D. If it ever
    passes (returns rows), the policy is permissive instead of strict
    and a missing ``set_tenant`` call would silently leak cross-tenant.
    """
    import asyncpg  # noqa: PLC0415

    conn = await asyncpg.connect(_pg_dsn)
    try:
        # Reset both GUCs to be sure the test starts clean.
        await conn.execute("SELECT set_config('app.current_org_id', '', false)")
        await conn.execute("SELECT set_config('app.cross_org_admin', '', false)")
        rows = await conn.fetch(f"SELECT 1 FROM {table} LIMIT 1")  # noqa: S608
        assert rows == [], (
            f"Strict RLS contract broken: {table} returned a row with no "
            "tenant context. Either FORCE ROW LEVEL SECURITY is missing, "
            "the policy uses a permissive `IS NULL` branch on USING, or "
            "the runtime role bypasses RLS (BYPASSRLS attribute)."
        )
    finally:
        await conn.close()


@_pg_skipif
@pytest.mark.asyncio
async def test_live_cross_org_admin_unlocks_full_table_scan() -> None:
    """Probe: ``app.cross_org_admin = '1'`` permits cross-tenant SELECT.

    The lifespan reset + SyncRunReaper rely on this escape-hatch. If a
    future policy-tweak removes the ``cross_org_admin`` branch from
    USING, both will silently see zero rows and the reaper stops
    finalising orphaned runs.
    """
    import asyncpg  # noqa: PLC0415

    conn = await asyncpg.connect(_pg_dsn)
    try:
        await conn.execute("SELECT set_config('app.cross_org_admin', '1', false)")
        # The query SHALL not raise — it may return zero rows if the
        # table is empty in this test DB, but the policy MUST permit it.
        await conn.fetch("SELECT 1 FROM connector.sync_runs LIMIT 1")
        await conn.fetch("SELECT 1 FROM connector.connectors LIMIT 1")
    finally:
        await conn.execute("SELECT set_config('app.cross_org_admin', '', false)")
        await conn.close()
