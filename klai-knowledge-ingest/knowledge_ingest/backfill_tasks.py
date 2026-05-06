"""Backfill task: detect + purge anonymous-crawl login-wall stubs.

SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-06.

Operator-triggered (NOT auto-on-deploy) Procrastinate task that scans an
existing tenant's ``knowledge.crawled_pages`` rows, runs the same anonymous-
auth-wall detector used at ingest time (Phase A), and for every match:

1. Deletes the corresponding Qdrant points (filtered by
   ``org_id + kb_slug + path`` — REQ-09.1 tenant isolation).
2. Marks the page row's ``content_hash`` to a placeholder
   (``__login_wall_purged__``) so the next scheduled crawl detects
   "stored != current" and re-ingests through the new ingest-time guard.

Idempotent: pages whose ``content_hash`` already equals the placeholder are
filtered out at SQL level — re-running the task on the same tenant after a
clean run is a no-op.

CLI entry-point::

    python -m knowledge_ingest.backfill_tasks --org voys --kb support

The CLI resolves ``--org`` (slug) to ``zitadel_org_id`` via the existing
``portal_orgs`` table over the same tenant-scoped connection used elsewhere
in this module.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import structlog
from qdrant_client.models import FieldCondition, Filter, MatchValue

from knowledge_ingest import queues
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.qdrant_store import COLLECTION
from knowledge_ingest.qdrant_store import get_client as get_qdrant_client
from knowledge_ingest.utils.auth_wall_detector import detect_anonymous_auth_wall

logger = structlog.get_logger()

# Sentinel content_hash assigned to pages purged by this task. The next
# scheduled crawl compares stored vs current content_hash; placeholder ≠ any
# real hash so the page is re-fetched and re-ingested through the new
# detector at ingest time. Idempotency: SELECT excludes rows already at this
# value, so re-running the backfill on a previously-cleaned tenant is a no-op.
PURGED_PLACEHOLDER_HASH = "__login_wall_purged__"


# ---------------------------------------------------------------------------
# Core async function — used by both the Procrastinate task and the CLI.
# ---------------------------------------------------------------------------


async def backfill_detect_login_walls(
    org_id: str,
    kb_slug: str,
) -> dict[str, int]:
    """Scan ``crawled_pages`` for ``(org_id, kb_slug)``, detect walls, purge.

    Returns:
        ``{"processed": N, "flagged": M, "qdrant_deleted": K}``. ``processed``
        excludes already-purged rows (SQL-level filter). ``flagged`` ≤
        ``processed``. ``qdrant_deleted`` ≤ ``flagged`` (one Qdrant delete
        call per flagged page).

    REQ-09: All Postgres reads/writes go through ``tenant_scoped_connection``
    so RLS enforces ``org_id`` server-side. Qdrant deletes carry an
    ``org_id + kb_slug + path`` triple in ``Filter.must`` to comply with the
    tenant-isolation semgrep rule.
    """
    qdrant = get_qdrant_client()
    log = logger.bind(org_id=org_id, kb_slug=kb_slug)

    processed = 0
    flagged = 0
    qdrant_deleted = 0

    async with tenant_scoped_connection(org_id) as conn:
        rows = await conn.fetch(
            "SELECT url, raw_markdown, content_hash "
            "FROM knowledge.crawled_pages "
            "WHERE org_id = $1 AND kb_slug = $2 "
            "AND content_hash <> $3 "
            "ORDER BY id",
            org_id,
            kb_slug,
            PURGED_PLACEHOLDER_HASH,
        )

        for row in rows:
            processed += 1
            url = row["url"]
            raw_markdown = row["raw_markdown"]

            signal = detect_anonymous_auth_wall(raw_markdown or "")
            if signal is None:
                continue

            flagged += 1

            # REQ-09.1 — tenant isolation: filter MUST include org_id +
            # kb_slug + path. Removing any one of these is blocked by the
            # semgrep rule in tenant-isolation-review.yml.
            await qdrant.delete(
                COLLECTION,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                        FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
                        FieldCondition(key="path", match=MatchValue(value=url)),
                    ]
                ),
            )
            qdrant_deleted += 1

            await conn.execute(
                "UPDATE knowledge.crawled_pages "
                "SET content_hash = $1 "
                "WHERE org_id = $2 AND kb_slug = $3 AND url = $4",
                PURGED_PLACEHOLDER_HASH,
                org_id,
                kb_slug,
                url,
            )

            log.info(
                "backfill_login_wall_purged",
                url=url,
                pattern=signal.pattern,
                confidence=signal.confidence,
            )

    log.info(
        "backfill_login_walls_complete",
        processed=processed,
        flagged=flagged,
        qdrant_deleted=qdrant_deleted,
    )
    return {
        "processed": processed,
        "flagged": flagged,
        "qdrant_deleted": qdrant_deleted,
    }


# ---------------------------------------------------------------------------
# Procrastinate task registration — called from enrichment_tasks.init_app
# alongside the other registrations (same pattern as connector_purge_tasks).
# ---------------------------------------------------------------------------


def register_backfill_login_walls_task(procrastinate_app: Any) -> None:
    """Register the ``backfill_detect_login_walls`` task on the given app."""

    @procrastinate_app.task(queue=queues.ENRICH_BULK)
    async def backfill_detect_login_walls_task(org_id: str, kb_slug: str) -> dict[str, int]:
        return await backfill_detect_login_walls(org_id=org_id, kb_slug=kb_slug)

    procrastinate_app.backfill_detect_login_walls_task = (  # type: ignore[attr-defined]
        backfill_detect_login_walls_task
    )


# ---------------------------------------------------------------------------
# CLI — operator entry point.
# ---------------------------------------------------------------------------


async def _resolve_org_slug_to_zitadel_id(org_slug: str) -> str:
    """Translate ``portal_orgs.slug`` → ``zitadel_org_id`` (the org_id used
    everywhere downstream). Reads via a regular pool connection because
    ``portal_orgs`` lives in the public schema and is not RLS-restricted to
    the ``knowledge`` schema's tenant context.
    """
    from knowledge_ingest.db import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT zitadel_org_id FROM portal_orgs WHERE slug = $1", org_slug
        )
    if row is None:
        raise SystemExit(f"unknown org slug: {org_slug!r}")
    return row["zitadel_org_id"]


async def _cli_main(args: argparse.Namespace) -> None:
    if args.org_id:
        org_id = args.org_id
    else:
        org_id = await _resolve_org_slug_to_zitadel_id(args.org)
    result = await backfill_detect_login_walls(org_id=org_id, kb_slug=args.kb)
    # Print to stdout for ops piping; structured log already carries the
    # same data into VictoriaLogs.
    print(
        f"backfill complete: org={org_id} kb={args.kb} "
        f"processed={result['processed']} flagged={result['flagged']} "
        f"qdrant_deleted={result['qdrant_deleted']}"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m knowledge_ingest.backfill_tasks",
        description=(
            "SPEC-INGEST-LOGIN-WALL-DETECT-001 — backfill: detect + purge "
            "anonymous-crawl login-wall stubs from a tenant's KB."
        ),
    )
    p.add_argument(
        "--org",
        help="Tenant slug (e.g. 'voys'). Resolved to zitadel_org_id via portal_orgs.",
    )
    p.add_argument(
        "--org-id",
        help="Bypass slug resolution and pass zitadel_org_id directly.",
    )
    p.add_argument("--kb", required=True, help="KB slug (e.g. 'support').")
    return p


def main() -> None:
    args = _build_parser().parse_args()
    if not args.org and not args.org_id:
        raise SystemExit("Either --org SLUG or --org-id ZITADEL_ID is required")
    asyncio.run(_cli_main(args))


if __name__ == "__main__":
    main()
