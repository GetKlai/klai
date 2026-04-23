"""Tests for Alembic migration 007 — cross-schema FK sync_runs -> portal_connectors.

SPEC-CONNECTOR-CLEANUP-001 REQ-04 + AC-04.

Mock-based: validates the migration calls the expected alembic op methods
and that the orphan pre-check raises a clear error before the FK is
created. Live Postgres validation (FK actually cascades on portal delete,
cross-schema permission check) runs in dev/staging via
``alembic upgrade head`` followed by an insert+delete cascade smoke test.
Same approach as ``test_migration_005.py`` and ``test_migration_006.py``.
"""

from __future__ import annotations

import importlib.util
import pathlib
import types
from unittest.mock import MagicMock, patch

import pytest


def _load_migration_module() -> types.ModuleType:
    versions_dir = pathlib.Path(__file__).parent.parent / "alembic" / "versions"
    path = versions_dir / "007_sync_runs_fk_portal_connectors.py"
    assert path.exists(), f"Migration file not found: {path}"
    spec = importlib.util.spec_from_file_location("migration_007", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def migration_module() -> types.ModuleType:
    return _load_migration_module()


def _patch_op_with_clean_db(
    migration_module: types.ModuleType,
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_op, mock_bind) configured for a clean DB.

    The bind responds to two execute calls in order:
    1. REFERENCES privilege check -> True (privilege granted)
    2. Orphan sync_runs query -> empty list (no orphans)
    """
    mock_op = MagicMock()
    mock_bind = MagicMock()

    # Result objects for each successive bind.execute() call.
    references_result = MagicMock()
    references_result.scalar.return_value = True
    orphan_result = MagicMock()
    orphan_result.fetchall.return_value = []

    # bind.execute is called twice in upgrade(); side_effect feeds them in order.
    mock_bind.execute.side_effect = [references_result, orphan_result]
    mock_op.get_bind.return_value = mock_bind
    return mock_op, mock_bind


def test_migration_module_metadata(migration_module: types.ModuleType) -> None:
    """Migration 007 has the expected revision identifiers."""
    assert migration_module.revision == "007_sync_runs_fk_portal_connectors"
    assert migration_module.down_revision == "006_drop_connectors_table"


def test_upgrade_runs_references_check_then_orphan_check_before_fk_create(
    migration_module: types.ModuleType,
) -> None:
    """upgrade() runs REFERENCES privilege check then orphan check before FK create."""
    mock_op, mock_bind = _patch_op_with_clean_db(migration_module)

    with patch.object(migration_module, "op", mock_op):
        migration_module.upgrade()

    # Two executes expected: first is REFERENCES privilege, then orphan query.
    assert mock_bind.execute.call_count == 2, (
        f"Expected 2 pre-checks (REFERENCES + orphans), got {mock_bind.execute.call_count}"
    )

    references_sql = str(mock_bind.execute.call_args_list[0].args[0])
    assert "has_table_privilege" in references_sql
    assert "public.portal_connectors" in references_sql
    assert "REFERENCES" in references_sql

    orphan_sql = str(mock_bind.execute.call_args_list[1].args[0])
    assert "connector.sync_runs" in orphan_sql
    assert "public.portal_connectors" in orphan_sql
    assert "LEFT JOIN" in orphan_sql.upper()
    assert "IS NULL" in orphan_sql.upper()


def test_upgrade_aborts_when_references_privilege_missing(
    migration_module: types.ModuleType,
) -> None:
    """upgrade() raises RuntimeError with GRANT instructions if REFERENCES denied."""
    mock_op = MagicMock()
    mock_bind = MagicMock()

    # First execute (REFERENCES check) returns False
    references_result = MagicMock()
    references_result.scalar.return_value = False
    mock_bind.execute.return_value = references_result
    mock_op.get_bind.return_value = mock_bind

    with patch.object(migration_module, "op", mock_op), pytest.raises(RuntimeError) as exc_info:
        migration_module.upgrade()

    error_msg = str(exc_info.value)
    assert "REFERENCES" in error_msg
    assert "GRANT" in error_msg
    assert "public.portal_connectors" in error_msg
    # FK creation must not have been attempted
    assert not mock_op.create_foreign_key.called, (
        "FK creation must be skipped when REFERENCES privilege is missing"
    )


def test_upgrade_creates_cross_schema_cascade_fk(
    migration_module: types.ModuleType,
) -> None:
    """upgrade() creates the FK with CASCADE across the connector and public schemas."""
    mock_op, _ = _patch_op_with_clean_db(migration_module)

    with patch.object(migration_module, "op", mock_op):
        migration_module.upgrade()

    assert mock_op.create_foreign_key.called, "op.create_foreign_key must be called"
    kwargs = mock_op.create_foreign_key.call_args.kwargs

    assert kwargs["constraint_name"] == "fk_sync_runs_connector_id_portal_connectors"
    assert kwargs["source_table"] == "sync_runs"
    assert kwargs["referent_table"] == "portal_connectors"
    assert kwargs["local_cols"] == ["connector_id"]
    assert kwargs["remote_cols"] == ["id"]
    assert kwargs["source_schema"] == "connector"
    assert kwargs["referent_schema"] == "public"
    assert kwargs["ondelete"] == "CASCADE"


def test_upgrade_aborts_when_orphans_exist(
    migration_module: types.ModuleType,
) -> None:
    """upgrade() raises RuntimeError with actionable message when orphans found."""
    mock_op = MagicMock()
    mock_bind = MagicMock()

    # First execute: REFERENCES check passes
    references_result = MagicMock()
    references_result.scalar.return_value = True
    # Second execute: orphan query returns 2 rows
    orphan_result = MagicMock()
    orphan_result.fetchall.return_value = [
        ("11111111-1111-1111-1111-111111111111", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ("22222222-2222-2222-2222-222222222222", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
    ]
    mock_bind.execute.side_effect = [references_result, orphan_result]
    mock_op.get_bind.return_value = mock_bind

    with patch.object(migration_module, "op", mock_op), pytest.raises(RuntimeError) as exc_info:
        migration_module.upgrade()

    error_msg = str(exc_info.value)
    assert "orphan sync_runs" in error_msg
    assert "11111111-1111-1111-1111-111111111111" in error_msg
    # FK creation must NOT have been attempted after the orphan check failed
    assert not mock_op.create_foreign_key.called, (
        "FK creation must be skipped when orphans are detected"
    )


def test_downgrade_drops_fk(migration_module: types.ModuleType) -> None:
    """downgrade() drops the FK constraint without touching data."""
    mock_op = MagicMock()

    with patch.object(migration_module, "op", mock_op):
        migration_module.downgrade()

    assert mock_op.drop_constraint.called, "op.drop_constraint must be called"
    args, kwargs = mock_op.drop_constraint.call_args
    assert args[0] == "fk_sync_runs_connector_id_portal_connectors"
    assert args[1] == "sync_runs"
    assert kwargs.get("schema") == "connector"
    assert kwargs.get("type_") == "foreignkey"
