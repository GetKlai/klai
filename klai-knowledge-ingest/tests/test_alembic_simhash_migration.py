"""SPEC-INGEST-LOGIN-WALL-DETECT-002 Phase A -- content_simhash migration.

Validates that ``alembic/versions/0006_crawled_pages_simhash.py`` exists with
the correct revision chain, adds the ``content_simhash bigint`` column on
``knowledge.crawled_pages``, creates the partial index supporting cluster
lookups, is idempotent on re-run, and reverses cleanly on downgrade.

The migration was renamed from 0005 to 0006 in the hotfix branch
``fix/login-wall-detect-002-alembic-head`` after PR #440
(SPEC-INGEST-RECONCILE-001) merged its own ``0005_crawl_jobs_fetch_outcomes``
chained on the same parent, leaving alembic with two heads. Re-chaining
on a8c5e1d2f3b4 serialises the chain.

These are static-content tests (no live DB required) following the pattern set
by ``test_alembic_bootstrap.py``. A live-DB integration test would belong in
the docker-compose CI matrix; the static checks pin the migration's DDL shape
so a regression is caught at unit-test time.
"""

from __future__ import annotations

import re
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = SERVICE_ROOT / "alembic" / "versions"
MIGRATION = VERSIONS_DIR / "0006_crawled_pages_simhash.py"


def test_migration_file_exists() -> None:
    assert MIGRATION.exists(), f"0006_crawled_pages_simhash.py missing in {VERSIONS_DIR}"


def test_revision_chains_after_0005_fetch_outcomes() -> None:
    """Chains on a8c5e1d2f3b4 (0005_crawl_jobs_fetch_outcomes) — PR #440 merged
    with that revision before this migration landed; rebasing avoids the
    "multiple alembic heads" failure that broke the entrypoint.
    """
    content = MIGRATION.read_text()
    assert 'down_revision: str | None = "a8c5e1d2f3b4"' in content, (
        "0006 must chain after 0005_crawl_jobs_fetch_outcomes (revision a8c5e1d2f3b4)"
    )


def test_revision_id_is_distinct_uuid_style() -> None:
    """Revision id must be a 12-char hex string and not collide with predecessors."""
    content = MIGRATION.read_text()
    match = re.search(r'^revision: str = "([^"]+)"', content, re.MULTILINE)
    assert match, "top-level `revision: str = \"...\"` declaration not found"
    rev = match.group(1)
    assert re.fullmatch(r"[0-9a-f]{12}", rev), (
        f"revision id {rev!r} must be 12 lowercase hex chars, matching "
        "the pattern set by 0002-0004"
    )
    assert rev not in {
        "603787256fb8",
        "9a3c4d5e6f7b",
        "dd1b439a57d0",
        "0001_baseline",
        "a8c5e1d2f3b4",  # 0005_crawl_jobs_fetch_outcomes
    }, f"revision id {rev!r} collides with an existing migration"


def test_adds_content_simhash_column() -> None:
    content = MIGRATION.read_text()
    upgrade = content.split("def upgrade")[1].split("def downgrade")[0]
    assert "ADD COLUMN IF NOT EXISTS content_simhash bigint" in upgrade, (
        "upgrade() must add `content_simhash bigint` with IF NOT EXISTS guard"
    )
    assert "knowledge.crawled_pages" in upgrade


def test_creates_partial_index_on_org_kb_simhash() -> None:
    content = MIGRATION.read_text()
    upgrade = content.split("def upgrade")[1].split("def downgrade")[0]
    assert "CREATE INDEX IF NOT EXISTS idx_crawled_pages_simhash_org_kb" in upgrade
    assert "knowledge.crawled_pages" in upgrade
    assert "(org_id, kb_slug, content_simhash)" in upgrade
    assert "WHERE content_simhash IS NOT NULL" in upgrade, (
        "Partial index must filter on non-NULL simhash to keep cold-start cost low"
    )


def test_downgrade_drops_index_and_column() -> None:
    content = MIGRATION.read_text()
    downgrade = content.split("def downgrade")[1]
    assert (
        "DROP INDEX IF EXISTS knowledge.idx_crawled_pages_simhash_org_kb" in downgrade
        or "DROP INDEX IF EXISTS idx_crawled_pages_simhash_org_kb" in downgrade
    ), "downgrade() must drop the partial index"
    assert "DROP COLUMN IF EXISTS content_simhash" in downgrade


def test_migration_upgrade_is_idempotent() -> None:
    """All DDL in upgrade() must use IF NOT EXISTS guards (pattern from 0004)."""
    content = MIGRATION.read_text()
    upgrade = content.split("def upgrade")[1].split("def downgrade")[0]
    # Each DDL action must be guarded
    assert upgrade.count("IF NOT EXISTS") >= 2, (
        "upgrade() must use IF NOT EXISTS for both ADD COLUMN and CREATE INDEX"
    )


def test_spec_id_referenced_in_module_docstring() -> None:
    content = MIGRATION.read_text()
    assert "SPEC-INGEST-LOGIN-WALL-DETECT-002" in content, (
        "Migration must cite SPEC-INGEST-LOGIN-WALL-DETECT-002 for traceability"
    )
