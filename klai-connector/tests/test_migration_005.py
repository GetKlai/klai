"""Tests for Alembic migration 005 — adds quality_status column to sync_runs.

SPEC-CRAWL-003 REQ-2, AC-9.

The migration uses schema="connector" which is PostgreSQL-only (SQLite does not
support schemas). This test validates the migration *content* by importing the
migration module directly and verifying the upgrade/downgrade operations call the
expected alembic op methods with the correct column definition.

For a live integration test against a real Postgres instance, run:
    DATABASE_URL=postgresql+asyncpg://... alembic upgrade head
    DATABASE_URL=postgresql+asyncpg://... alembic downgrade -1
"""

from __future__ import annotations

import importlib.util
import pathlib
import types
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa


def _load_migration_module() -> types.ModuleType:
    """Load migration 005 from the alembic/versions directory by file path."""
    versions_dir = pathlib.Path(__file__).parent.parent / "alembic" / "versions"
    path = versions_dir / "migration_005_add_sync_run_quality_status.py"
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("migration_005", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def migration_module() -> types.ModuleType:
    """Load migration 005 module once per test session."""
    return _load_migration_module()


def test_migration_module_importable(migration_module: types.ModuleType) -> None:
    """Migration module 005 is importable and has expected revision metadata."""
    assert migration_module.revision == "005_add_sync_run_quality_status"
    assert migration_module.down_revision == "004_remove_sync_run_fk"


def test_migration_upgrade_adds_quality_status_column(migration_module: types.ModuleType) -> None:
    """upgrade() calls op.add_column with quality_status String(20) nullable on connector schema."""
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.upgrade()

    assert mock_op.add_column.called, "op.add_column must be called in upgrade()"

    args, kwargs = mock_op.add_column.call_args
    table_name = args[0]
    column = args[1]

    assert table_name == "sync_runs", f"Expected sync_runs, got {table_name!r}"
    assert isinstance(column, sa.Column), f"Expected sa.Column, got {type(column)}"
    assert column.name == "quality_status", f"Expected quality_status, got {column.name!r}"
    assert column.nullable is True, "quality_status must be nullable (backward compat per REQ-19)"
    assert kwargs.get("schema") == "connector", f"Expected schema='connector', got {kwargs.get('schema')!r}"


def test_migration_downgrade_drops_quality_status_column(migration_module: types.ModuleType) -> None:
    """downgrade() calls op.drop_column for quality_status on connector schema."""
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.downgrade()

    assert mock_op.drop_column.called, "op.drop_column must be called in downgrade()"

    args, kwargs = mock_op.drop_column.call_args
    table_name = args[0]
    column_name = args[1]

    assert table_name == "sync_runs", f"Expected sync_runs, got {table_name!r}"
    assert column_name == "quality_status", f"Expected quality_status, got {column_name!r}"
    assert kwargs.get("schema") == "connector", f"Expected schema='connector', got {kwargs.get('schema')!r}"


def test_migration_upgrade_does_not_add_index(migration_module: types.ModuleType) -> None:
    """upgrade() must NOT add an index for quality_status (low-cardinality per SPEC REQ-2)."""
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.upgrade()

    assert not mock_op.create_index.called, "upgrade() must not create an index for quality_status"


def test_sync_run_model_has_quality_status_field() -> None:
    """SyncRun model has quality_status: Mapped[str | None] column (VARCHAR(20), nullable)."""
    from app.models.sync_run import SyncRun

    assert hasattr(SyncRun, "quality_status"), "SyncRun must have quality_status attribute"

    col = SyncRun.__table__.c.get("quality_status")
    assert col is not None, "quality_status column not found in SyncRun.__table__"
    assert col.nullable is True, "quality_status column must be nullable"
    assert str(col.type) == "VARCHAR(20)", f"Expected VARCHAR(20), got {col.type!r}"
