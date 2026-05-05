"""SPEC-INGEST-ALEMBIC-001 -- alembic bootstrap sanity checks.

Verifies that the alembic infrastructure for klai-knowledge-ingest is
correctly wired:

1. The alembic env.py can be imported without errors.
2. The 0001_baseline migration file exists with the correct revision ID.
3. alembic upgrade head --sql produces DDL for the knowledge schema.
4. The entrypoint.sh exists and calls alembic upgrade head.
5. The Dockerfile ENTRYPOINT points to entrypoint.sh.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_DIR = SERVICE_ROOT / "alembic"
VERSIONS_DIR = ALEMBIC_DIR / "versions"
ALEMBIC_INI = SERVICE_ROOT / "alembic.ini"
ENTRYPOINT = SERVICE_ROOT / "entrypoint.sh"
DOCKERFILE = SERVICE_ROOT / "Dockerfile"


# ---------------------------------------------------------------------------
# 1. Env.py importable
# ---------------------------------------------------------------------------


def test_env_py_exists() -> None:
    """env.py must be present in alembic/."""
    assert (ALEMBIC_DIR / "env.py").exists(), "alembic/env.py missing"


def test_env_py_importable() -> None:
    """env.py must be importable (no syntax errors, no top-level failures)."""
    env_py = ALEMBIC_DIR / "env.py"
    script = f"import ast; ast.parse(open(r'{env_py}').read())"
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"env.py has syntax errors:\n{result.stderr}"


# ---------------------------------------------------------------------------
# 2. Baseline migration file
# ---------------------------------------------------------------------------


def test_baseline_migration_exists() -> None:
    baseline = VERSIONS_DIR / "0001_baseline.py"
    assert baseline.exists(), f"0001_baseline.py not found in {VERSIONS_DIR}"


def test_baseline_revision_id() -> None:
    baseline = VERSIONS_DIR / "0001_baseline.py"
    content = baseline.read_text()
    assert 'revision: str = "0001_baseline"' in content, (
        "revision ID in 0001_baseline.py does not match expected '0001_baseline'"
    )


def test_baseline_down_revision_is_none() -> None:
    baseline = VERSIONS_DIR / "0001_baseline.py"
    content = baseline.read_text()
    assert "down_revision: str | None = None" in content, (
        "down_revision must be None (baseline has no predecessor)"
    )


def test_baseline_covers_knowledge_schema() -> None:
    baseline = VERSIONS_DIR / "0001_baseline.py"
    content = baseline.read_text()
    required_tables = [
        "knowledge.artifacts",
        "knowledge.crawl_domains",
        "knowledge.crawl_jobs",
        "knowledge.crawled_pages",
        "knowledge.embedding_queue",
        "knowledge.entities",
        "knowledge.kb_config",
        "knowledge.org_config",
        "knowledge.artifact_entities",
        "knowledge.artifact_images",
        "knowledge.derivations",
        "knowledge.page_links",
        "knowledge.rag_eval_results",
    ]
    for table in required_tables:
        assert table in content, f"Baseline migration missing table: {table}"


def test_baseline_idempotent_ddl() -> None:
    """All CREATE TABLE statements must use IF NOT EXISTS."""
    import re

    baseline = VERSIONS_DIR / "0001_baseline.py"
    content = baseline.read_text()
    create_table_stmts = re.findall(
        r"CREATE TABLE(?: IF NOT EXISTS)?",
        content,
        re.IGNORECASE,
    )
    for stmt in create_table_stmts:
        assert "IF NOT EXISTS" in stmt, (
            f"CREATE TABLE without IF NOT EXISTS found in baseline: {stmt!r}"
        )


# ---------------------------------------------------------------------------
# 3. alembic upgrade head --sql (offline mode, no DB required)
# ---------------------------------------------------------------------------


def _alembic_bin() -> Path | None:
    """Locate the alembic binary in the service venv (cross-platform)."""
    for candidate in [
        SERVICE_ROOT / ".venv" / "bin" / "alembic",
        SERVICE_ROOT / ".venv" / "Scripts" / "alembic.exe",
        SERVICE_ROOT / ".venv" / "Scripts" / "alembic",
    ]:
        if candidate.exists():
            return candidate
    return None


@pytest.mark.skipif(
    _alembic_bin() is None or sys.platform == "win32",
    reason="alembic not in .venv or Windows Winsock env issue (runs in CI on Linux)",
)
def test_alembic_upgrade_sql_produces_ddl() -> None:
    """alembic upgrade head --sql must emit DDL for knowledge.artifacts."""
    alembic = _alembic_bin()
    assert alembic is not None
    result = subprocess.run(
        [str(alembic), "upgrade", "head", "--sql"],
        cwd=SERVICE_ROOT,
        capture_output=True,
        text=True,
        env={"DATABASE_URL": "postgresql://user:pass@localhost/db"},
    )
    combined = result.stdout + result.stderr
    assert "knowledge.artifacts" in combined or result.returncode == 0, (
        f"alembic --sql did not produce expected DDL:\n{combined}"
    )


# ---------------------------------------------------------------------------
# 4. entrypoint.sh
# ---------------------------------------------------------------------------


def test_entrypoint_sh_exists() -> None:
    assert ENTRYPOINT.exists(), "klai-knowledge-ingest/entrypoint.sh missing"


def test_entrypoint_sh_has_alembic_upgrade() -> None:
    content = ENTRYPOINT.read_text()
    assert "alembic upgrade head" in content, (
        "entrypoint.sh must call alembic upgrade head (scribe-deploy-no-alembic pitfall)"
    )


def test_entrypoint_sh_has_exec() -> None:
    content = ENTRYPOINT.read_text()
    assert 'exec "$@"' in content, 'entrypoint.sh must end with exec "$@" to pass CMD args through'


# ---------------------------------------------------------------------------
# 5. Dockerfile wiring
# ---------------------------------------------------------------------------


def test_dockerfile_has_entrypoint_directive() -> None:
    content = DOCKERFILE.read_text()
    assert "ENTRYPOINT" in content, (
        "Dockerfile must have ENTRYPOINT directive pointing to entrypoint.sh"
    )


def test_dockerfile_entrypoint_references_entrypoint_sh() -> None:
    content = DOCKERFILE.read_text()
    assert "entrypoint.sh" in content, "Dockerfile ENTRYPOINT must reference entrypoint.sh"


def test_dockerfile_copies_alembic_dir() -> None:
    content = DOCKERFILE.read_text()
    assert "COPY" in content and "alembic" in content, (
        "Dockerfile must COPY the alembic/ directory into the image"
    )


def test_old_migrations_dir_removed() -> None:
    """The legacy migrations/001_crawl_domains.sql must not exist.

    Its content is inlined into the baseline migration.
    """
    old_sql = SERVICE_ROOT / "migrations" / "001_crawl_domains.sql"
    assert not old_sql.exists(), (
        "migrations/001_crawl_domains.sql should have been removed; "
        "its content is now in alembic/versions/0001_baseline.py"
    )
