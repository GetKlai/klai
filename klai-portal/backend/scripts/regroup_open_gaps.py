"""Fold one organisation's existing open gap groups by need, once.

Rows written before the grouping judge ran on the write path only carry the
literal-text key from the post-deploy backfill, so paraphrases of one question
still sit in separate groups: on the first tenant measured, nearly every open
group held a single conversation. This runs every open group through the same fold the
write path uses (``gap_events.fold_into_open_group``), oldest group first.

Only 'full' telemetry, only rows with a knowledge base (candidates are scoped
per KB) and never redacted rows, the same limits as the write path. Before any
change it writes every open row's (id, question_key) to ``--snapshot``, a new
owner-only file, so a fold can be undone by hand. A fold only moves open rows,
so that covers everything the run changes. The file holds normalized customer
questions: keep it on the server or somewhere private, never in this repository.

Usage (inside the portal-api container, which has the database and LiteLLM):
    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \\
        python scripts/regroup_open_gaps.py --org-slug <slug> --snapshot /tmp/regroup-<slug>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def _group_sizes(org_id: int) -> dict[str, int]:
    from sqlalchemy import Text, cast, func, select

    from app.core.database import tenant_scoped_session
    from app.models.retrieval_gaps import PortalRetrievalGap

    # One support case or conversation counts once, however many rows it wrote.
    occurrence = func.coalesce(
        "c" + cast(PortalRetrievalGap.conversation_id, Text),
        "s" + cast(PortalRetrievalGap.support_case_id, Text),
        "r" + cast(PortalRetrievalGap.id, Text),
    )
    async with tenant_scoped_session(org_id) as session:
        rows = (
            await session.execute(
                select(func.count(func.distinct(occurrence)))
                .where(PortalRetrievalGap.org_id == org_id, PortalRetrievalGap.resolved_at.is_(None))
                .group_by(PortalRetrievalGap.question_key)
            )
        ).scalars()
        sizes = list(rows)
    return {
        "open_groups": len(sizes),
        "groups_2_plus": sum(1 for n in sizes if n >= 2),
        "groups_3_plus": sum(1 for n in sizes if n >= 3),
    }


async def amain(args: argparse.Namespace) -> int:
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionLocal, tenant_scoped_session
    from app.models.portal import PortalOrg
    from app.models.retrieval_gaps import PortalRetrievalGap
    from app.services.gap_events import fold_into_open_group

    async with AsyncSessionLocal() as db:
        org = (await db.execute(select(PortalOrg).where(PortalOrg.slug == args.org_slug))).scalar_one_or_none()
    if org is None:
        print(f"org not found: {args.org_slug}", file=sys.stderr)
        return 1
    if org.telemetry_level != "full":
        print(f"org {args.org_slug} is not on full telemetry; nothing to compare", file=sys.stderr)
        return 1

    async with tenant_scoped_session(org.id) as session:
        open_rows = (
            await session.execute(
                select(PortalRetrievalGap.id, PortalRetrievalGap.question_key).where(
                    PortalRetrievalGap.org_id == org.id, PortalRetrievalGap.resolved_at.is_(None)
                )
            )
        ).all()
        # The oldest row speaks for its group; groups are folded oldest first.
        first_ids = (
            select(func.min(PortalRetrievalGap.id))
            .where(
                PortalRetrievalGap.org_id == org.id,
                PortalRetrievalGap.resolved_at.is_(None),
                PortalRetrievalGap.nearest_kb_slug.isnot(None),
                PortalRetrievalGap.question_key.isnot(None),
                PortalRetrievalGap.query_text.not_like("[REDACTED:%"),
            )
            .group_by(PortalRetrievalGap.question_key)
        )
        groups = (
            await session.execute(
                select(
                    PortalRetrievalGap.question_key,
                    PortalRetrievalGap.query_text,
                    PortalRetrievalGap.language,
                    PortalRetrievalGap.audience,
                    PortalRetrievalGap.nearest_kb_slug,
                )
                .where(PortalRetrievalGap.id.in_(first_ids))
                .order_by(PortalRetrievalGap.id)
            )
        ).all()

    # Owner-only: the keys are normalized customer questions.
    with os.fdopen(os.open(args.snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as fh:
        json.dump([{"id": r.id, "question_key": r.question_key} for r in open_rows], fh)

    before = await _group_sizes(org.id)
    folded = failed = 0
    for group in groups:
        try:
            matched = await fold_into_open_group(
                org_id=org.id,
                kb_slug=group.nearest_kb_slug,
                question=group.query_text,
                language=group.language,
                audience=group.audience,
                base_key=group.question_key,
            )
        except Exception as exc:
            # Keep going: one malformed judge answer must not stop the run; the
            # group simply stays where it was.
            failed += 1
            print(f"fold failed: {type(exc).__name__}", file=sys.stderr)
            continue
        folded += matched is not None
    after = await _group_sizes(org.id)

    # Counts only: every key carries a customer question.
    print(
        json.dumps({"groups_tried": len(groups), "folded": folded, "failed": failed, "before": before, "after": after})
    )
    # A partial run is not a finished one: the unfolded groups need a rerun.
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--snapshot", required=True, help="where to write (id, question_key) of every open row first")
    return asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
