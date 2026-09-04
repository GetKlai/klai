"""Knowledge-gap event write-path shared by all telemetry producers.

Extracted verbatim from POST /internal/v1/gap-events so the HTTP endpoint
(LiteLLM hook → LibreChat, klai-knowledge-mcp → third-party clients) and
in-process callers — the widget / partner chatpad in
``app.services.partner_chat.retrieve_context`` — run through the identical
privacy-gating and tenant-scoping code. Portal-internal callers MUST call
this function directly, never loop back over HTTP to portal-api's own
endpoint.

SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8: the canonical per-tenant
``telemetry_level`` is always re-fetched from ``portal_orgs`` here. Callers
pass query text and retrieval metadata, never a telemetry level — an
upstream-supplied level is never trusted.

- off    → no row inserted
- shadow → row inserted with query_text='[REDACTED:shadow]'
- full   → row inserted with the literal query_text (existing behavior)

# @MX:WARN: Fire-and-forget callers must pass an RLS-scoped session — use
# @MX:WARN: app.core.database.tenant_scoped_session when no request session
# @MX:WARN: is at hand (see partner_chat._schedule_gap_event for the pattern).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.models.portal import PortalOrg
from app.models.retrieval_gaps import PortalRetrievalGap

logger = structlog.get_logger()

GapEventOutcome = Literal["created", "skipped", "not_found"]


@dataclass(frozen=True, slots=True)
class GapEventResult:
    """Outcome of one ``record_gap_event`` call.

    ``org_id`` is the resolved internal (integer) org id — ``None`` when the
    Zitadel org could not be mapped. The HTTP endpoint needs it for the
    internal-call audit; in-process callers can ignore it.
    """

    outcome: GapEventOutcome
    org_id: int | None = None


async def record_gap_event(
    db: AsyncSession,
    *,
    zitadel_org_id: str,
    user_id: str,
    query_text: str,
    gap_type: str,
    top_score: float | None = None,
    nearest_kb_slug: str | None = None,
    chunks_retrieved: int = 0,
    retrieval_ms: int = 0,
    taxonomy_node_ids: list[int] | None = None,
    caller_client_id: str | None = None,
) -> GapEventResult:
    """Insert one knowledge-gap row, gated by the org's telemetry level.

    Resolves the org by Zitadel id, binds the tenant scope for RLS
    (``set_tenant``), applies the SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8
    off/shadow/full gating, and commits through the caller-provided session.
    Never raises on a missing org — returns ``outcome='not_found'`` so the
    HTTP layer can decide how to surface it (404) while in-process callers
    just log and move on.
    """
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        return GapEventResult("not_found")
    await set_tenant(db, org.id)

    # REQ-8: 'off' → skip the INSERT entirely. Tenant accepts the
    # support-side trade-off; the endpoint still responds 200 to keep the
    # idempotent contract for fire-and-forget callers.
    if org.telemetry_level == "off":
        return GapEventResult("skipped", org.id)

    # REQ-8: 'shadow' → REDACT the literal query text. The matching
    # telemetry.query_shadow row (written by retrieval-api) carries the
    # embedding + features for support-team triage.
    effective_query_text = query_text if org.telemetry_level == "full" else "[REDACTED:shadow]"

    gap = PortalRetrievalGap(
        org_id=org.id,
        user_id=user_id,
        query_text=effective_query_text,
        gap_type=gap_type,
        top_score=top_score,
        nearest_kb_slug=nearest_kb_slug,
        chunks_retrieved=chunks_retrieved,
        retrieval_ms=retrieval_ms,
        taxonomy_node_ids=taxonomy_node_ids,
        caller_client_id=caller_client_id,
    )
    db.add(gap)
    await db.commit()

    # SPEC-KB-022 R6 + SPEC-KB-026 R4: async gap classification via knowledge-ingest
    if taxonomy_node_ids is None and nearest_kb_slug:

        async def _classify_gap(
            gap_id: int,
            org_int_id: int,
            org_zitadel_id: str,
            query_text: str,
            kb_slug: str,
        ) -> None:
            """Classify gap query against KB taxonomy via knowledge-ingest.

            Background task on a fresh session: `tenant_scoped_session`
            guarantees the connection is pinned and app.current_org_id is
            set before the UPDATE, so RLS does not silently filter the row
            to zero. rowcount==0 raises; the RLS guard event listener also
            catches this as a safety net.
            """
            try:
                from app.core.database import tenant_scoped_session
                from app.services.knowledge_ingest_client import classify_gap_taxonomy

                node_ids = await classify_gap_taxonomy(org_zitadel_id, kb_slug, query_text)
                if not node_ids:
                    return

                async with tenant_scoped_session(org_int_id) as session:
                    result = await session.execute(
                        update(PortalRetrievalGap)
                        .where(PortalRetrievalGap.id == gap_id)
                        .values(taxonomy_node_ids=node_ids)
                    )
                    if result.rowcount == 0:  # type: ignore[attr-defined]
                        raise RuntimeError(
                            f"gap_classification UPDATE matched 0 rows "
                            f"(gap_id={gap_id}, org_id={org_int_id}) — "
                            f"likely RLS/tenant-context mismatch"
                        )
                    await session.commit()

                logger.info(
                    "gap_classification_complete: gap_id=%s, node_ids=%s",
                    gap_id,
                    node_ids,
                )
            except Exception:
                logger.exception(
                    "gap_classification_failed: gap_id=%s",
                    gap_id,
                )

        _task = asyncio.create_task(  # noqa: RUF006
            _classify_gap(
                gap.id,
                org.id,
                zitadel_org_id,
                query_text,
                nearest_kb_slug,
            )
        )

    return GapEventResult("created", org.id)
