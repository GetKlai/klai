"""Daily drift check: Zitadel OIDC redirect_uris vs the validator allowlist.

The 2026-04-30 ``chat-getklai.getklai.com`` SSO outage was caused by a
Zitadel OIDC app whose redirect_uri pointed at a hostname that the
portal's ``_validate_callback_url`` allowlist did not accept. The
allowlist is hand-curated against a snapshot of the OIDC apps; if
someone adds a new Zitadel app between snapshots, the validator
silently rejects every login through that app.

This script flips the loop: it asks Zitadel for the live OIDC config
and compares against the local expectation. Drift = exit code 2 plus
a structured report. The accompanying workflow runs this nightly and
opens a GitHub issue on drift.

Inputs
------
ZITADEL_ADMIN_PAT       (required) — IAM_OWNER PAT
ZITADEL_BASE_URL        (default: https://auth.getklai.com)
ZITADEL_ORG_ID          (default: 362757920133283846 — klai org)
ZITADEL_PROJECT_ID      (default: 362771533686374406 — Klai Platform)
KLAI_DOMAIN             (default: getklai.com)
EXPECTED_STATIC_SUBDOMAINS  (comma-separated; default mirrors
                              ``_STATIC_SYSTEM_SUBDOMAINS`` in
                              klai-portal/backend/app/api/auth.py)

Exit codes
----------
0  no drift detected — every Zitadel-registered host classifies as
   static / tenant-shaped / chat-prefix-shaped per the spec
1  configuration error (missing env, Zitadel unreachable, malformed
   response)
2  drift detected — at least one host did not classify; see stderr
   for the actionable report

Output
------
On any outcome, a JSON report is written to ``stdout``:

    {
        "ok": <bool>,
        "expected_static": [...],
        "observed_first_labels": [...],
        "static_drift": {"missing": [...], "extra": [...]},
        "unclassified": [...]
    }
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

# Mirrors `_STATIC_SYSTEM_SUBDOMAINS` in klai-portal/backend/app/api/auth.py.
# Keep these two in sync; the contract test
# `test_static_system_subdomains_set_includes_known_oidc_apps` enforces
# the auth.py side at PR time, and this script enforces the live-Zitadel
# side at nightly schedule.
DEFAULT_EXPECTED_STATIC: frozenset[str] = frozenset({
    "chat",
    "chat-dev",
    "dev",
    "grafana",
    "errors",
    "auth",
})


def _env(name: str, default: str | None = None) -> str:
    """Read env var; raise on missing+no-default."""
    value = os.environ.get(name)
    if value is None:
        if default is None:
            print(f"FATAL: missing required env var {name}", file=sys.stderr)
            sys.exit(1)
        return default
    return value


def _fetch_oidc_apps(
    base_url: str,
    pat: str,
    org_id: str,
    project_id: str,
) -> list[dict]:
    """Call Zitadel management API to list OIDC apps under the project."""
    url = f"{base_url.rstrip('/')}/management/v1/projects/{project_id}/apps/_search"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
            "X-Zitadel-Orgid": org_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        print(f"FATAL: Zitadel API call failed: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"FATAL: Zitadel response not JSON: {exc}", file=sys.stderr)
        sys.exit(1)
    return payload.get("result", [])


def _extract_first_labels(apps: list[dict], domain: str) -> set[str]:
    """For every redirect_uri under *.{domain}, extract the first
    subdomain label. Hostnames not under {domain} are ignored (e.g.
    localhost, third-party redirects)."""
    suffix = f".{domain}"
    labels: set[str] = set()
    for app in apps:
        oidc = app.get("oidcConfig") or {}
        for uri in oidc.get("redirectUris", []) or []:
            host = (urllib.parse.urlparse(uri).hostname or "").lower()
            if host == domain:
                # Bare apex — not a "subdomain"; skip.
                continue
            if not host.endswith(suffix):
                # Non-Klai redirect (localhost, third-party).
                continue
            subdomain = host[: -len(suffix)]
            first_label = subdomain.split(".")[0]
            if first_label:
                labels.add(first_label)
    return labels


def _classify(
    label: str,
    expected_static: frozenset[str],
) -> str:
    """Return one of {"static", "tenant", "chat-prefix", "unknown"}.

    A label is "tenant-shaped" if it contains no dashes (single slug).
    A label is "chat-prefix-shaped" if it starts with ``chat-`` and the
    remainder is not empty. Note this script does NOT verify the
    chat-prefix slug exists in portal_orgs; that's a runtime concern and
    will fail the validator at request time, not here.
    """
    if label in expected_static:
        return "static"
    if "-" not in label:
        return "tenant"
    if label.startswith("chat-") and len(label) > len("chat-"):
        return "chat-prefix"
    return "unknown"


def main() -> int:
    pat = _env("ZITADEL_ADMIN_PAT")
    base_url = _env("ZITADEL_BASE_URL", "https://auth.getklai.com")
    org_id = _env("ZITADEL_ORG_ID", "362757920133283846")
    project_id = _env("ZITADEL_PROJECT_ID", "362771533686374406")
    domain = _env("KLAI_DOMAIN", "getklai.com")
    expected_env = os.environ.get("EXPECTED_STATIC_SUBDOMAINS", "")
    expected_static = (
        frozenset(s.strip() for s in expected_env.split(",") if s.strip())
        if expected_env
        else DEFAULT_EXPECTED_STATIC
    )

    apps = _fetch_oidc_apps(base_url, pat, org_id, project_id)
    observed = _extract_first_labels(apps, domain)

    # Static drift: any expected-static label missing from Zitadel,
    # OR any "static-shaped" (dash-containing) Zitadel label not in
    # expected_static.
    classified = {label: _classify(label, expected_static) for label in observed}
    unclassified = sorted(
        label for label, klass in classified.items() if klass == "unknown"
    )
    static_observed = {
        label for label, klass in classified.items() if klass == "static"
    }
    static_missing = sorted(expected_static - static_observed)
    static_extra = sorted(
        label
        for label in observed
        if (label not in expected_static)
        and (
            "-" in label
            and not (label.startswith("chat-") and len(label) > len("chat-"))
        )
    )

    has_drift = bool(unclassified) or bool(static_missing) or bool(static_extra)

    report = {
        "ok": not has_drift,
        "expected_static": sorted(expected_static),
        "observed_first_labels": sorted(observed),
        "classified": classified,
        "static_drift": {
            "missing": static_missing,
            "extra": static_extra,
        },
        "unclassified": unclassified,
    }
    print(json.dumps(report, indent=2))

    if has_drift:
        print("DRIFT DETECTED — see report on stdout", file=sys.stderr)
        if unclassified:
            print(
                f"  Unclassified hosts: {', '.join(unclassified)}",
                file=sys.stderr,
            )
            print(
                "  These appear in Zitadel as redirect_uri targets but are "
                "neither static (in EXPECTED) nor tenant-shaped (single label) "
                "nor chat-prefix-shaped. Either:",
                file=sys.stderr,
            )
            print(
                "    1. Add the host to `_STATIC_SYSTEM_SUBDOMAINS` in "
                "auth.py + update DEFAULT_EXPECTED_STATIC in this script",
                file=sys.stderr,
            )
            print(
                "    2. Remove the redirect_uri from Zitadel if it's stale",
                file=sys.stderr,
            )
            print(
                "    3. Extend the validator with a new prefix-strip rule "
                "if it represents a new per-tenant pattern",
                file=sys.stderr,
            )
        if static_missing:
            print(
                f"  Static labels in EXPECTED but missing from Zitadel: "
                f"{', '.join(static_missing)}",
                file=sys.stderr,
            )
            print(
                "  Either an OIDC app was deleted (then remove from EXPECTED) "
                "or PAT lacks read access to it.",
                file=sys.stderr,
            )
        if static_extra:
            print(
                f"  New static-shaped labels in Zitadel not in EXPECTED: "
                f"{', '.join(static_extra)}",
                file=sys.stderr,
            )
            print(
                "  Add to `_STATIC_SYSTEM_SUBDOMAINS` in auth.py + update "
                "DEFAULT_EXPECTED_STATIC in this script.",
                file=sys.stderr,
            )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
