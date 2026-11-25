#!/usr/bin/env python3
"""Fail the required `quality` check when a changed Dockerfile was not scanned,
or a changed image pin was not triaged (image-pin-triage.yml).

Inputs (environment):
  CHANGED_DOCKERFILES  JSON list of Dockerfiles changed by the pull request
                       (paths-filter `dockerfiles_files`; empty means none).
  IMAGE_PINS_SELECTED  "true" when paths-filter selected `image_pins`.
  NEEDS_JSON           toJSON(needs) of the `quality` job.

Each service image has a job in quality.yml that calls the service workflow.
That workflow exports `scanned: "true"` only when its Trivy `scan` job ran, so a
job that finished `success` with the scan skipped inside it still reads as
not scanned. On 2026-11-23 two scans were skipped on a pull request, the
aggregate counted `skipped` as passed, and both scans failed on main.

A changed Dockerfile passes only when its job finished `success` AND exported
`scanned == "true"`. A Dockerfile that is neither mapped nor exempt fails, so a
new image cannot be added without deciding how it is scanned.

Run the tests: python3 -m unittest scripts/tests/test_require_image_scans.py
"""

from __future__ import annotations

import json
import os
import sys

# Dockerfile -> job name in .github/workflows/quality.yml
SCAN_JOBS = {
    "deploy/bge-m3-sparse/Dockerfile": "bge-m3-sparse",
    "deploy/caddy/Dockerfile": "caddy",
    "klai-connector/Dockerfile": "connector",
    "klai-docker-authz/Dockerfile": "docker-authz",
    "klai-docs/Dockerfile": "docs",
    "klai-knowledge-ingest/Dockerfile": "knowledge-ingest",
    "klai-knowledge-mcp/Dockerfile": "knowledge-mcp",
    "klai-mailer/Dockerfile": "mailer",
    "klai-portal/backend/Dockerfile": "portal-api",
    "klai-retrieval-api/Dockerfile": "retrieval-api",
    "klai-scribe/scribe-api/Dockerfile": "scribe-api",
    "klai-scribe/whisper-server/Dockerfile": "whisper-server",
    "klai-slack/Dockerfile": "slack",
}

# Images derived from an upstream image and never deployed from a CI-built
# sha tag: they are built by their own pull-request workflows (not via
# quality.yml) and are scanned by digest in scan-pinned-images.yml (WARN tier),
# or are built on the server (crawl4ai). Listed here so the choice is explicit
# and visible in a pull request; moving one to SCAN_JOBS is the upgrade path.
UNSCANNED_BY_POLICY = {
    "deploy/crawl4ai/Dockerfile": "built on the server from an upstream image; no CI scan",
    "deploy/librechat/Dockerfile.klai": "upstream-derived, scanned by digest in scan-pinned-images.yml (WARN tier)",
    "deploy/presidio/analyzer/Dockerfile": "upstream-derived, scanned by digest in scan-pinned-images.yml (WARN tier)",
}


def verdict(changed: list[str], needs: dict) -> list[str]:
    """Return the error messages; an empty list means the check passes."""
    errors = []
    for path in sorted(changed):
        if path in UNSCANNED_BY_POLICY:
            print(f"::notice::{path} is not scanned in this check: {UNSCANNED_BY_POLICY[path]}")
            continue
        job = SCAN_JOBS.get(path)
        if job is None:
            errors.append(
                f"{path} changed but maps to no scan job: add it to SCAN_JOBS in "
                "scripts/require-image-scans.py (and a job in quality.yml), or to "
                "UNSCANNED_BY_POLICY with the reason"
            )
            continue
        data = needs.get(job)
        if data is None:
            errors.append(f"{path} changed but quality.yml has no `{job}` job in its needs")
        elif data.get("result") != "success":
            errors.append(f"{path} changed but the `{job}` job finished {data.get('result')}, so its image scan did not run to success")
        elif data.get("outputs", {}).get("scanned") != "true":
            errors.append(f"{path} changed and the `{job}` job passed without running the Trivy scan")
    return errors


def pin_triage_verdict(selected: bool, needs: dict) -> list[str]:
    """A pull request that changes an image pin must have run image-pin-triage.yml.

    Same reasoning as for Dockerfiles: `skipped` passes the aggregate, so a
    selected triage that did not export `checked == "true"` must fail here.
    """
    if not selected:
        return []
    data = needs.get("image-pin-triage") or {}
    if data.get("result") != "success" or data.get("outputs", {}).get("checked") != "true":
        return [
            f"an image pin or its triage inputs changed but the `image-pin-triage` job finished "
            f"{data.get('result')} without checking the changed pins"
        ]
    return []


def main() -> int:
    raw = os.environ.get("CHANGED_DOCKERFILES", "").strip()
    # An empty value means paths-filter listed nothing. If the `changes` job
    # itself failed, the aggregate step in quality.yml is already red.
    changed = json.loads(raw) if raw else []
    needs = json.loads(os.environ["NEEDS_JSON"])
    errors = verdict(changed, needs)
    errors += pin_triage_verdict(os.environ.get("IMAGE_PINS_SELECTED") == "true", needs)
    for message in errors:
        print(f"::error::{message}")
    if not changed:
        print("No Dockerfile changed: no image scan required.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
