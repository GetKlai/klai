"""SPEC-SEC-IDENTITY-ASSERT-002 REQ-6.2 — syntax + invariant tests for
the identity-assert Grafana alert rules.

Pinned invariants:
- The YAML parses without error.
- Group name + UID prefix follow the klai convention
  (`spec-iam-002-`, per process-rules.md grafana-uid-40-char-limit).
- The bff_proxy_verify_failures rule queries the
  `bff_proxy_verified verified:false` log shape that the proxy emits.
- The scribe_auth_internal_secret_mismatch rule queries the
  `scribe_auth_internal_secret_mismatch` event the new auth path emits.
- All UIDs stay under 40 chars (Grafana hard limit).
- All rules carry a `spec=SPEC-SEC-IDENTITY-ASSERT-002` label so the
  alert routes through the right notification policy.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
ALERT_PATH = REPO_ROOT / "deploy" / "grafana" / "provisioning" / "alerting" / "identity-assert-rules.yaml"


def test_alert_yaml_syntactically_valid() -> None:
    """The alert YAML parses without error and ships one group."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    assert parsed["apiVersion"] == 1
    assert len(parsed["groups"]) == 1
    assert parsed["groups"][0]["name"] == "spec-iam-002-identity-assert"


def test_all_uids_under_40_chars() -> None:
    """Grafana enforces a hard 40-char UID limit (process-rules.md
    grafana-uid-40-char-limit). Crossing it crashes Grafana into a
    restart loop on first sync."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    for rule in parsed["groups"][0]["rules"]:
        assert len(rule["uid"]) <= 40, f"UID {rule['uid']!r} exceeds 40-char Grafana limit ({len(rule['uid'])} chars)"


def test_all_uids_use_klai_prefix_convention() -> None:
    """Per process-rules.md the klai UID convention is
    `spec-<3-letter>-<3-digit>-<verb>`. SPEC-002 uses `spec-iam-002-`."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    for rule in parsed["groups"][0]["rules"]:
        assert rule["uid"].startswith("spec-iam-002-"), (
            f"UID {rule['uid']!r} does not follow the spec-iam-002- convention"
        )


def test_bff_verify_failures_rule_present() -> None:
    """The Mark-incident regression guard is provisioned with the
    correct LogsQL query."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in parsed["groups"][0]["rules"] if r["uid"] == "spec-iam-002-bff-verify-fail")
    assert rule["title"] == "bff_proxy_verify_failures"
    expr = rule["data"][0]["model"]["expr"]
    assert "service:portal-api" in expr
    assert "event:bff_proxy_verified" in expr
    assert "verified:false" in expr


def test_scribe_secret_mismatch_rule_present() -> None:
    """The post-rotation asymmetric-deploy guard is provisioned."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    rule = next(r for r in parsed["groups"][0]["rules"] if r["uid"] == "spec-iam-002-scribe-secret-mismatch")
    assert rule["title"] == "scribe_auth_internal_secret_mismatch"
    expr = rule["data"][0]["model"]["expr"]
    assert "service:scribe-api" in expr
    assert "event:scribe_auth_internal_secret_mismatch" in expr


def test_every_rule_carries_spec_label() -> None:
    """Notification routing keys on the `spec` label — a missing label
    means the alert never reaches the right inbox."""
    parsed = yaml.safe_load(ALERT_PATH.read_text(encoding="utf-8"))
    for rule in parsed["groups"][0]["rules"]:
        labels = rule.get("labels", {})
        assert labels.get("spec") == "SPEC-SEC-IDENTITY-ASSERT-002", (
            f"Rule {rule['uid']!r} missing or wrong `spec` label: {labels.get('spec')!r}"
        )
