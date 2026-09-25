#!/usr/bin/env python3
"""Fail when a VictoriaLogs-backed alert query cannot feed Grafana SSE.

The victoriametrics-logs-datasource plugin answers queryType `instant` with a
log-row frame (Time, Line, labels, ...). Grafana's reduce/math expressions
reject that frame with "input data must be a wide series but got type long",
so the rule never evaluates. Only queryType `stats` with a `| stats` pipe
returns the numeric Time/Value frame SSE accepts.

On 2026-09-25, 40 of the 43 VictoriaLogs rules had the instant shape: 12 showed
as firing through execErrState Alerting, the other 28 sat in a masked error
state (execErrState OK) and could never fire.

usage: audit-alert-logsql-shape.py [DIR]
"""

import sys
from pathlib import Path

import yaml

root = Path(sys.argv[1] if len(sys.argv) > 1 else "deploy/grafana/provisioning/alerting")
errors = 0
for path in sorted(root.rglob("*.y*ml")):
    for group in (yaml.safe_load(path.read_text()) or {}).get("groups") or []:
        for rule in group.get("rules") or []:
            for query in rule.get("data") or []:
                if query.get("datasourceUid") != "victorialogs":
                    continue
                model = query.get("model") or {}
                if model.get("queryType") != "stats" or "| stats" not in model.get("expr", ""):
                    print(f"FAIL: {path}: {rule.get('uid')} refId {query.get('refId')} needs "
                          f"queryType: stats and a '| stats' pipe (got {model.get('queryType')!r})")
                    errors += 1

if errors:
    sys.exit(1)
print("OK: every VictoriaLogs alert query uses the stats shape.")
