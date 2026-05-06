"""Backfill + recovery tasks for SPEC-INGEST-LOGIN-WALL-DETECT-002.

Two operator-triggered Procrastinate tasks (NOT auto-on-deploy):

* ``backfill_detect_login_walls(org_id, kb_slug)`` — re-evaluates an existing
  tenant's KB under v2 cluster logic. For every page in
  ``knowledge.crawled_pages`` whose ``content_simhash`` is NULL, computes the
  SimHash and stores it; then for every non-placeholder page, counts how
  many OTHER pages in the same KB cluster within Hamming 3. Pages in clusters
  of >= ``cluster_min`` (default 5) are purged: Qdrant points deleted (filter
  scoped by ``org_id + kb_slug + path``, REQ-09.1) and ``content_hash`` set
  to ``__login_wall_purged__`` so the next scheduled crawl re-fetches them
  through the new ingest-time guard. Idempotent: pages already at the
  placeholder are filtered out at SQL level.

* ``recover_purged_pages(org_id, kb_slug)`` — undoes v1 false-positive purges
  by clearing the placeholder hash for any page whose v2 cluster size has
  dropped below threshold. The next scheduled crawl re-ingests these pages
  cleanly.

CLI (operator entry point)::

    python -m knowledge_ingest.backfill_tasks --org voys --kb support
    python -m knowledge_ingest.backfill_tasks --org getklai --kb voys-test --recover

Both tasks resolve ``--org`` (slug) to ``zitadel_org_id`` via ``portal_orgs``
over a regular pool connection (the slug→id table lives in the public schema
and is not RLS-restricted to the knowledge schema's tenant context). The
``backfill`` and ``recover`` operations themselves go through
``tenant_scoped_connection`` so the knowledge-schema RLS GUC is set.
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

import structlog
from qdrant_client.models import FieldCondition, Filter, MatchValue

from knowledge_ingest import pg_store, queues
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.qdrant_store import COLLECTION
from knowledge_ingest.qdrant_store import get_client as get_qdrant_client
from knowledge_ingest.utils.auth_wall_detector import (
    DEFAULT_CLUSTER_MIN,
    DEFAULT_HAMMING_MAX,
)
from knowledge_ingest.utils.content_fingerprint import (
    compute_simhash,
    hamming_distance,
)

logger = structlog.get_logger()

# Sentinel content_hash assigned to pages purged by this task. The next
# scheduled crawl compares stored vs current content_hash; placeholder ≠ any
# real hash so the page is re-fetched and re-ingested through the new
# detector at ingest time. Idempotency: SELECT excludes rows already at this
# value, so re-running the backfill on a previously-cleaned tenant is a no-op.
PURGED_PLACEHOLDER_HASH = "__login_wall_purged__"


# ---------------------------------------------------------------------------
# Cluster evaluation helper — shared between backfill and recovery.
# ---------------------------------------------------------------------------


def _count_cluster_siblings(
    target_url: str,
    target_hash: int,
    url_to_hash: dict[str, int],
    *,
    hamming_max: int,
) -> int:
    """Count OTHER URLs whose SimHash is within ``hamming_max`` of ``target``.

    O(N) per call, O(N^2) over the whole KB. Acceptable at klai's scale
    (low thousands per KB; SPEC REQ-08 budgets 50 ms per cluster query at
    1000-page KB). LSH banding is deferred — see research.md §4.2.
    """
    return sum(
        1
        for other_url, other_hash in url_to_hash.items()
        if other_url != target_url
        and hamming_distance(target_hash, other_hash) <= hamming_max
    )


# ---------------------------------------------------------------------------
# Pass 1: SimHash backfill — populate content_simhash for any NULL rows.
# ---------------------------------------------------------------------------


async def _ensure_simhashes(
    conn: Any,
    rows: list[Any],
    *,
    org_id: str,
    kb_slug: str,
) -> dict[str, int]:
    """Compute + persist SimHash for any row missing one; return url→hash map.

    Existing fingerprints are reused; missing ones are computed from
    ``raw_markdown`` and written via the standard helper so the path matches
    crawler ingest. Returned map covers every row (including those already
    populated) for the cluster scan in pass 2.
    """
    url_to_hash: dict[str, int] = {}
    for row in rows:
        sh = row["content_simhash"]
        if sh is None:
            sh = compute_simhash(row["raw_markdown"] or "")
            await pg_store.update_crawled_page_simhash(
                conn,
                org_id=org_id,
                kb_slug=kb_slug,
                url=row["url"],
                content_simhash=sh,
            )
        url_to_hash[row["url"]] = sh
    return url_to_hash


# ---------------------------------------------------------------------------
# backfill_detect_login_walls — operator-triggered task.
# ---------------------------------------------------------------------------


async def backfill_detect_login_walls(
    org_id: str,
    kb_slug: str,
    *,
    cluster_min: int = DEFAULT_CLUSTER_MIN,
    hamming_max: int = DEFAULT_HAMMING_MAX,
) -> dict[str, int]:
    """Detect + purge wall clusters in ``(org_id, kb_slug)``.

    Returns ``{"processed": N, "flagged": M, "qdrant_deleted": K}``.
    ``processed`` excludes rows already at the placeholder hash (SQL-level
    filter, makes re-runs free). ``flagged`` is the number of pages whose
    cluster size hits the threshold; each flagged page incurs one Qdrant
    delete and one Postgres UPDATE.

    REQ-09: All Postgres reads/writes go through ``tenant_scoped_connection``
    so RLS enforces ``org_id`` server-side. Qdrant deletes carry an
    ``org_id + kb_slug + path`` triple in ``Filter.must`` to comply with the
    tenant-isolation semgrep rule.
    """
    qdrant = get_qdrant_client()
    log = logger.bind(org_id=org_id, kb_slug=kb_slug)

    async with tenant_scoped_connection(org_id) as conn:
        rows = await conn.fetch(
            "SELECT url, raw_markdown, content_hash, content_simhash "
            "FROM knowledge.crawled_pages "
            "WHERE org_id = $1 AND kb_slug = $2 "
            "AND content_hash <> $3 "
            "ORDER BY id",
            org_id,
            kb_slug,
            PURGED_PLACEHOLDER_HASH,
        )

        # Pass 1: ensure every row has a SimHash (Phase D, plan.md item 1).
        url_to_hash = await _ensure_simhashes(
            conn, list(rows), org_id=org_id, kb_slug=kb_slug
        )

        # Pass 2: cluster eval in-memory (single O(N^2) scan, no extra SQL).
        processed = len(rows)
        flagged = 0
        qdrant_deleted = 0
        for row in rows:
            url = row["url"]
            target_hash = url_to_hash[url]
            cluster_size = _count_cluster_siblings(
                url, target_hash, url_to_hash, hamming_max=hamming_max
            )
            if cluster_size < cluster_min:
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
                pattern="template_cluster",
                cluster_size=cluster_size,
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
# recover_purged_pages — un-purge v1 false-positive pages.
# ---------------------------------------------------------------------------


async def recover_purged_pages(
    org_id: str,
    kb_slug: str,
    *,
    cluster_min: int = DEFAULT_CLUSTER_MIN,
    hamming_max: int = DEFAULT_HAMMING_MAX,
) -> dict[str, int]:
    """Un-purge pages whose v2 cluster size dropped below threshold.

    Returns ``{"processed": N, "recovered": M}``. ``processed`` counts only
    pages currently at the placeholder hash; ``recovered`` is how many of
    them are no longer cluster members under v2 and got their content_hash
    cleared (forcing re-ingest at the next scheduled crawl).

    Designed for one-shot operator use immediately after v2 deploys — to
    undo v1 phrase-detector FPs that v2's cluster mechanism exonerates.
    """
    log = logger.bind(org_id=org_id, kb_slug=kb_slug)

    async with tenant_scoped_connection(org_id) as conn:
        # Fetch ALL rows (including purged) so we can both un-purge purged
        # rows and use the live rows as cluster context.
        rows = await conn.fetch(
            "SELECT url, raw_markdown, content_hash, content_simhash "
            "FROM knowledge.crawled_pages "
            "WHERE org_id = $1 AND kb_slug = $2 "
            "ORDER BY id",
            org_id,
            kb_slug,
        )

        url_to_hash = await _ensure_simhashes(
            conn, list(rows), org_id=org_id, kb_slug=kb_slug
        )

        processed = 0
        recovered = 0
        for row in rows:
            if row["content_hash"] != PURGED_PLACEHOLDER_HASH:
                continue
            processed += 1
            url = row["url"]
            target_hash = url_to_hash[url]
            cluster_size = _count_cluster_siblings(
                url, target_hash, url_to_hash, hamming_max=hamming_max
            )
            if cluster_size >= cluster_min:
                # Still in a cluster under v2 — leave purged.
                continue
            # Cluster shrunk below threshold → un-purge so next crawl
            # re-ingests through the v2 ingest-time detector.
            await conn.execute(
                "UPDATE knowledge.crawled_pages "
                "SET content_hash = '' "
                "WHERE org_id = $1 AND kb_slug = $2 AND url = $3",
                org_id,
                kb_slug,
                url,
            )
            recovered += 1
            log.info(
                "recover_purged_page_unpurged",
                url=url,
                cluster_size=cluster_size,
            )

    log.info(
        "recover_purged_pages_complete",
        processed=processed,
        recovered=recovered,
    )
    return {"processed": processed, "recovered": recovered}


# ---------------------------------------------------------------------------
# Procrastinate task registration — called from enrichment_tasks.init_app
# alongside the other registrations (same pattern as connector_purge_tasks).
# ---------------------------------------------------------------------------


def register_backfill_login_walls_task(procrastinate_app: Any) -> None:
    """Register the backfill + recover tasks on the given app."""

    @procrastinate_app.task(queue=queues.ENRICH_BULK)
    async def backfill_detect_login_walls_task(
        org_id: str, kb_slug: str
    ) -> dict[str, int]:
        return await backfill_detect_login_walls(org_id=org_id, kb_slug=kb_slug)

    @procrastinate_app.task(queue=queues.ENRICH_BULK)
    async def recover_purged_pages_task(org_id: str, kb_slug: str) -> dict[str, int]:
        return await recover_purged_pages(org_id=org_id, kb_slug=kb_slug)

    procrastinate_app.backfill_detect_login_walls_task = (  # type: ignore[attr-defined]
        backfill_detect_login_walls_task
    )
    procrastinate_app.recover_purged_pages_task = (  # type: ignore[attr-defined]
        recover_purged_pages_task
    )


# ---------------------------------------------------------------------------
# CLI — operator entry point.
# ---------------------------------------------------------------------------


async def _resolve_org_slug_to_zitadel_id(org_slug: str) -> str:
    """Translate ``portal_orgs.slug`` → ``zitadel_org_id``.

    Uses a regular pool connection (not tenant-scoped) because ``portal_orgs``
    lives in the public schema and is not RLS-restricted to the ``knowledge``
    schema's tenant context.
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

    if args.recover:
        result = await recover_purged_pages(org_id=org_id, kb_slug=args.kb)
        print(
            f"recover complete: org={org_id} kb={args.kb} "
            f"processed={result['processed']} recovered={result['recovered']}"
        )
    else:
        result = await backfill_detect_login_walls(org_id=org_id, kb_slug=args.kb)
        print(
            f"backfill complete: org={org_id} kb={args.kb} "
            f"processed={result['processed']} flagged={result['flagged']} "
            f"qdrant_deleted={result['qdrant_deleted']}"
        )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m knowledge_ingest.backfill_tasks",
        description=(
            "SPEC-INGEST-LOGIN-WALL-DETECT-002 — backfill + recover login-wall "
            "purges via SimHash near-duplicate clustering."
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
    p.add_argument(
        "--recover",
        action="store_true",
        help=(
            "Run recover_purged_pages instead of backfill_detect_login_walls. "
            "Clears __login_wall_purged__ placeholder for pages whose v2 "
            "cluster size dropped below threshold."
        ),
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    if not args.org and not args.org_id:
        raise SystemExit("Either --org SLUG or --org-id ZITADEL_ID is required")
    asyncio.run(_cli_main(args))


if __name__ == "__main__":
    main()
