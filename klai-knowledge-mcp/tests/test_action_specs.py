"""Phase 1 validation tests for SPEC-ACTION-CONTRACT-001."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest

from action_specs import SEARCH_KNOWLEDGE_SPEC, validate_action_spec


def _pilot_payload() -> dict:
    return asdict(SEARCH_KNOWLEDGE_SPEC)


def test_validator_accepts_search_knowledge_pilot() -> None:
    validate_action_spec(SEARCH_KNOWLEDGE_SPEC)


@pytest.mark.parametrize(
    "field",
    [
        "action_id",
        "owner_service",
        "entrypoint",
        "kind",
        "input",
        "auth",
        "effects",
        "execution",
        "failure",
        "telemetry",
        "tests",
        "docs",
    ],
)
def test_validator_rejects_missing_required_fields(field: str) -> None:
    payload = _pilot_payload()
    payload.pop(field)

    with pytest.raises(ValueError, match="missing required field"):
        validate_action_spec(payload)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        (None, "kind"),
        ("auth", "mode"),
        ("effects", "access"),
        ("execution", "concurrency_class"),
        ("failure", "mode"),
    ],
)
def test_validator_rejects_invalid_enum_values(section: str | None, field: str) -> None:
    payload = _pilot_payload()
    target = payload if section is None else payload[section]
    target[field] = "invalid"

    with pytest.raises(ValueError, match="invalid"):
        validate_action_spec(payload)


def test_validator_rejects_http_action_without_timeout() -> None:
    payload = _pilot_payload()
    payload["execution"].pop("timeout_ms")

    with pytest.raises(ValueError, match="timeout_ms"):
        validate_action_spec(payload)


@pytest.mark.parametrize("destructive", [None, "false"])
def test_validator_requires_explicit_destructive_boolean(destructive: object) -> None:
    payload = _pilot_payload()
    if destructive is None:
        payload["effects"].pop("destructive")
    else:
        payload["effects"]["destructive"] = destructive

    with pytest.raises(ValueError, match="destructive"):
        validate_action_spec(payload)


def test_validator_rejects_model_facing_action_without_result_policy() -> None:
    payload = deepcopy(_pilot_payload())
    payload.pop("result_policy")

    with pytest.raises(ValueError, match="result_policy"):
        validate_action_spec(payload)
