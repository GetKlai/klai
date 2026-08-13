"""SPEC-TI-005 -- portal-api RLS hygiene batch (findings A-1 to A-6).

These tests guard the post-deploy SQL file and the Python code changes
so that a future refactor does not accidentally revert the hygiene fixes.
All tests are pure file-content checks -- no live PostgreSQL required.
"""

from __future__ import annotations

import re
from pathlib import Path

POST_DEPLOY_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "post_deploy_ti005_tenant_isolation_hygiene.sql"
)

DATABASE_PY_PATH = Path(__file__).resolve().parents[1] / "app" / "core" / "database.py"

MAIN_PY_PATH = Path(__file__).resolve().parents[1] / "app" / "main.py"

TENANT_LIFECYCLE_PY_PATH = Path(__file__).resolve().parents[1] / "app" / "services" / "audit" / "tenant_lifecycle.py"


def _read_sql() -> str:
    assert POST_DEPLOY_PATH.exists()
    return POST_DEPLOY_PATH.read_text(encoding="utf-8")


def _read_database_py() -> str:
    assert DATABASE_PY_PATH.exists()
    return DATABASE_PY_PATH.read_text(encoding="utf-8")


def _read_main_py() -> str:
    assert MAIN_PY_PATH.exists()
    return MAIN_PY_PATH.read_text(encoding="utf-8")


def _read_tenant_lifecycle_py() -> str:
    assert TENANT_LIFECYCLE_PY_PATH.exists()
    return TENANT_LIFECYCLE_PY_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# A-1: portal_users + portal_connectors -- explicit WITH CHECK
# ---------------------------------------------------------------------------


def test_a1_portal_users_with_check_excludes_null_branch() -> None:
    """portal_users WITH CHECK must NOT contain the empty-GUC OR branch (A-1)."""
    sql = _read_sql()
    m = re.search(r"CREATE POLICY tenant_isolation ON portal_users.*?;\n", sql, re.DOTALL)
    assert m, "portal_users CREATE POLICY not found in post-deploy SQL"
    block = m.group(0)
    assert "WITH CHECK" in block
    wc = re.search(r"WITH CHECK \((.+?)\);", block, re.DOTALL)
    assert wc
    wc_body = wc.group(1)
    assert not re.search(r"OR\s+current_setting.*?=\s*''", wc_body), (
        "portal_users WITH CHECK must omit empty-GUC OR. Got: " + wc_body
    )


def test_a1_portal_connectors_with_check_excludes_null_branch() -> None:
    """portal_connectors WITH CHECK must NOT contain the IS NULL branch (A-1)."""
    sql = _read_sql()
    m = re.search(r"CREATE POLICY tenant_isolation ON portal_connectors.*?;\n", sql, re.DOTALL)
    assert m, "portal_connectors CREATE POLICY not found"
    block = m.group(0)
    assert "WITH CHECK" in block
    wc = re.search(r"WITH CHECK \((.+?)\);", block, re.DOTALL)
    assert wc
    assert not re.search(r"OR\s+current_setting.*?=\s*''", wc.group(1)), (
        "portal_connectors WITH CHECK must omit empty-GUC OR. Got: " + wc.group(1)
    )


def test_a1_portal_users_using_retains_null_branch() -> None:
    """portal_users USING clause MUST keep an unset-GUC branch (Cat-A).

    portal_users is AUTH-SEED — `/api/me` and `_get_caller_org` look up
    (org, user) BEFORE knowing the tenant. The USING clause MUST evaluate
    permissively when `app.current_org_id` is unset, otherwise every
    authenticated request 500s.

    Two structurally-equivalent shapes both deliver the unset-GUC branch:
      - inline NULLIF pattern:   ``... OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL``
      - alternate inline form:   ``... OR current_setting('app.current_org_id', true) = ''``

    Both contain the literal substring "IS NULL" so the lifespan guard
    `assert_portal_users_rls_ready()` accepts them. See pitfall:
    `rls-policy-shape-must-match-lifespan-assert` (HIGH).
    """
    sql = _read_sql()
    m = re.search(r"CREATE POLICY tenant_isolation ON portal_users.*?;\n", sql, re.DOTALL)
    assert m
    # Robust against nested parens in NULLIF/IS NULL expressions.
    um = re.search(r"USING\s+(.+?)\s+WITH CHECK", m.group(0), re.DOTALL)
    assert um, "Cannot parse USING from portal_users policy"
    body = um.group(1)
    has_unset_branch = bool(re.search(r"current_setting.*?=\s*''", body) or re.search(r"IS NULL", body))
    assert has_unset_branch, (
        "portal_users USING must keep an unset-GUC branch — either "
        "`NULLIF(...) IS NULL` or `current_setting(...) = ''`. "
        "Got: " + body
    )


def test_a1_portal_users_using_does_not_call_raising_helper() -> None:
    """portal_users USING MUST NOT call `_rls_current_org_id()`.

    Cat-A AUTH-SEED tables are queried BEFORE the tenant context is set
    (e.g. `/api/me` resolves the user by `zitadel_user_id` to discover
    the tenant). `_rls_current_org_id()` is the strict Cat-D helper that
    RAISES `42501` on missing GUC, so any USING that calls it makes the
    auth-seed lookup fail with 500.

    Regression for the 2026-05-06 portal_users 500 outage where an
    intermediate hot-fix migrated USING to the helper-function pattern,
    breaking every authenticated request. See incident-report:
    `reports/audit-tenant-isolation-2026-05-05/spec-ti-003-incident/`.
    """
    sql = _read_sql()
    for table in ("portal_users", "portal_connectors"):
        m = re.search(rf"CREATE POLICY tenant_isolation ON {table}.*?;\n", sql, re.DOTALL)
        assert m, f"{table} CREATE POLICY not found"
        um = re.search(r"USING\s+(.+?)\s+WITH CHECK", m.group(0), re.DOTALL)
        assert um, f"Cannot parse USING from {table} policy"
        body = um.group(1)
        assert "_rls_current_org_id" not in body, (
            f"{table} USING must NOT call `_rls_current_org_id()` — that helper "
            "RAISES 42501 on missing GUC and would break Cat-A AUTH-SEED reads. "
            "Use inline `NULLIF(current_setting('app.current_org_id', true), '')::integer` "
            "instead. Got: " + body
        )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# A-2
# ---------------------------------------------------------------------------


def test_a2_group_memberships_has_enable_and_force_rls() -> None:
    """portal_group_memberships must have both ENABLE and FORCE RLS (A-2)."""
    sql = _read_sql()
    assert "ALTER TABLE portal_group_memberships ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE portal_group_memberships FORCE ROW LEVEL SECURITY" in sql


def test_a2_group_memberships_policy_uses_subquery() -> None:
    """portal_group_memberships policy must scope via subquery on portal_groups (A-2)."""
    sql = _read_sql()
    m = re.search(
        r"CREATE POLICY tenant_isolation ON portal_group_memberships.*?;\n",
        sql,
        re.DOTALL,
    )
    assert m, "portal_group_memberships CREATE POLICY not found"
    block = m.group(0)
    assert "SELECT id FROM portal_groups" in block, "policy must subquery portal_groups. Block: " + block


def test_a2_group_memberships_with_check_strict() -> None:
    """portal_group_memberships WITH CHECK must not include IS NULL branch."""
    sql = _read_sql()
    m = re.search(
        r"CREATE POLICY tenant_isolation ON portal_group_memberships.*?;\n",
        sql,
        re.DOTALL,
    )
    assert m
    wc = re.search(r"WITH CHECK \((.+?)\);", m.group(0), re.DOTALL)
    assert wc
    assert "IS NULL" not in wc.group(1), "WITH CHECK must omit IS NULL. Got: " + wc.group(1)


# ---------------------------------------------------------------------------
# A-3: partner_api_keys
# ---------------------------------------------------------------------------


def test_a3_partner_api_keys_enable_and_force_rls_in_sql() -> None:
    """partner_api_keys and partner_api_key_kb_access must have ENABLE+FORCE (A-3)."""
    sql = _read_sql()
    for table in ("partner_api_keys", "partner_api_key_kb_access"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql


def test_a3_startup_assertion_exists_in_database_py() -> None:
    """assert_partner_api_keys_rls_ready() must exist in database.py (A-3)."""
    src = _read_database_py()
    assert "def assert_partner_api_keys_rls_ready" in src


def test_a3_startup_assertion_checks_both_rls_flags() -> None:
    """Startup assertion must check both relrowsecurity AND relforcerowsecurity."""
    src = _read_database_py()
    assert "relrowsecurity" in src
    assert "relforcerowsecurity" in src


def test_a3_startup_assertion_wired_before_portal_users_check() -> None:
    """assert_partner_api_keys_rls_ready() must run BEFORE assert_portal_users_rls_ready()."""
    src = _read_main_py()
    idx_p = src.find("await assert_partner_api_keys_rls_ready()")
    idx_u = src.find("await assert_portal_users_rls_ready()")
    assert idx_p != -1, "partner assertion not called in main.py"
    assert idx_u != -1, "portal_users assertion not called in main.py"
    assert idx_p < idx_u, f"partner ({idx_p}) must precede portal_users ({idx_u}) in lifespan"


# ---------------------------------------------------------------------------
# A-4: FORCE RLS on four tables
# ---------------------------------------------------------------------------

_A4_TABLES = (
    "portal_feedback_events",
    "widgets",
    "widget_kb_access",
    "tenant_lifecycle_events",
)


def test_a4_force_rls_set_on_four_tables() -> None:
    """All four tables from Finding A-4 must get FORCE ROW LEVEL SECURITY."""
    sql = _read_sql()
    for table in _A4_TABLES:
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql, f"must FORCE RLS on {table} (A-4)"


# ---------------------------------------------------------------------------
# A-5: Cat-C INSERT WITH CHECK tightened
# ---------------------------------------------------------------------------


def test_a5_portal_audit_log_insert_check_guards_org_id() -> None:
    """portal_audit_log INSERT WITH CHECK must not be (true) (A-5)."""
    sql = _read_sql()
    assert "DROP POLICY IF EXISTS tenant_isolation_write ON portal_audit_log" in sql
    assert "CREATE POLICY tenant_isolation_write ON portal_audit_log" in sql
    m = re.search(
        r"CREATE POLICY tenant_isolation_write ON portal_audit_log.*?;\n",
        sql,
        re.DOTALL,
    )
    assert m
    block = m.group(0)
    assert "WITH CHECK" in block
    assert "org_id" in block
    wc = re.search(r"WITH CHECK \((.+?)\);", block, re.DOTALL)
    assert wc
    cleaned = wc.group(1).replace(" ", "").replace("\\n", "").lower()
    assert cleaned != "true", "tenant_isolation_write must not use WITH CHECK (true)"


def test_a5_product_events_insert_check_guards_org_id() -> None:
    """product_events INSERT WITH CHECK must guard org_id (A-5)."""
    sql = _read_sql()
    assert "DROP POLICY IF EXISTS tenant_write ON product_events" in sql
    assert "CREATE POLICY tenant_write ON product_events" in sql
    m = re.search(
        r"CREATE POLICY tenant_write ON product_events.*?;\n",
        sql,
        re.DOTALL,
    )
    assert m
    block = m.group(0)
    assert "WITH CHECK" in block
    assert "org_id" in block


def test_a5_feedback_events_insert_check_guards_org_id() -> None:
    """portal_feedback_events INSERT WITH CHECK must guard org_id (A-5)."""
    sql = _read_sql()
    assert "DROP POLICY IF EXISTS feedback_events_insert_policy ON portal_feedback_events" in sql
    assert "CREATE POLICY feedback_events_insert_policy ON portal_feedback_events" in sql
    m = re.search(
        r"CREATE POLICY feedback_events_insert_policy ON portal_feedback_events.*?;\n",
        sql,
        re.DOTALL,
    )
    assert m
    block = m.group(0)
    assert "WITH CHECK" in block
    assert "org_id" in block


def test_a5_tenant_lifecycle_events_uses_org_id_snapshot_column() -> None:
    """tenant_lifecycle_events INSERT WITH CHECK must reference org_id_snapshot (A-5)."""
    sql = _read_sql()
    assert "DROP POLICY IF EXISTS tenant_lifecycle_events_insert ON tenant_lifecycle_events" in sql
    assert "CREATE POLICY tenant_lifecycle_events_insert ON tenant_lifecycle_events" in sql
    m = re.search(
        r"CREATE POLICY tenant_lifecycle_events_insert ON tenant_lifecycle_events.*?;\n",
        sql,
        re.DOTALL,
    )
    assert m
    block = m.group(0)
    assert "WITH CHECK" in block
    assert "org_id_snapshot" in block


# ---------------------------------------------------------------------------
# A-6: tenant_lifecycle_events SELECT GUC documentation
# ---------------------------------------------------------------------------


def test_a6_tenant_lifecycle_has_is_platform_admin_guc_docs() -> None:
    """tenant_lifecycle.py must document app.is_platform_admin GUC (A-6)."""
    src = _read_tenant_lifecycle_py()
    assert "is_platform_admin" in src, "tenant_lifecycle.py must document app.is_platform_admin GUC (A-6)"


def test_a6_documentation_mentions_is_local() -> None:
    """A-6 docs must mention is_local (transaction-scoped GUC reset)."""
    src = _read_tenant_lifecycle_py()
    assert "is_local" in src, "must mention is_local=true to prevent GUC leaks"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_sql_drop_if_exists_matches_create_policy_count() -> None:
    """Every CREATE POLICY must be preceded by DROP IF EXISTS."""
    sql = _read_sql()
    drop_count = len(re.findall("DROP POLICY IF EXISTS", sql))
    create_count = len(re.findall("CREATE POLICY", sql))
    assert drop_count >= create_count, f"{create_count} CREATE POLICY but only {drop_count} DROP IF EXISTS guards"


def test_sql_is_wrapped_in_begin_commit() -> None:
    """The post-deploy SQL must be wrapped in BEGIN / COMMIT."""
    sql = _read_sql()
    lines = sql.strip().splitlines()
    # Skip leading comments and blank lines to find the first executable statement
    first_stmt = next(
        (line.strip() for line in lines if line.strip() and not line.strip().startswith("--")),
        "",
    )
    assert first_stmt.rstrip(";") == "BEGIN", f"First executable line must be BEGIN, got: {first_stmt!r}"
    assert lines[-1].strip() == "COMMIT;"


# ---------------------------------------------------------------------------
# Per-transaction tenant context (2026-08-13) — fail-loud source tripwires
# ---------------------------------------------------------------------------
#
# The 2026-08-13 cleanup collapsed tenant context onto ONE model: the
# `after_begin` listener applies all four RLS GUCs transaction-locally at every
# BEGIN. The reset/pin machinery that used to sit alongside it was deleted —
# the cleanup-time reset never durably landed (session close rolls it back) and
# the checkout-time reset defended against a pollution source that no longer
# exists.
#
# With the defense-in-depth layers gone, these three source-level checks are the
# guard: a reintroduced session-level GUC, an unregistered listener, or a GUC
# dropped from the combined statement now fails CI instead of failing silently
# in production.

_APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_no_session_level_set_config_anywhere_in_app() -> None:
    """ZERO ``set_config(..., false)`` calls may exist under ``app/``.

    ``is_local=false`` writes survive COMMIT on the pooled connection and are
    exactly the mechanism behind the 2026-04-24 pool-pollution 404s and the
    2026-08-13 post-commit refresh 500. Nothing in portal-api needs one: the
    combined transaction-local statement is the single writer of every RLS GUC.

    Count must stay at 0 — this is the tripwire against reintroduction.
    """
    offenders: list[str] = []
    for path in sorted(_APP_DIR.rglob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"set_config\([^)]*,\s*false\s*\)", line):
                offenders.append(f"{path.relative_to(_APP_DIR.parent)}:{lineno}: {line.strip()}")
    assert offenders == [], (
        "session-level (is_local=false) set_config calls found under app/. "
        "Tenant context is per-transaction; use the combined statement in "
        "app/core/database.py instead:\n  " + "\n  ".join(offenders)
    )


def test_after_begin_listener_is_wired_to_the_tenant_session_class() -> None:
    """The listener must be registered on ``_SyncTenantContextSession`` AND that
    class must be the one ``TenantContextSession`` actually uses.

    Both halves matter. A listener registered on a class no session uses is
    dead code; a ``sync_session_class`` pointing elsewhere silently drops the
    tenant context on every transaction.
    """
    src = _read_database_py()
    assert '@event.listens_for(_SyncTenantContextSession, "after_begin")' in src, (
        "the after_begin listener must be registered on _SyncTenantContextSession"
    )
    assert re.search(r"sync_session_class\s*=\s*_SyncTenantContextSession", src), (
        "TenantContextSession.sync_session_class must point at _SyncTenantContextSession"
    )


def test_tenant_context_statement_sets_all_four_gucs_transaction_locally() -> None:
    """``_TENANT_CONTEXT_SQL`` must bind all four GUCs with ``is_local=true``.

    Dropping one silently disables a policy branch: ``app.current_org_id`` is
    tenant isolation, ``app.cross_org_admin`` the platform bypass,
    ``klai.changed_by_user_id`` the seat-history attribution, and
    ``app.is_platform_admin`` the tenant_lifecycle_events admin branch.
    """
    src = _read_database_py()
    m = re.search(r"_TENANT_CONTEXT_SQL = \((.*?)\n\)\n", src, re.DOTALL)
    assert m, "_TENANT_CONTEXT_SQL not found in database.py"
    stmt = m.group(1)
    for guc, param in (
        ("app.current_org_id", ":org_id"),
        ("app.cross_org_admin", ":cross_org_admin"),
        ("klai.changed_by_user_id", ":changed_by_user_id"),
        ("app.is_platform_admin", ":is_platform_admin"),
    ):
        assert re.search(rf"set_config\('{re.escape(guc)}',\s*{re.escape(param)},\s*true\)", stmt), (
            f"{guc} must be bound with is_local=true in _TENANT_CONTEXT_SQL. Got: {stmt}"
        )
    assert stmt.count("set_config(") == 4, f"expected exactly four set_config calls, got: {stmt}"
