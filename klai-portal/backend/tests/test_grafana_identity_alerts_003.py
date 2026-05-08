"""SPEC-SEC-IDENTITY-ASSERT-003 REQ-4 — syntax + invariant tests for the
SPEC-003 Grafana alert rules.

Pinned invariants:
- The YAML parses without error.
- Group name + UID prefix follow the klai convention
  (`spec-iam-003-`, per process-rules.md grafana-uid-40-char-limit).
- All UIDs stay under 40 chars (Grafana hard limit).
- All rules carry `spec=SPEC-SEC-IDENTITY-ASSERT-003` for routing.
- Each rule queries the matching log shape that the production
  middleware emits (event:identity_assertion_failed,
  event:missing_org_id).
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERT_PATH = (
    REPO_ROOT
    / "deploy"
    / "grafana"
    / "provisioning"
    / "alerting"
    / "identity-assert-003-rules.yaml"
)


def test_alert_yaml_syntactically_valid() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    assert parsed["apiVersion"] == 1
    assert len(parsed["groups"]) == 1
    assert parsed["groups"][0]["name"] == "spec-iam-003-identity-assert"


def test_all_uids_under_40_chars() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    for rule in parsed["groups"][0]["rules"]:
        assert len(rule["uid"]) <= 40, (
            f"UID {rule['uid']!r} exceeds 40-char Grafana limit ({len(rule['uid'])} chars)"
        )


def test_all_uids_use_klai_prefix_convention() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    for rule in parsed["groups"][0]["rules"]:
        assert rule["uid"].startswith("spec-iam-003-"), (
            f"UID {rule['uid']!r} does not follow the spec-iam-003- convention"
        )


def test_retrieval_verify_failures_rule_present() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in parsed["groups"][0]["rules"] if r["uid"] == "spec-iam-003-retrieval-verify-fail")
    assert rule["title"] == "retrieval_api_identity_assert_failures"
    expr = rule["data"][0]["model"]["expr"]
    assert "service:retrieval-api" in expr
    assert "event:identity_assertion_failed" in expr


def test_retrieval_missing_org_id_rule_present() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in parsed["groups"][0]["rules"] if r["uid"] == "spec-iam-003-retrieval-no-org-id")
    assert rule["title"] == "retrieval_api_missing_org_id"
    expr = rule["data"][0]["model"]["expr"]
    assert "service:retrieval-api" in expr
    assert "event:missing_org_id" in expr


def test_connector_verify_failures_rule_present() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in parsed["groups"][0]["rules"] if r["uid"] == "spec-iam-003-connector-verify-fail")
    assert rule["title"] == "klai_connector_identity_assert_failures"
    expr = rule["data"][0]["model"]["expr"]
    assert "service:klai-connector" in expr
    assert "event:identity_assertion_failed" in expr


def test_every_rule_carries_spec_label() -> None:
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    for rule in parsed["groups"][0]["rules"]:
        labels = rule.get("labels", {})
        assert labels.get("spec") == "SPEC-SEC-IDENTITY-ASSERT-003", (
            f"Rule {rule['uid']!r} missing or wrong `spec` label: {labels.get('spec')!r}"
        )
