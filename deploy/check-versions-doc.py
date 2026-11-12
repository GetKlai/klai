#!/usr/bin/env python3
"""Fail when deploy/VERSIONS.md states what the compose files already say.

The compose files are the only place an image version lives. Renovate bumps
them and never touches this document, so a version written here is stale at
the next bump (on 2026-11-12 about a dozen rows had drifted). The document
keeps what compose cannot say: why an image is held back, what to check
before a bump, what Klai builds itself. This check enforces that split:

  1. every service named in the first column of a "Service" table exists in
     the compose file the section is about (docker-compose.dev.yml for a
     heading that names it, deploy/docker-compose*.yml otherwise);
  2. no backticked image reference carries a tag or digest (`repo:1.2.3`,
     `repo@sha256:...`); say which service instead;
  3. no "pin sync" footer, which is a dated claim that everything agrees.

Exit 0 on a clean document, 1 on any violation, naming the line and the fix.

Usage: python3 deploy/check-versions-doc.py [ROOT]
ROOT overrides the repo root (the test points it at a synthetic tree).
"""
import re
import sys
from pathlib import Path

DEPLOY_COMPOSE = [
    "deploy/docker-compose.yml",
    "deploy/docker-compose.override.yml",
    "deploy/docker-compose.gpu.yml",
]
DEV_COMPOSE = ["docker-compose.dev.yml"]

SERVICE_KEY = re.compile(r"^  ([\w.-]+):\s*(?:#.*)?$")
IMAGE_WITH_VERSION = re.compile(
    r"^[a-z0-9][\w.-]*(?:/[\w.-]+)*(?::[\w][\w.+-]*|@sha256:[\w.…-]+)$"
)


def services(root, rels):
    found = set()
    for rel in rels:
        path = root / rel
        if not path.is_file():
            continue
        in_services = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\S", line):
                in_services = line.startswith("services:")
                continue
            match = SERVICE_KEY.match(line)
            if in_services and match:
                found.add(match.group(1))
    return found


def check(root):
    doc_rel = "deploy/VERSIONS.md"
    doc = root / doc_rel
    if not doc.is_file():
        print(f"FAIL: {doc_rel}: file missing", file=sys.stderr)
        return False

    known = {
        "dev": (DEV_COMPOSE, services(root, DEV_COMPOSE)),
        "deploy": (DEPLOY_COMPOSE, services(root, DEPLOY_COMPOSE)),
    }
    errors = []
    heading = ""
    in_service_table = False
    for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("#"):
            heading = line
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if cells:
            if cells[0] == "Service":
                in_service_table = True
            elif in_service_table and not set(cells[0]) <= {"-", ":", " "}:
                kind = "dev" if "docker-compose.dev.yml" in heading else "deploy"
                rels, names = known[kind]
                for name in re.findall(r"`([^`]+)`", cells[0]):
                    if name not in names:
                        errors.append(
                            f"{doc_rel}:{number}: service `{name}` is not in "
                            f"{' or '.join(rels)}. Rename it to the compose "
                            f"service name or delete the row."
                        )
        else:
            in_service_table = False
        for token in re.findall(r"`([^`\s]+)`", line):
            if IMAGE_WITH_VERSION.match(token):
                errors.append(
                    f"{doc_rel}:{number}: `{token}` states a version; the "
                    f"compose file is the only place a tag or digest lives. "
                    f"Name the service instead and keep the reason for the pin."
                )
        if re.search(r"pin sync", line, re.I):
            errors.append(
                f"{doc_rel}:{number}: a 'pin sync' footer is a dated claim that "
                f"compose and this file agree. Delete it; this check is the claim."
            )

    for error in errors:
        print("FAIL:", error, file=sys.stderr)
    if not errors:
        print(f"OK: {doc_rel} names only services that exist in compose and states no version")
    return not errors


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    sys.exit(0 if check(Path(args[0]).resolve() if args else Path(__file__).resolve().parents[1]) else 1)
