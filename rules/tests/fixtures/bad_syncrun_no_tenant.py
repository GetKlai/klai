# Fixture for SPEC-SEC-PORTAL-RLS-001 — untenanted SyncRun query.
# The lint MUST flag this: `select(SyncRun)` is bound to a variable and
# filtered only by connector_id + status. There is no SyncRun.org_id
# clause anywhere in the chain, so a row from another tenant could leak
# through (connector.sync_runs has no Postgres RLS policy — TP-5 in the
# 2026-05-04 audit).

from sqlalchemy import select

from app.models.sync_run import SyncRun


async def get_active_run(session, connector_id):
    query = select(SyncRun).where(
        SyncRun.connector_id == connector_id,
        SyncRun.status == "running",
    )
    result = await session.execute(query)
    return result.scalars().first()
