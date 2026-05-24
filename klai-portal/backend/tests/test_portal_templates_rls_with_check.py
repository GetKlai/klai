"""Tests for REQ-3 (Finding C-1): portal_templates RLS WITH CHECK clause.

SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-3.

Migration 34d8f876ffbf shipped the Cat-D helper pattern for USING but
omitted the explicit WITH CHECK clause. PostgreSQL reuses USING as an
implicit WITH CHECK on FOR ALL policies, which means WITH CHECK passes
ANY org_id when app.cross_org_admin=true — the superuser bypass
that is intentional for reads becomes a cross-tenant write hole.

These tests guard that the post-deploy SQL for the additive fix migration
contains the correct, explicit WITH CHECK clause so that a future reviewer
cannot land the issue again by omitting it.

All tests are pure file-content checks — no live PostgreSQL required.
"""

from __future__ import annotations

import re
from pathlib import Path

# The REQ-3 post-deploy SQL lives at this path once the migration is created.
# @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-3
POST_DEPLOY_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "post_deploy_b2d3e4f5a6c7_portal_templates_rls_with_check.sql"
)


def _read_sql() -> str:
    assert POST_DEPLOY_PATH.exists(), (
        f"REQ-3 post-deploy SQL not found at {POST_DEPLOY_PATH}. Run the migration creation step first."
    )
    return POST_DEPLOY_PATH.read_text(encoding="utf-8")


class TestPortalTemplatesWithCheck:
    """REQ-3: portal_templates RLS policy must have an explicit WITH CHECK clause."""

    def test_post_deploy_sql_file_exists(self) -> None:
        """The post-deploy SQL file for the REQ-3 migration must exist."""
        assert POST_DEPLOY_PATH.exists(), f"post_deploy SQL not found: {POST_DEPLOY_PATH}"

    def test_creates_policy_for_portal_templates(self) -> None:
        """post-deploy SQL must DROP + CREATE POLICY on portal_templates (REQ-3)."""
        sql = _read_sql()
        assert "DROP POLICY IF EXISTS tenant_isolation ON portal_templates" in sql
        assert "CREATE POLICY tenant_isolation ON portal_templates" in sql

    def test_policy_has_explicit_with_check(self) -> None:
        """portal_templates policy must have an explicit WITH CHECK clause (REQ-3)."""
        sql = _read_sql()
        m = re.search(
            r"CREATE POLICY tenant_isolation ON portal_templates.*?;",
            sql,
            re.DOTALL,
        )
        assert m, "portal_templates CREATE POLICY not found in post-deploy SQL"
        block = m.group(0)
        assert "WITH CHECK" in block, (
            "portal_templates policy must have an explicit WITH CHECK clause. "
            "Implicit re-use of USING is insufficient — it passes any org_id "
            "when app.cross_org_admin=true. Got block:\n" + block
        )

    def test_with_check_uses_helper_function(self) -> None:
        """WITH CHECK must call _rls_current_org_id() (Cat-D strict pattern)."""
        sql = _read_sql()
        m = re.search(
            r"CREATE POLICY tenant_isolation ON portal_templates.*?;",
            sql,
            re.DOTALL,
        )
        assert m
        block = m.group(0)
        # Use [^;]+ (greedy up to statement end) to capture nested parens like
        # _rls_current_org_id() correctly. Non-greedy (.+?) stops at the first )
        # which is the inner ) of the helper call, not the outer WITH CHECK ).
        wc = re.search(r"WITH CHECK\s*\(([^;]+)\)", block, re.DOTALL)
        assert wc, "Cannot extract WITH CHECK body. Block:\n" + block
        wc_body = wc.group(1)
        assert "_rls_current_org_id()" in wc_body, (
            "WITH CHECK must use _rls_current_org_id() helper (Cat-D pattern). Got: " + wc_body
        )

    def test_with_check_does_not_contain_is_null_branch(self) -> None:
        """WITH CHECK must NOT contain an IS NULL / empty-GUC permissive branch.

        portal_templates is Cat-D (strict tenant). The superuser bypass
        (app.cross_org_admin) is intentional for reads (USING); for writes
        the check must always bind to _rls_current_org_id() so a cross-org
        admin session can still only write to the tenant whose context is set.
        """
        sql = _read_sql()
        m = re.search(
            r"CREATE POLICY tenant_isolation ON portal_templates.*?;",
            sql,
            re.DOTALL,
        )
        assert m
        block = m.group(0)
        wc = re.search(r"WITH CHECK\s*\(([^;]+)\)", block, re.DOTALL)
        assert wc
        wc_body = wc.group(1)
        assert "IS NULL" not in wc_body, (
            "WITH CHECK must not have an IS NULL branch (would allow writes with no tenant context). Got: " + wc_body
        )
        assert "current_setting" not in wc_body, (
            "WITH CHECK must use _rls_current_org_id() not inline current_setting(). Got: " + wc_body
        )

    def test_using_clause_retains_is_null_branch(self) -> None:
        """USING must retain the IS NULL branch for cross-org admin reads."""
        sql = _read_sql()
        m = re.search(
            r"CREATE POLICY tenant_isolation ON portal_templates.*?;",
            sql,
            re.DOTALL,
        )
        assert m
        block = m.group(0)
        using = re.search(r"USING\s*\((.+?)\)\s+WITH CHECK", block, re.DOTALL)
        assert using, "Cannot parse USING clause. Block:\n" + block
        using_body = using.group(1)
        assert "IS NULL" in using_body, "USING must retain IS NULL branch for cross-org admin reads. Got: " + using_body
        assert "_rls_current_org_id()" in using_body, (
            "USING must call _rls_current_org_id() (Cat-D helper). Got: " + using_body
        )

    def test_sql_is_wrapped_in_begin_commit(self) -> None:
        """The post-deploy SQL must be wrapped in BEGIN / COMMIT."""
        sql = _read_sql()
        lines = sql.strip().splitlines()
        first_stmt = next(
            (line.strip() for line in lines if line.strip() and not line.strip().startswith("--")),
            "",
        )
        assert first_stmt.rstrip(";") == "BEGIN", f"First executable line must be BEGIN. Got: {first_stmt!r}"
        assert lines[-1].strip() == "COMMIT;", f"Last line must be COMMIT;. Got: {lines[-1].strip()!r}"

    def test_drop_if_exists_precedes_create(self) -> None:
        """DROP POLICY IF EXISTS must appear before CREATE POLICY (idempotency)."""
        sql = _read_sql()
        drop_pos = sql.find("DROP POLICY IF EXISTS tenant_isolation ON portal_templates")
        create_pos = sql.find("CREATE POLICY tenant_isolation ON portal_templates")
        assert drop_pos != -1, "DROP POLICY not found"
        assert create_pos != -1, "CREATE POLICY not found"
        assert drop_pos < create_pos, (
            f"DROP must precede CREATE for idempotent re-runs. drop_pos={drop_pos}, create_pos={create_pos}"
        )
