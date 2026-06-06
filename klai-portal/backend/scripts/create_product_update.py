#!/usr/bin/env python3
"""Create Klai product updates through the platform-admin API.

Authoring contract:
1. Write English title/body from the finished work.
2. Apply `.claude/rules/gtm/klai-brand-voice.md`.
3. Apply `.claude/rules/gtm/klai-humanizer.md`.
4. Publish through the platform-admin API. Do not write product updates directly
   to the database and do not ship content updates as migrations.

The backend endpoint enforces `require_platform_admin()`. This script may use a
platform-admin bearer token or an existing platform-admin browser session cookie;
both still go through the same admin gate.

Single update:
    python scripts/create_product_update.py \
      --api-url https://my.getklai.com \
      --cookie "$KLAI_ADMIN_COOKIE" \
      --title "Feedback updates are easier to follow" \
      --body "You can now see what happened with a report directly from your account menu." \
      --created-at 2026-06-06T12:00:00Z \
      --commit abc1234

Batch:
    python scripts/create_product_update.py \
      --api-url https://my.getklai.com \
      --cookie "$KLAI_ADMIN_COOKIE" \
      --json product-updates.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def _load_cookie(args: argparse.Namespace) -> str | None:
    if args.cookie_file:
        return Path(args.cookie_file).read_text(encoding="utf-8").strip()
    return args.cookie


def _headers(args: argparse.Namespace) -> dict[str, str]:
    headers: dict[str, str] = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    cookie = _load_cookie(args)
    if cookie:
        headers["Cookie"] = cookie
    if not headers:
        raise RuntimeError(
            "A platform-admin auth context is required. Set --token, --cookie, --cookie-file, "
            "KLAI_ADMIN_TOKEN or KLAI_ADMIN_COOKIE."
        )
    return headers


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


def _publish_updates(args: argparse.Namespace, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    url = args.api_url.rstrip("/") + "/api/admin/platform/product-updates"
    headers = _headers(args)
    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=15.0) as client:
        for payload in updates:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                title = payload.get("title", "<untitled>")
                raise RuntimeError(
                    f"Product update create failed for {title!r} ({response.status_code}): {response.text}"
                )
            results.append(response.json())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-url", default=os.environ.get("KLAI_PORTAL_API_URL", "http://localhost:8010"))
    parser.add_argument("--token", default=os.environ.get("KLAI_ADMIN_TOKEN"))
    parser.add_argument("--cookie", default=os.environ.get("KLAI_ADMIN_COOKIE"))
    parser.add_argument("--cookie-file", help="File containing a raw Cookie header for a platform-admin session")
    parser.add_argument("--title")
    parser.add_argument("--body")
    parser.add_argument("--created-at", help="Release/display timestamp, e.g. 2026-06-06T12:00:00Z")
    parser.add_argument("--dedupe-key", help="Stable idempotency key. Generated by the backend when omitted.")
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
        results = _publish_updates(args, updates)
    except (KeyError, RuntimeError, ValueError, httpx.HTTPError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(json.dumps(results[0] if len(results) == 1 else results, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
