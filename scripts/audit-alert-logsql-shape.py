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

import re
import sys
from pathlib import Path

import yaml

# Both VictoriaLogs datasources registered in deploy/grafana/provisioning.
_VL_UIDS = {"victorialogs", "victorialogs-content"}
# A real pipe, not text inside a quoted phrase such as _msg:"| stats".
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_STATS_PIPE = re.compile(r"\|\s*stats\b")

root = Path(sys.argv[1] if len(sys.argv) > 1 else "deploy/grafana/provisioning/alerting")
errors = 0
for path in sorted(root.rglob("*.y*ml")):
    for group in (yaml.safe_load(path.read_text()) or {}).get("groups") or []:
        for rule in group.get("rules") or []:
            data = rule.get("data") or []
            vl_refs = set()
            for query in data:
                if query.get("datasourceUid") not in _VL_UIDS:
                    continue
                vl_refs.add(query.get("refId"))
                model = query.get("model") or {}
                expr = _QUOTED.sub('""', model.get("expr", ""))
                if model.get("queryType") != "stats" or not _STATS_PIPE.search(expr):
                    print(f"FAIL: {path}: {rule.get('uid')} refId {query.get('refId')} needs "
                          f"queryType: stats and a '| stats' pipe (got {model.get('queryType')!r})")
                    errors += 1
            # A stats query returns one point per window; reducer `count` would
            # always yield 1 and silently disable any threshold above 1.
            for query in data:
                model = query.get("model") or {}
                if model.get("type") == "reduce" and model.get("expression") in vl_refs and model.get("reducer") == "count":
                    print(f"FAIL: {path}: {rule.get('uid')} refId {query.get('refId')} reduces a stats "
                          f"query with 'count'; use 'last' (the query already returns the count)")
                    errors += 1

if errors:
    sys.exit(1)
print("OK: every VictoriaLogs alert query uses the stats shape.")
