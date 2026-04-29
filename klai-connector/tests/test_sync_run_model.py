"""SPEC-SEC-TENANT-001 A-5 — SyncRun.org_id schema contract.

Pure SQLAlchemy reflection tests; no DB connection needed. Verify that
the model declares the column with the v0.5.0 / β shape:
    org_id: VARCHAR(255), NOT NULL, indexed.

The corresponding migration (``006_add_org_id_to_sync_runs``) creates
the underlying database column with the matching definition; the model
↔ schema parity is enforced by Alembic autogenerate at deploy time, but
this test pins the model side independently.
"""

from __future__ import annotations

import uuid

from sqlalchemy import String

from app.core.enums import SyncStatus
from app.models.sync_run import SyncRun


def test_sync_run_model_declares_org_id_column() -> None:
    """REQ-7.2: org_id is a VARCHAR(255), NOT NULL, indexed."""
    column = SyncRun.__table__.c.org_id

    assert isinstance(column.type, String), (
        f"REQ-7.2 (v0.5.0): SyncRun.org_id must be String, got {type(column.type).__name__}. "
        "Type was reconciled from int to Zitadel-resourceowner string in v0.5.0 to match "
        "Connector.org_id (migration 003_org_id_string) and PortalOrg.zitadel_org_id."
    )
    assert column.type.length == 255, (
        f"REQ-7.2: SyncRun.org_id length must be 255 to match Connector.org_id, got {column.type.length}."
    )
    assert column.nullable is False, "REQ-7.1: SyncRun.org_id must be NOT NULL."
    assert column.index is True, "REQ-7.2: SyncRun.org_id must be indexed."


def test_sync_run_instance_accepts_org_id_kwarg() -> None:
    """REQ-7.2: SyncRun(...) constructor accepts org_id as kwarg.

    Pre-fix this test would raise ``TypeError: 'org_id' is an invalid
    keyword argument for SyncRun`` — proving the regression guard fired
    against any future revert that drops the column from the model.
    """
    run = SyncRun(
        connector_id=uuid.uuid4(),
        org_id="org-a-resourceowner",
        status=SyncStatus.RUNNING,
    )
    assert run.org_id == "org-a-resourceowner"
