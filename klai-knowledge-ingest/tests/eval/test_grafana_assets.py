"""Syntax + invariant tests for the Grafana dashboard + alert rule (SPEC-RAG-EVAL-001 Unit 5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
_GRAFANA_DIR = REPO_ROOT / "deploy" / "grafana" / "provisioning"
DASHBOARD_PATH = _GRAFANA_DIR / "dashboards" / "rag-quality.json"
ALERT_PATH = _GRAFANA_DIR / "alerting" / "rag-eval-rules.yaml"


def test_dashboard_json_syntactically_valid() -> None:
    """The dashboard JSON parses without error."""
    raw = DASHBOARD_PATH.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["uid"] == "rag-quality"
    assert parsed["title"] == "RAG quality (RAGAS metrics)"


def test_dashboard_has_four_metric_panels_plus_failure_panel() -> None:
    """Dashboard ships 4 RAGAS metric panels + 1 failure-row panel + 1 low-confidence panel.

    The "Low-Confidence" Prometheus panel was added in
    SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (REQ-8, commit b9c1d1229) to surface
    confidence-band / link-survival observability alongside the RAGAS metrics.
    """
    parsed = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    panels = parsed["panels"]
    assert len(panels) == 6
    titles = {p["title"] for p in panels}
    expected_metrics = {
        "Context precision (7-day moving average)",
        "Context recall (7-day moving average)",
        "Faithfulness (7-day moving average)",
        "Answer relevance (7-day moving average)",
        "Failed-row count per nightly run (NULL metrics)",
        "Low-Confidence",
    }
    assert titles == expected_metrics


def test_dashboard_has_variant_template_variable() -> None:
    """The dashboard exposes a `variant` template variable for SQL filtering."""
    parsed = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    template_names = {v["name"] for v in parsed["templating"]["list"]}
    assert "variant" in template_names


def test_dashboard_panels_filter_by_variant() -> None:
    """Every SQL metric panel filters on `$variant` (Unit 5 contract).

    The postgres RAGAS panels query knowledge.rag_eval_results and MUST scope to
    `$variant`. The "Low-Confidence" panel added in REQ-8 is a Prometheus panel
    whose targets use `expr` (PromQL) instead of `rawSql` and filter on `$org_id`
    rather than `$variant`; it has no SQL, so the variant contract does not apply
    to it. Assert each SQL target still carries the $variant filter, and confirm
    the non-SQL panel uses `expr` (so a future SQL panel can't silently drop the
    filter by omitting rawSql).
    """
    parsed = json.loads(DASHBOARD_PATH.read_text(encoding="utf-8"))
    sql_target_count = 0
    for panel in parsed["panels"]:
        for target in panel["targets"]:
            if "rawSql" in target:
                sql_target_count += 1
                assert "$variant" in target["rawSql"], (
                    f"Panel {panel['title']!r} SQL is missing $variant filter."
                )
            else:
                assert "expr" in target, (
                    f"Panel {panel['title']!r} target has neither rawSql nor expr."
                )
    # The five RAGAS/failure panels each contribute exactly one SQL target.
    assert sql_target_count == 5, (
        f"Expected 5 SQL targets across the RAGAS panels, found {sql_target_count}."
    )


def test_alert_yaml_syntactically_valid() -> None:
    """The alert rule YAML parses without error."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    assert parsed["apiVersion"] == 1
    assert len(parsed["groups"]) == 1
    assert parsed["groups"][0]["name"] == "rag-eval-001"


def test_alert_faithfulness_rule_present() -> None:
    """rag_eval_faithfulness_low rule is provisioned with the right uid + threshold."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rules = parsed["groups"][0]["rules"]
    rule_uids = {r["uid"] for r in rules}
    assert "rag-eval-001-faithfulness-low" in rule_uids
    rule = next(r for r in rules if r["uid"] == "rag-eval-001-faithfulness-low")
    assert rule["title"] == "rag_eval_faithfulness_low"
    raw_sql = rule["data"][0]["model"]["rawSql"]
    # PR #363 deliberately lowered the rag_eval_faithfulness_low
    # threshold from 0.85 to 0.80. The alert rule is the source of
    # truth; this assertion follows.
    assert "0.80" in raw_sql
    assert "variant = 'baseline'" in raw_sql
    assert "HAVING COUNT(*) = 2" in raw_sql


def test_alert_carries_spec_label() -> None:
    """Alert rule carries the SPEC-RAG-EVAL-001 label for routing."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rule = parsed["groups"][0]["rules"][0]
    assert rule["labels"]["spec"] == "SPEC-RAG-EVAL-001"
    assert rule["labels"]["severity"] == "high"


@pytest.mark.parametrize("path", [DASHBOARD_PATH, ALERT_PATH])
def test_grafana_asset_exists(path: Path) -> None:
    """Sanity: the asset files exist where Grafana provisioning expects them."""
    assert path.is_file(), f"missing Grafana asset: {path}"
