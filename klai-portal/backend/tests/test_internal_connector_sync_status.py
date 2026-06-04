from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_completed_sync_status_schedules_gap_rescore(monkeypatch) -> None:
    from app.api import internal

    connector = MagicMock()
    connector.org_id = 77
    connector.last_sync_at = None
    connector.last_sync_status = None
    connector.last_sync_documents_ok = None

    org = MagicMock()
    org.zitadel_org_id = "zitadel-org-77"

    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = org

    db = AsyncMock()
    db.get = AsyncMock(return_value=connector)
    db.execute = AsyncMock(return_value=org_result)
    db.commit = AsyncMock()

    schedule_rescore = AsyncMock()
    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())
    monkeypatch.setattr(internal, "schedule_rescore", schedule_rescore)

    completed_at = datetime(2026, 6, 3, 16, 0, tzinfo=UTC)
    body = internal.SyncStatusCallback(
        sync_run_id="sync-run-1",
        status="completed",
        completed_at=completed_at,
        documents_ok=4,
    )

    await internal.receive_sync_status(
        connector_id="connector-1",
        body=body,
        request=MagicMock(),
        db=db,
    )

    assert connector.last_sync_at == completed_at
    assert connector.last_sync_status == "completed"
    assert connector.last_sync_documents_ok == 4
    schedule_rescore.assert_awaited_once_with(
        org_id=77,
        zitadel_org_id="zitadel-org-77",
        kb_slug=None,
        db_factory=internal.get_db,
        delay_seconds=0.0,
    )
