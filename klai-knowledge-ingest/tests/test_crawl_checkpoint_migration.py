"""Static integrity checks for the durable crawl checkpoint migration."""

from pathlib import Path

MIGRATION = (
    Path(__file__).parents[1] / "alembic" / "versions" / "0012_crawl_job_checkpoints.py"
).read_text()


def test_frontier_fk_binds_job_and_tenant_together() -> None:
    assert "UNIQUE (id, org_id)" in MIGRATION
    assert "FOREIGN KEY (job_id, org_id)" in MIGRATION
    assert "REFERENCES knowledge.crawl_jobs(id, org_id)" in MIGRATION


def test_fresh_database_always_gets_a_frontier_tenant_policy() -> None:
    assert "IF to_regprocedure('knowledge._rls_current_org_id()') IS NOT NULL" not in MIGRATION
    assert "CREATE POLICY tenant_isolation ON knowledge.crawl_job_frontier" in MIGRATION
