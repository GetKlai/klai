from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


async def _receive(monkeypatch, *, documents_changed: int | None) -> tuple[MagicMock, AsyncMock, MagicMock]:
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
    schedule_support_reanalysis = MagicMock()
    monkeypatch.setattr(internal, "_require_internal_token", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())
    monkeypatch.setattr(internal, "schedule_rescore", schedule_rescore)
    monkeypatch.setattr(internal, "schedule_support_reanalysis", schedule_support_reanalysis)

    fields: dict = {
        "sync_run_id": "sync-run-1",
        "status": "completed",
        "completed_at": datetime(2026, 6, 3, 16, 0, tzinfo=UTC),
        "documents_ok": 4,
    }
    if documents_changed is not None:
        fields["documents_changed"] = documents_changed
    await internal.receive_sync_status(
        connector_id="connector-1",
        body=internal.SyncStatusCallback(**fields),
        request=MagicMock(),
        db=db,
    )
    return connector, schedule_rescore, schedule_support_reanalysis


@pytest.mark.asyncio
@pytest.mark.parametrize("documents_changed", [0, None])
async def test_sync_without_changed_documents_rescores_gaps_but_reanalyses_no_support_cases(
    monkeypatch, documents_changed
) -> None:
    """A sync that changed nothing, or an older connector that does not report
    changes, keeps the cheap retrieval rescore and spends nothing on support-case
    reanalysis."""
    from app.api import internal

    connector, schedule_rescore, schedule_support_reanalysis = await _receive(
        monkeypatch, documents_changed=documents_changed
    )

    assert connector.last_sync_status == "completed"
    assert connector.last_sync_documents_ok == 4
    schedule_rescore.assert_awaited_once_with(
        org_id=77,
        zitadel_org_id="zitadel-org-77",
        kb_slug=None,
        db_factory=internal.get_db,
        delay_seconds=0.0,
        reanalyse_support=False,
    )
    schedule_support_reanalysis.assert_not_called()


@pytest.mark.asyncio
async def test_sync_with_changed_documents_schedules_support_reanalysis(monkeypatch) -> None:
    from app.api import internal

    _, schedule_rescore, schedule_support_reanalysis = await _receive(monkeypatch, documents_changed=3)

    schedule_rescore.assert_awaited_once()
    schedule_support_reanalysis.assert_called_once_with(
        org_id=77,
        zitadel_org_id="zitadel-org-77",
        db_factory=internal.get_db,
    )
