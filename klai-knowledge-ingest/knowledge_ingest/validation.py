"""SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-10 -- production validation.

Read-only scan of a tenant's KB that reports the wall clusters discovered
under v2 logic plus the v1-purged pages whose v2 cluster size has dropped
below threshold (recovery candidates).

Used as the merge gate before v2 ships: ``python
scripts/validate_login_wall_detector.py --org voys --kb support`` MUST
report 0 surprise classifications before the PR lands.

The function is read-only: NULL ``content_simhash`` rows have their hash
computed in memory but NOT written back to the DB. The Phase D backfill
task is the canonical place to populate the column.
"""

from __future__ import annotations

from typing import Any

from knowledge_ingest.backfill_tasks import (
    PURGED_PLACEHOLDER_HASH,
    _count_cluster_siblings,
)
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.utils.auth_wall_detector import (
    DEFAULT_CLUSTER_MIN,
    DEFAULT_HAMMING_MAX,
)
from knowledge_ingest.utils.content_fingerprint import (
    compute_simhash,
    hamming_distance,
)

__all__ = [
    "validate_login_wall_detector",
]


async def validate_login_wall_detector(
    org_id: str,
    kb_slug: str,
    *,
    cluster_min: int = DEFAULT_CLUSTER_MIN,
    hamming_max: int = DEFAULT_HAMMING_MAX,
    sample_size: int = 10,
) -> dict[str, Any]:
    """Return a structured report of v2 cluster classifications for a tenant.

    Output schema::

        {
          "org_id": "<id>",
          "kb_slug": "<slug>",
          "total_pages": int,
          "clusters": [
            {"size": int, "sample_urls": [str, ...]}, ...
          ],
          "recovery_candidates": [str, ...],
        }

    ``clusters`` lists connected components in the Hamming-``hamming_max``
    graph whose size meets ``cluster_min`` + 1 (= the threshold +
    self). ``recovery_candidates`` are URLs currently at the placeholder
    hash whose cluster size has dropped below threshold under v2.

    @MX:ANCHOR — invariant. This is the merge-gate report consumed by
    ``scripts/validate_login_wall_detector.py`` and operator dashboards.
    The schema (top-level keys + cluster shape) is the contract; new
    fields can be added but existing names/types MUST NOT change without
    updating the script's ``_print_human`` formatter and any downstream
    JSON consumers.
    @MX:NOTE — read-only. NULL ``content_simhash`` rows have their hash
    computed in memory but NOT written back to the DB. Persistence is
    the backfill task's job (REQ-04); the validation script must not
    mutate state because operators run it before deciding to backfill.
    Reason: SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-10.
    """
    async with tenant_scoped_connection(org_id) as conn:
        rows = await conn.fetch(
            "SELECT url, raw_markdown, content_hash, content_simhash "
            "FROM knowledge.crawled_pages "
            "WHERE org_id = $1 AND kb_slug = $2 "
            "ORDER BY id",
            org_id,
            kb_slug,
        )

    return _build_report(
        rows,
        org_id=org_id,
        kb_slug=kb_slug,
        cluster_min=cluster_min,
        hamming_max=hamming_max,
        sample_size=sample_size,
    )


def _build_report(
    rows: list[Any],
    *,
    org_id: str,
    kb_slug: str,
    cluster_min: int,
    hamming_max: int,
    sample_size: int,
) -> dict[str, Any]:
    """Pure helper — exposes the report builder for direct unit testing.

    Read-only: hashes for NULL rows are computed in memory but NOT written
    back to the DB. (Phase D backfill is the canonical write path.)
    """
    url_to_hash: dict[str, int] = {}
    for row in rows:
        sh = row["content_simhash"]
        if sh is None:
            sh = compute_simhash(row["raw_markdown"] or "")
        url_to_hash[row["url"]] = sh

    # Find URLs that meet the cluster threshold (>= cluster_min OTHERS).
    flagged_urls: set[str] = set()
    for url, target_hash in url_to_hash.items():
        siblings = _count_cluster_siblings(
            url, target_hash, url_to_hash, hamming_max=hamming_max
        )
        if siblings >= cluster_min:
            flagged_urls.add(url)

    # Group flagged URLs into connected components in the Hamming-≤max graph.
    clusters: list[dict[str, Any]] = []
    visited: set[str] = set()
    for seed in flagged_urls:
        if seed in visited:
            continue
        component: list[str] = []
        stack = [seed]
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for other in flagged_urls:
                if other in visited:
                    continue
                if (
                    hamming_distance(url_to_hash[current], url_to_hash[other])
                    <= hamming_max
                ):
                    stack.append(other)
        component.sort()
        clusters.append({"size": len(component), "sample_urls": component[:sample_size]})

    clusters.sort(key=lambda c: c["size"], reverse=True)

    # Recovery candidates: currently purged AND no longer clustering.
    recovery_candidates: list[str] = []
    for row in rows:
        if row["content_hash"] != PURGED_PLACEHOLDER_HASH:
            continue
        url = row["url"]
        target_hash = url_to_hash[url]
        siblings = _count_cluster_siblings(
            url, target_hash, url_to_hash, hamming_max=hamming_max
        )
        if siblings < cluster_min:
            recovery_candidates.append(url)

    recovery_candidates.sort()

    return {
        "org_id": org_id,
        "kb_slug": kb_slug,
        "total_pages": len(rows),
        "clusters": clusters,
        "recovery_candidates": recovery_candidates,
    }
