#!/usr/bin/env python3
"""Fail when a critical alert rule has no explicit notification route.

A rule that matches no route in policies.yaml falls to the default receiver,
a shared mailbox nobody triages. In October 2026 the critical
mistral_api_probe_failed alert went there and sat unread for 2.5 days while a
Mistral workspace was out of budget.

A rule counts as routed when its labels satisfy every matcher of some
top-level route; a nested route can only narrow its parent, so the top level
decides. Only equality matchers
are understood; a route with any other operator, or with no matchers at all,
is not counted, so this check can only err towards failing.

usage: audit-alert-routes.py [DIR]
"""

import sys
from pathlib import Path

import yaml


def _matchers(route):
    pairs = []
    for raw in route.get("matchers") or []:
        key, op, value = raw.partition("=")
        if op != "=" or key.endswith(("!", "=")) or value.startswith(("~", "=")):
            return None
        pairs.append((key.strip(), value.strip().strip("\"'")))
    for key, op, value in route.get("object_matchers") or []:
        if op != "=":
            return None
        pairs.append((key, value))
    return pairs or None


def _routed(labels, routes):
    for route in routes:
        pairs = _matchers(route)
        if pairs is None or any(labels.get(k) != v for k, v in pairs):
            continue
        return True
    return False


root = Path(sys.argv[1] if len(sys.argv) > 1 else "deploy/grafana/provisioning/alerting")
routes = []
for policy in (yaml.safe_load((root / "policies.yaml").read_text()) or {}).get("policies") or []:
    routes.extend(policy.get("routes") or [])

errors = 0
for path in sorted(root.rglob("*.y*ml")):
    for group in (yaml.safe_load(path.read_text()) or {}).get("groups") or []:
        for rule in group.get("rules") or []:
            labels = {k: str(v) for k, v in (rule.get("labels") or {}).items()}
            if labels.get("severity") == "critical" and not _routed(labels, routes):
                print(f"FAIL: {path}: {rule.get('uid')} is severity critical but matches no route in policies.yaml")
                errors += 1

if errors:
    sys.exit(1)
print("OK: every critical alert rule has an explicit route.")
