#!/usr/bin/env python3
"""Create a Klai product update through the platform-admin API.

Authoring contract:
1. Write English title/body from the finished work.
2. Apply `.claude/rules/gtm/klai-brand-voice.md`.
3. Apply `.claude/rules/gtm/klai-humanizer.md`.
4. Run this script to publish the validated title/body and related commits.

Example:
    python scripts/create_product_update.py \
      --api-url https://my.getklai.com \
      --token "$KLAI_ADMIN_TOKEN" \
      --title "Feedback updates are easier to follow" \
      --body "You can now see what happened with a report directly from your account menu." \
      --from-git origin/main..HEAD
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

import httpx


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default=os.environ.get("KLAI_PORTAL_API_URL", "http://localhost:8010"))
    parser.add_argument("--token", default=os.environ.get("KLAI_ADMIN_TOKEN"))
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--commit", dest="commits", action="append", default=[])
    parser.add_argument("--from-git", help="Git rev range to collect commit SHAs from, e.g. origin/main..HEAD")
    args = parser.parse_args()

    if not args.token:
        parser.error("--token or KLAI_ADMIN_TOKEN is required")

    commit_shas = list(args.commits)
    if args.from_git:
        commit_shas.extend(_commits_from_git(args.from_git))

    payload = {
        "title": args.title,
        "body": args.body,
        "commit_shas": commit_shas,
    }
    url = args.api_url.rstrip("/") + "/api/admin/platform/product-updates"
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {args.token}"},
        json=payload,
        timeout=15.0,
    )
    if response.status_code >= 400:
        sys.stderr.write(f"Product update create failed ({response.status_code}): {response.text}\n")
        return 1
    sys.stdout.write(json.dumps(response.json(), indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
