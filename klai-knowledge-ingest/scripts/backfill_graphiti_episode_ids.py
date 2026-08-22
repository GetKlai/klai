"""Populate Graphiti episode-id lists for artifacts that only have the legacy scalar.

Usage (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \
        python -m scripts.backfill_graphiti_episode_ids --org-id <org_id> [--dry-run]

Idempotent: rows that already carry ``graphiti_episode_ids`` are left unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from knowledge_ingest.db import tenant_scoped_connection

logger = structlog.get_logger()


async def run(org_id: str, dry_run: bool) -> int:
    async with tenant_scoped_connection(org_id) as conn:
        pending = await conn.fetchval(
            """SELECT count(*)
               FROM knowledge.artifacts
               WHERE org_id = $1
                 AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL
                 AND NOT (extra::jsonb ? 'graphiti_episode_ids')""",
            org_id,
        )
        logger.info(
            "backfill_graphiti_episode_ids_plan",
            org_id=org_id,
            artifacts=pending,
            dry_run=dry_run,
        )
        if dry_run or not pending:
            return 0

        status = await conn.execute(
            """UPDATE knowledge.artifacts
               SET extra = extra::jsonb || jsonb_build_object(
                   'graphiti_episode_ids',
                   CASE
                       WHEN extra::jsonb->>'graphiti_episode_id' = 'no-chunks'
                           THEN '[]'::jsonb
                       ELSE jsonb_build_array(extra::jsonb->>'graphiti_episode_id')
                   END
               )
               WHERE org_id = $1
                 AND extra::jsonb->>'graphiti_episode_id' IS NOT NULL
                 AND NOT (extra::jsonb ? 'graphiti_episode_ids')""",
            org_id,
        )

    logger.info(
        "backfill_graphiti_episode_ids_complete",
        org_id=org_id,
        artifacts_updated=int(status.rsplit(" ", 1)[-1]),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-id", required=True, help="Zitadel org id (one tenant per run)")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan, change nothing")
    args = parser.parse_args()
    return asyncio.run(run(args.org_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
