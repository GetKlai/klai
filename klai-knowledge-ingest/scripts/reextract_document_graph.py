"""Show, and only with --execute repair, one document whose graph extraction was lost.

FalkorDB persists by snapshot only. A crash or a kill during the shutdown save
loses the writes since the last snapshot, while Postgres already records the
extraction as complete; the nightly reconcile reports such rows as
``graph_lost_extractions``. This command handles one of them.

By default it prints what it finds: whether the row is active, its recorded
episode ids, which of them the graph holds, and its extraction version.
``--execute`` marks the extraction as not done (the episodes that still exist
stay recorded in front of the new parts, ``graphiti_episode_ids_from``), so the
current-content rule falls back to the previous finished extraction meanwhile,
and queues the normal graph job through ``graph_refresh`` as a user-requested
refresh (no daily cap, no cool-off). The new extraction retires the earlier
ones when it completes, like any other.

It only touches what it is for: an active row whose extraction is marked
finished, with stored text, and with at least one recorded episode missing from
the graph. It refuses anything else, an unfinished row in particular: there an
extraction is queued, running, failed or quarantined, which is the job's own
state, and rewriting the row could drop a part the job is appending. The
repair is all-or-nothing: when the job is not queued, the row is put back
exactly as it was, so the nightly check keeps reporting the loss.
Run it by hand only (in a knowledge-ingest container):

    docker exec klai-core-knowledge-ingest-1 \\
        python -m scripts.reextract_document_graph --org <zitadel_org_id> \\
        --artifact <artifact_id> [--execute]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from knowledge_ingest import graph as graph_module
from knowledge_ingest import pg_store
from knowledge_ingest.db import tenant_scoped_connection
from knowledge_ingest.graph_refresh import maybe_refresh_stale_graph
from knowledge_ingest.scripts.requeue_failed_artifacts import _procrastinate_app


async def run(org_id: str, artifact_id: str, execute: bool) -> int:
    async with tenant_scoped_connection(org_id) as conn:
        graph_row = next(
            (
                row
                for row in await pg_store.get_graph_rows_for_org(conn, org_id)
                if row["id"] == artifact_id
            ),
            None,
        )
        if graph_row is None:
            print(f"artifact {artifact_id} not found in org {org_id}")
            return 2
        row = await conn.fetchrow(
            "SELECT content_type, belief_time_start, extra::text AS extra "
            "FROM knowledge.artifacts WHERE org_id = $1 AND id = $2::uuid",
            org_id,
            artifact_id,
        )
        original = row["extra"]
        extra = json.loads(original)
        recorded = list(graph_row["episode_ids"])
        stored = await graph_module.stored_episode_uuids(org_id)
        existing = [episode_id for episode_id in recorded if episode_id in stored]
        missing = [episode_id for episode_id in recorded if episode_id not in stored]
        print(f"artifact {artifact_id} in org {org_id}")
        print(f"  active: {graph_row['active']}")
        print(f"  extraction_version: {extra.get('graphiti_extraction_version')}")
        print(f"  finished: {graph_row['finished']}")
        print(f"  recorded_episode_ids: {recorded}")
        print(f"  missing_in_graph: {missing}")
        refusal = None
        if not graph_row["active"]:
            refusal = "the row is not active; only a current version is re-extracted"
        elif not graph_row["finished"]:
            refusal = (
                "the extraction is not finished (queued, running, failed or quarantined); "
                "that is the graph job's own state, not a lost write"
            )
        elif not missing:
            refusal = "every recorded episode is in the graph; nothing was lost"
        elif not extra.get("document_text"):
            refusal = "the row has no stored document_text, so the graph job could not run"
        if refusal:
            print(f"REFUSING: {refusal}")
            return 2
        if not execute:
            return 0

        # Version 0 makes graph_refresh treat the extraction as stale, which is
        # what queues it; complete=False with the surviving ids in front is the
        # state ingest_graphiti_episode resumes from as a fresh extraction.
        patch = {
            "graphiti_episode_ids": existing,
            "graphiti_episode_ids_from": len(existing),
            "graphiti_episode_id": existing[0] if existing else None,
            "graphiti_episode_complete": False,
            "graphiti_extraction_version": 0,
        }
        await pg_store.update_artifact_extra(conn, artifact_id, patch)
        async with _procrastinate_app():
            result = await maybe_refresh_stale_graph(
                conn,
                artifact_id=artifact_id,
                extra={**extra, **patch},
                org_id=org_id,
                kb_slug=graph_row["kb_slug"],
                path=graph_row["path"],
                content_type=row["content_type"],
                belief_time_start=row["belief_time_start"],
                indexable_content=extra["document_text"],
                user_requested=True,
            )
        print(f"  graph_refresh: {result}")
        # "skipped:<reason>": the text is now held out of the graph, and
        # graph_refresh has already finished the row that way (its earlier
        # extractions retired, the skip marker written), so there is nothing to
        # put back and nothing lost left to report.
        if result in ("queued", "already_queued") or (result or "").startswith("skipped:"):
            return 0
        # No job: an unfinished row without one would hide the loss from the
        # nightly check, which skips unfinished rows. Put it back as it was.
        await conn.execute(
            "UPDATE knowledge.artifacts SET extra = $3::jsonb WHERE org_id = $1 AND id = $2::uuid",
            org_id,
            artifact_id,
            original,
        )
    print("FAILED to queue the graph job; the row is back as it was, the loss stays reported")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", required=True, help="Zitadel org id")
    parser.add_argument("--artifact", required=True, help="Artifact id of the active version")
    parser.add_argument(
        "--execute", action="store_true", help="Mark it not extracted and queue the job"
    )
    args = parser.parse_args()
    return asyncio.run(run(args.org, args.artifact, args.execute))


if __name__ == "__main__":
    sys.exit(main())
