# Fixture for SPEC-SEC-PORTAL-RLS-001 — canonical tenant-scoped SyncRun query.
# `select(SyncRun)` is followed by a `.where(SyncRun.org_id == ...)` clause
# in the same chain, so the tenant-isolation barrier is in place.

from sqlalchemy import select

from app.models.sync_run import SyncRun


async def get_active_run(session, connector_id, org_id):
    query = select(SyncRun).where(
        SyncRun.connector_id == connector_id,
        SyncRun.org_id == org_id,
        SyncRun.status == "running",
    )
    result = await session.execute(query)
    return result.scalars().first()
