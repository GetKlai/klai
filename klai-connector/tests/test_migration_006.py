"""Tests for Alembic migration 006 — drop legacy connector.connectors table.

SPEC-CONNECTOR-CLEANUP-001 REQ-01.

Mock-based: validates the migration calls the expected alembic op methods.
Live Postgres validation runs in dev/staging via ``alembic upgrade head``
and ``alembic downgrade -1``. Same approach as ``test_migration_005.py``.
"""

from __future__ import annotations

import importlib.util
import pathlib
import types
from unittest.mock import MagicMock, patch

import pytest


def _load_migration_module() -> types.ModuleType:
    versions_dir = pathlib.Path(__file__).parent.parent / "alembic" / "versions"
    path = versions_dir / "006_drop_connectors_table.py"
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("migration_006", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def migration_module() -> types.ModuleType:
    return _load_migration_module()


def test_migration_module_metadata(migration_module: types.ModuleType) -> None:
    """Migration 006 has the expected revision identifiers."""
    assert migration_module.revision == "006_drop_connectors_table"
    assert migration_module.down_revision == "005_add_sync_run_quality_status"


def test_upgrade_drops_index_then_table(migration_module: types.ModuleType) -> None:
    """upgrade() drops idx_connectors_org_id and the connectors table on connector schema."""
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.upgrade()

    assert mock_op.drop_index.called, "op.drop_index must be called"
    assert mock_op.drop_table.called, "op.drop_table must be called"

    drop_index_args, drop_index_kwargs = mock_op.drop_index.call_args
    assert drop_index_args[0] == "idx_connectors_org_id"
    assert drop_index_kwargs.get("table_name") == "connectors"
    assert drop_index_kwargs.get("schema") == "connector"

    drop_table_args, drop_table_kwargs = mock_op.drop_table.call_args
    assert drop_table_args[0] == "connectors"
    assert drop_table_kwargs.get("schema") == "connector"


def test_downgrade_recreates_table_with_index(migration_module: types.ModuleType) -> None:
    """downgrade() restores the connectors table and its org_id index."""
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.downgrade()

    assert mock_op.create_table.called, "op.create_table must be called in downgrade"
    assert mock_op.create_index.called, "op.create_index must be called in downgrade"

    create_table_args, create_table_kwargs = mock_op.create_table.call_args
    assert create_table_args[0] == "connectors"
    assert create_table_kwargs.get("schema") == "connector"

    column_names = {col.name for col in create_table_args[1:]}
    expected_columns = {
        "id",
        "org_id",
        "name",
        "connector_type",
        "config",
        "credentials_enc",
        "encryption_key_version",
        "schedule",
        "is_enabled",
        "last_sync_at",
        "last_sync_status",
        "created_at",
        "updated_at",
    }
    missing = expected_columns - column_names
    assert not missing, f"Missing columns in downgrade table: {missing}"


def test_downgrade_org_id_is_string_not_uuid(migration_module: types.ModuleType) -> None:
    """downgrade() restores the post-003 schema where org_id is String(255), not UUID.

    Migration 003_org_id_string changed org_id from UUID to String. The downgrade
    of 006 must restore the most recent shape, not the original 001 shape.
    """
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.downgrade()

    create_table_args, _ = mock_op.create_table.call_args
    org_id_col = next(c for c in create_table_args[1:] if c.name == "org_id")
    assert "VARCHAR" in str(org_id_col.type).upper(), (
        f"Expected VARCHAR org_id (post-003), got {org_id_col.type!r}"
    )
