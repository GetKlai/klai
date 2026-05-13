#!/usr/bin/env python3
"""SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-10 -- merge-gate validation script.

Read-only scan of a tenant's KB. Reports clusters discovered under v2 logic
plus v1-purged pages that would be recovered. Required output: zero surprise
classifications on voys + getklai before v2 v1→v2 migration ships to prod.

Usage::

    python scripts/validate_login_wall_detector.py --org voys --kb support
    python scripts/validate_login_wall_detector.py --org getklai --kb voys-test --json

The default human-readable summary writes to stdout. ``--json`` emits the
structured report for piping into ``jq`` or ops dashboards.

The script connects via the standard tenant-scoped connection helper
(``knowledge_ingest.db.tenant_scoped_connection``) so RLS applies. Set
``DATABASE_URL`` / ``POSTGRES_DSN`` in the environment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Allow running from a checkout without an editable install: prepend the
# klai-knowledge-ingest package root onto sys.path. Inside the production
# container the package is installed normally and this branch is a no-op.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_INGEST_ROOT = _REPO_ROOT / "klai-knowledge-ingest"
if _INGEST_ROOT.exists() and str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))

from knowledge_ingest.backfill_tasks import _resolve_org_slug_to_zitadel_id  # noqa: E402
from knowledge_ingest.validation import validate_login_wall_detector  # noqa: E402


async def _resolve_org(args: argparse.Namespace) -> str:
    if args.org_id:
        return args.org_id
    return await _resolve_org_slug_to_zitadel_id(args.org)


def _print_human(report: dict) -> None:
    print(f"Tenant: org={report['org_id']} kb={report['kb_slug']}")
    print(f"Total pages: {report['total_pages']}")
    print(f"Wall clusters: {len(report['clusters'])}")
    for cluster in report["clusters"]:
        print(f"  cluster_size: {cluster['size']}")
        print("  sample_urls:")
        for url in cluster["sample_urls"]:
            print(f"    - {url}")
    print(
        f"v1-purged but no longer clustering: {len(report['recovery_candidates'])}"
    )
    for url in report["recovery_candidates"]:
        print(f"  - {url}")


async def _async_main(args: argparse.Namespace) -> int:
    org_id = await _resolve_org(args)
    report = await validate_login_wall_detector(
        org_id=org_id,
        kb_slug=args.kb,
        cluster_min=args.cluster_min,
        hamming_max=args.hamming_max,
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_human(report)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="validate_login_wall_detector",
        description=(
            "Read-only scan of a tenant KB. Reports v2 cluster "
            "classifications + recovery candidates."
        ),
    )
    p.add_argument(
        "--org",
        help="Tenant slug (resolved to zitadel_org_id via portal_orgs).",
    )
    p.add_argument(
        "--org-id",
        help="Bypass slug resolution and pass zitadel_org_id directly.",
    )
    p.add_argument("--kb", required=True, help="KB slug.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    p.add_argument(
        "--cluster-min",
        type=int,
        default=5,
        help="Cluster threshold (default 5; matches REQ-02).",
    )
    p.add_argument(
        "--hamming-max",
        type=int,
        default=3,
        help="Hamming distance threshold (default 3; matches REQ-02).",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    if not args.org and not args.org_id:
        print(
            "Either --org SLUG or --org-id ZITADEL_ID is required",
            file=sys.stderr,
        )
        return 2
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
