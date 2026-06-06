#!/usr/bin/env python3
"""Create Klai product updates from a trusted operator environment.

Authoring contract:
1. Write English title/body from the finished work.
2. Apply `.claude/rules/gtm/klai-brand-voice.md`.
3. Apply `.claude/rules/gtm/klai-humanizer.md`.
4. Run this script only from trusted infra or an equivalent operator shell with
   production backend database access. Do not expose product update publishing
   as a portal/API endpoint and do not ship content updates as migrations.

This script writes through the backend ORM/service layer and uses
`cross_org_session()` because product updates are global announcements, not
tenant-owned rows. Infra access is the admin boundary.

Single update:
    python scripts/create_product_update.py \\
      --title "Feedback updates are easier to follow" \\
      --body "You can now see what happened with a report directly from your account menu." \\
      --created-at 2026-06-06T12:00:00Z \\
      --commit abc1234

Batch:
    python scripts/create_product_update.py \\
      --json product-updates.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import cross_org_session  # noqa: E402
from app.product_updates.schemas import product_update_out  # noqa: E402
from app.product_updates.service import ProductUpdateValidationError, create_product_update  # noqa: E402


def _commits_from_git(rev_range: str) -> list[str]:
    git_bin = shutil.which("git")
    if git_bin is None:
        raise RuntimeError("git is not available on PATH")
    result = subprocess.run(  # noqa: S603 - rev_range is an operator-supplied git revision, not shell-expanded.
        [git_bin, "log", "--format=%H", rev_range],
        check=True,
        text=True,
        capture_output=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _normalize_update(raw: dict[str, Any]) -> dict[str, Any]:
    commit_shas = raw.get("commit_shas", raw.get("commits", []))
    if commit_shas is None:
        commit_shas = []
    if not isinstance(commit_shas, list):
        raise TypeError("commit_shas must be a list")
    payload = {
        "title": raw["title"],
        "body": raw["body"],
        "commit_shas": commit_shas,
    }
    if raw.get("created_at"):
        payload["created_at"] = raw["created_at"]
    if raw.get("dedupe_key"):
        payload["dedupe_key"] = raw["dedupe_key"]
    return payload


def _updates_from_json(path: str) -> list[dict[str, Any]]:
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(loaded, dict) and "updates" in loaded:
        loaded = loaded["updates"]
    if not isinstance(loaded, list):
        raise TypeError("JSON input must be a list or an object with an 'updates' list")
    return [_normalize_update(item) for item in loaded]


def _single_update_payload(args: argparse.Namespace) -> dict[str, Any]:
    if not args.title or not args.body:
        raise RuntimeError("--title and --body are required unless --json is used")
    commit_shas = list(args.commits)
    if args.from_git:
        commit_shas.extend(_commits_from_git(args.from_git))
    payload: dict[str, Any] = {
        "title": args.title,
        "body": args.body,
        "commit_shas": commit_shas,
    }
    if args.created_at:
        payload["created_at"] = args.created_at
    if args.dedupe_key:
        payload["dedupe_key"] = args.dedupe_key
    return payload


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


async def _publish_updates(updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    async with cross_org_session() as db:
        for payload in updates:
            update = await create_product_update(
                db,
                title=payload["title"],
                body=payload["body"],
                commit_shas=payload.get("commit_shas", []),
                created_at=_parse_created_at(payload.get("created_at")),
                dedupe_key=payload.get("dedupe_key"),
                published_via="operator_script",
            )
            results.append(product_update_out(update, read_at=None).model_dump(mode="json"))
        await db.commit()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title")
    parser.add_argument("--body")
    parser.add_argument("--created-at", help="Release/display timestamp, e.g. 2026-06-06T12:00:00Z")
    parser.add_argument("--dedupe-key", help="Stable idempotency key. Generated by the service when omitted.")
    parser.add_argument("--commit", dest="commits", action="append", default=[])
    parser.add_argument("--from-git", help="Git rev range to collect commit SHAs from, e.g. origin/main..HEAD")
    parser.add_argument("--json", help="Publish a batch from a JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Print the payload(s) without publishing")
    args = parser.parse_args()

    try:
        updates = _updates_from_json(args.json) if args.json else [_single_update_payload(args)]
        if args.dry_run:
            sys.stdout.write(json.dumps(updates, indent=2, ensure_ascii=False) + "\n")
            return 0
        results = asyncio.run(_publish_updates(updates))
    except (KeyError, RuntimeError, TypeError, ValueError, ProductUpdateValidationError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(json.dumps(results[0] if len(results) == 1 else results, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
