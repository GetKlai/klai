"""The knowledge gaps of one organisation over the last days, from the signals its chat turns stored.

Clusters the turns that ended in a refusal, a clarifying question, a request
for a person or a partial answer, and the answers on a weak search, per topic
(app/services/knowledge_gap_report.py). No model call, read only.

Run on the server:

    docker exec -w /repo/klai-portal/backend klai-core-portal-api-1 \
        python scripts/knowledge_gap_report.py --org <slug> [--days 7] [--json] [--include-decided]

Reads tenant-scoped: the org is looked up once across tenants (its id is
needed before a scope can be set), every other read runs in that org's scope.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    # "python scripts/knowledge_gap_report.py" puts scripts/ on the path, not
    # the backend root; same bootstrap as grounding_report.py.
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import select  # noqa: E402

from app.core.database import cross_org_session, tenant_scoped_session  # noqa: E402
from app.models.portal import PortalOrg  # noqa: E402
from app.services.knowledge_gap_report import knowledge_gap_clusters  # noqa: E402

_KIND_LABELS = {
    "no_article": "geen artikel",
    "article_misses_part": "artikel mist een deel",
    "variant_unclear": "variant onduidelijk",
    "contact_wish": "contactwens",
    "weak_match": "antwoord op zwakke zoektreffer",
}


def _cell(value: str | None, width: int) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(clusters: list[dict]) -> None:
    if not clusters:
        print("Geen kennisgaten in deze periode.")
        return
    print(f"{'aantal':>6}  {'soort':<30}  {'product':<16}  {'vraag':<60}  {'artikel':<40}  open delen")
    for c in clusters:
        article = (c["article"]["title"] or c["article"]["url"]) if c["article"] else "geen artikel"
        print(
            f"{c['count']:>6}  {_cell(_KIND_LABELS[c['kind']], 30):<30}  {_cell(c['product'], 16):<16}  "
            f"{_cell(c['question'] or ('(intern, geen tekst)' if c['internal_count'] else ''), 60):<60}  "
            f"{_cell(article, 40):<40}  {_cell('; '.join(c['open_parts']), 80)}"
        )


async def main(args: argparse.Namespace) -> int:
    async with cross_org_session() as session:
        org = (
            await session.execute(
                select(PortalOrg.id).where(PortalOrg.slug == args.org, PortalOrg.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
    if org is None:
        print(f"No active org with slug {args.org}.", file=sys.stderr)
        return 1
    async with tenant_scoped_session(org) as db:
        clusters = await knowledge_gap_clusters(db, org_id=org, days=args.days, include_decided=args.include_decided)
    if args.json:
        print(json.dumps({"org": args.org, "days": args.days, "clusters": clusters}, ensure_ascii=False, indent=2))
    else:
        print_table(clusters)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The knowledge gaps of one organisation, from stored chat signals.")
    parser.add_argument("--org", required=True, help="org slug")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--include-decided", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args())))
