"""A failed knowledge-base retrieval on an internal chat turn must page someone.

The LiteLLM hook's retrieval-failure alerts went with the hook on 2026-10-21.
The portal path logs the same failure as ``partner_chat_internal_retrieval_failed``
(app/services/retrieval_context.py); without a rule on that event a Strict
refusal or an Open answer without the knowledge base is silent again, as it was
for 7 days in the 2026-04-28 incident.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_BACKEND = Path(__file__).resolve().parents[1]
_ALERTING = _BACKEND.parents[1] / "deploy/grafana/provisioning/alerting"
_EVENT = "partner_chat_internal_retrieval_failed"


def test_an_alert_watches_the_internal_retrieval_failure_that_portal_api_logs() -> None:
    exprs = [
        query["model"]["expr"]
        for path in _ALERTING.glob("*.yaml")
        for group in (yaml.safe_load(path.read_text()) or {}).get("groups") or []
        for rule in group.get("rules") or []
        for query in rule.get("data") or []
        if "expr" in query.get("model", {})
    ]
    source = (_BACKEND / "app/services/retrieval_context.py").read_text()

    assert re.search(rf'logger\.\w+\(\s*"{_EVENT}"', source)
    assert any(re.search(rf"service:portal-api\b.*\bevent:{_EVENT}\b", expr) for expr in exprs)
