"""The failed-product alert counts the event set_product_state logs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import structlog
import yaml

from knowledge_ingest.derived_state import set_product_state

_RULES = (
    Path(__file__).resolve().parents[2] / "deploy/grafana/provisioning/alerting/ingest-rules.yaml"
)


async def test_the_alert_queries_the_event_a_failure_logs() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "org_id": "org-1",
        "kb_slug": "kb",
        "previous_state": "pending",
        "previous_reason": None,
    }
    with structlog.testing.capture_logs() as logs:
        await set_product_state(conn, "a-1", "graph", "failed", "quarantined")
    (event,) = [e for e in logs if e["log_level"] == "error"]

    rules = yaml.safe_load(_RULES.read_text())["groups"][0]["rules"]
    (rule,) = [r for r in rules if r["title"] == event["event"]]
    assert f"event:{event['event']}" in rule["data"][0]["model"]["expr"]
    assert rule["annotations"]["runbook_url"] == "docs/runbooks/derived-product-failed.md"
