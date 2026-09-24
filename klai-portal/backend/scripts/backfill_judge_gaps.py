"""File the conversation judge's past knowledge-failure verdicts as gap rows, once.

The webchat judge only started filing gap rows on 23 September and the
LibreChat judge not at all, so the verdicts from before that never reached the
knowledge inbox. This walks one organisation's verdicts of the last ``--days``
days, oldest first, and files each through the same ``file_judge_gap`` the two
judge passes use, so the duplicate check, audience and evidence are identical.
Each row's grouping fold is awaited before the next row is filed, so every
verdict is compared against the groups the earlier ones formed.

The question is the one the live passes file: for the webchat the question the
visit started with (the stored ``first_user_query``, which outlives the message
retention), dated when the visit started; for LibreChat the employee's last
question in the tenant's MongoDB, dated when it was asked, since a thread can
run for weeks. Not today's date, so the inbox's 30-day window counts it right.

It refuses an organisation whose telemetry level changed within the window: the
level decides whether a question may be stored at all, and the level that
applied when a question was asked is what counts, not today's.

Usage (inside the portal-api container). Run it as a module: invoked as a file,
scripts/ sits first on sys.path and the ``app`` package cannot be imported.
    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \\
        python -m scripts.backfill_judge_gaps --org-slug <slug> --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime


async def _settle_background_tasks() -> None:
    """Wait for the grouping fold record_gap_event starts for each new row."""
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def amain(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    from app.core.config import settings
    from app.core.database import cross_org_session, tenant_scoped_session
    from app.core.provisioning_names import provisioning_names_for_slug
    from app.services.conversation_judge import file_judge_gap
    from app.services.librechat_quality_judge import _sync_fetch_messages, _turns_from_messages, last_user_question

    async with cross_org_session() as db:
        org = (
            await db.execute(
                text("SELECT id, telemetry_level FROM portal_orgs WHERE slug = :slug AND deleted_at IS NULL"),
                {"slug": args.org_slug},
            )
        ).first()
    if org is None:
        print(f"org not found: {args.org_slug}", file=sys.stderr)
        return 1
    if org.telemetry_level != "full":
        print(f"org {args.org_slug} is not on full telemetry; its questions are not stored", file=sys.stderr)
        return 1
    async with tenant_scoped_session(org.id) as db:
        level_changed = (
            await db.execute(
                text(
                    "SELECT 1 FROM portal_audit_log WHERE org_id = :org_id AND action = 'telemetry_level_changed' "
                    "AND created_at > now() - make_interval(days => :days) LIMIT 1"
                ),
                {"org_id": org.id, "days": args.days},
            )
        ).first()
    if level_changed is not None:
        print(f"telemetry level of {args.org_slug} changed within {args.days} days; shorten --days", file=sys.stderr)
        return 1

    async with tenant_scoped_session(org.id) as db:
        verdicts = (
            await db.execute(
                text(
                    """
                    SELECT j.channel, j.conversation_id, j.external_conversation_id,
                           j.outcome, j.failure_category, j.confidence, wc.first_user_query, wc.started_at
                      FROM conversation_quality_judgments j
                      LEFT JOIN widget_conversations wc ON wc.id = j.conversation_id
                     WHERE j.org_id = :org_id
                       AND j.judged_at > now() - make_interval(days => :days)
                       AND j.outcome IN ('unresolved', 'partially_resolved', 'escalated')
                       AND j.failure_category IN ('retrieval_miss', 'retrieval_wrong')
                     ORDER BY j.judged_at, j.id
                    """
                ),
                {"org_id": org.id, "days": args.days},
            )
        ).all()

    librechat_ids = [v.external_conversation_id for v in verdicts if v.channel == "librechat"]
    librechat_question: dict[str, tuple[str, datetime | None]] = {}
    if librechat_ids:
        db_name = provisioning_names_for_slug(args.org_slug, domain=settings.domain).mongodb_database
        messages = await asyncio.to_thread(_sync_fetch_messages, db_name, librechat_ids)
        for cid, docs in messages.items():
            question = last_user_question(_turns_from_messages(docs))
            last = next((d for d in reversed(docs) if d.get("isCreatedByUser") and _turns_from_messages([d])), None)
            if question is not None and last is not None:
                # pymongo returns naive UTC datetimes.
                asked = last.get("createdAt")
                asked = asked.replace(tzinfo=UTC) if asked is not None and asked.tzinfo is None else asked
                librechat_question[cid] = (question, asked)

    # "skipped": file_judge_gap declined, i.e. a row the inbox shows already
    # covers the conversation, or the widget conversation is marked test/preview.
    counts = {"verdicts": len(verdicts), "filed": 0, "skipped": 0, "no_question": 0, "failed": 0}
    for v in verdicts:
        verdict = {"outcome": v.outcome, "failure_category": v.failure_category, "confidence": v.confidence}
        if v.channel == "webchat":
            question, asked = v.first_user_query, v.started_at
            target = {"audience": "customer", "conversation_id": v.conversation_id}
        else:
            question, asked = librechat_question.get(v.external_conversation_id, (None, None))
            target = {"audience": "internal", "librechat_conversation_id": v.external_conversation_id}
        if not question:
            counts["no_question"] += 1
            continue
        try:
            async with tenant_scoped_session(org.id) as db:
                filed = await file_judge_gap(
                    db, org_id=org.id, question=question, verdict=verdict, occurred_at=asked, **target
                )
            await _settle_background_tasks()
        except Exception as exc:
            # Keep going: one failed row must not stop the run; a rerun files it,
            # since the duplicate check skips everything already filed.
            counts["failed"] += 1
            print(f"filing failed: {type(exc).__name__}", file=sys.stderr)
            continue
        counts["filed" if filed else "skipped"] += 1

    # Counts only: the questions are customer and employee text.
    print(json.dumps(counts))
    return 1 if counts["failed"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--days", type=int, default=30)
    return asyncio.run(amain(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
