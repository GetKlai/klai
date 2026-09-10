"""Regression tests for the internal scheduled-connectors listing.

klai-connector's APScheduler bootstraps its cron job list from
GET /internal/scheduled-connectors. Before that endpoint existed the
scheduler started with 0 scheduled connectors in production even when a
schedule was set on connectors — the cross-org listing simply had no source.

The schedule write-path validation (5-field crontab) is covered here too:
the same bootstrapping bug in reverse — a stored typo like "0 3 * *" never
fires and never surfaces an error.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.conftest import make_perms


def _request() -> MagicMock:
    return MagicMock()


def _row(connector_id: str, schedule: str, zitadel_org_id: str) -> tuple[MagicMock, MagicMock]:
    """One (PortalConnector, PortalOrg) row as returned by the joined select."""
    connector = MagicMock()
    connector.id = connector_id
    connector.schedule = schedule
    org = MagicMock()
    org.zitadel_org_id = zitadel_org_id
    return connector, org


@asynccontextmanager
async def _fake_cross_org_scope(db):
    """Stand-in for app.core.database.cross_org_scope: flags the bypass on db.info."""
    db.info["cross_org_admin"] = True
    try:
        yield db
    finally:
        db.info["cross_org_admin"] = False


class TestListScheduledConnectors:
    @pytest.mark.asyncio
    async def test_scheduled_connectors_query_runs_under_cross_org_scope(self) -> None:
        """portal_knowledge_bases is FORCE-RLS: the joined select must run with the bypass live."""
        from app.api.internal import list_scheduled_connectors

        db = AsyncMock()
        db.info = {}
        seen: list[bool | None] = []

        async def _execute(stmt):
            seen.append(db.info.get("cross_org_admin"))
            result = MagicMock()
            result.all.return_value = []
            return result

        db.execute = AsyncMock(side_effect=_execute)

        with (
            patch("app.api.internal._require_internal_token", new=AsyncMock()),
            patch("app.api.internal._audit_internal_call", new=AsyncMock()),
            patch("app.api.internal.cross_org_scope", _fake_cross_org_scope),
        ):
            items = await list_scheduled_connectors(request=_request(), db=db)

        assert items == []
        assert seen == [True]
        assert db.info["cross_org_admin"] is False

    @pytest.mark.asyncio
    async def test_scheduled_connectors_lists_only_enabled_active_with_schedule(self) -> None:
        from app.api.internal import list_scheduled_connectors

        db = AsyncMock()
        db.info = {}
        result = MagicMock()
        result.all.return_value = [
            _row("11111111-1111-1111-1111-111111111111", "0 3 * * *", "200000000000000001"),
            _row("22222222-2222-2222-2222-222222222222", "*/15 * * * *", "200000000000000002"),
        ]
        db.execute = AsyncMock(return_value=result)

        with (
            patch("app.api.internal._require_internal_token", new=AsyncMock()),
            patch("app.api.internal._audit_internal_call", new=AsyncMock()),
            patch("app.api.internal.cross_org_scope", _fake_cross_org_scope),
        ):
            items = await list_scheduled_connectors(request=_request(), db=db)

        assert [item.connector_id for item in items] == [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]
        assert [item.zitadel_org_id for item in items] == [
            "200000000000000001",
            "200000000000000002",
        ]
        assert [item.schedule for item in items] == ["0 3 * * *", "*/15 * * * *"]

        # The filter lives in SQL, not in Python post-filtering: rows with
        # is_enabled false, state != 'active' or no schedule must never be
        # fetched (klai-connector trusts the list verbatim).
        stmt = db.execute.await_args.args[0]
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "portal_connectors.is_enabled IS true" in sql
        assert "portal_connectors.state = 'active'" in sql
        assert "portal_connectors.schedule IS NOT NULL" in sql
        # Personal KBs carry an item quota that scheduled syncs cannot check.
        assert "portal_knowledge_bases.owner_type = 'org'" in sql

    @pytest.mark.asyncio
    async def test_scheduled_connectors_requires_internal_token(self) -> None:
        from app.api.internal import list_scheduled_connectors

        db = AsyncMock()
        db.execute = AsyncMock()

        with (
            patch(
                "app.api.internal._require_internal_token",
                new=AsyncMock(side_effect=HTTPException(status_code=401, detail="Unauthorized")),
            ),
            patch("app.api.internal._audit_internal_call", new=AsyncMock()),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await list_scheduled_connectors(request=_request(), db=db)

        assert exc_info.value.status_code == 401
        db.execute.assert_not_awaited()


class TestScheduleValidation:
    @pytest.mark.asyncio
    async def test_update_connector_rejects_malformed_schedule(self) -> None:
        from app.api.connectors import ConnectorUpdateRequest, update_connector

        connector = MagicMock()
        connector.id = "11111111-1111-1111-1111-111111111111"
        connector.schedule = "0 3 * * *"

        kb = MagicMock()
        kb.id = 123
        result = MagicMock()
        result.scalar_one_or_none.return_value = connector
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result)

        with (
            patch("app.api.connectors._get_kb_with_owner_check", new=AsyncMock(return_value=kb)),
            patch("app.api.connectors._connector_out", return_value=MagicMock()),
        ):
            # 4-field typo must fail loudly instead of being stored dead.
            with pytest.raises(HTTPException) as exc_info:
                await update_connector(
                    kb_slug="my-kb",
                    connector_id=str(connector.id),
                    body=ConnectorUpdateRequest(schedule="0 3 * *"),
                    perms=make_perms(),
                    db=db,
                )

            assert exc_info.value.status_code == 422
            assert exc_info.value.detail == {"error_code": "invalid_schedule"}
            assert connector.schedule == "0 3 * * *"

            # A valid 5-field crontab is stored unchanged.
            await update_connector(
                kb_slug="my-kb",
                connector_id=str(connector.id),
                body=ConnectorUpdateRequest(schedule="0 3 * * *"),
                perms=make_perms(),
                db=db,
            )
            assert connector.schedule == "0 3 * * *"
