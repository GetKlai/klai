"""SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-14 — fail-closed validator for
retrieval-api ``PORTAL_INTERNAL_SECRET``.

retrieval-api → portal-api outbound trust boundary. The IdentityAsserter
in retrieval_api.middleware.auth uses this Bearer to call
/internal/identity/verify on portal-api when an internal-secret request
includes org_id / user_id in the body (SPEC-SEC-IDENTITY-ASSERT-001).

With an empty/whitespace value, the outbound httpx call would send
``Authorization: Bearer `` (literal trailing space) — exact
empty-secret-fail-open pattern from
.claude/rules/klai/pitfalls/process-rules.md. Pre this REQ-14 extension,
the failure surfaced at IdentityAsserter construct time (deeper in the
stack); now it surfaces at Settings() time alongside INTERNAL_SECRET
and REDIS_URL.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from pydantic import ValidationError


def _purge_config_module() -> None:
    """Drop cached config module so re-import re-runs the model_validator."""
    sys.modules.pop("retrieval_api.config", None)


def test_valid_portal_internal_secret_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: non-empty value passes."""
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", "valid-non-empty-secret")
    _purge_config_module()
    config_module = importlib.import_module("retrieval_api.config")
    assert config_module.settings.portal_internal_secret == "valid-non-empty-secret"


@pytest.mark.parametrize("value", ["", "   ", "\t\n "], ids=["empty", "spaces", "whitespace"])
def test_empty_or_whitespace_portal_internal_secret_refuses_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """REQ-14: empty / whitespace-only PORTAL_INTERNAL_SECRET raises ValidationError.

    The module-level ``settings = Settings()`` in retrieval_api.config means
    the failure surfaces at IMPORT TIME — that is the actual production
    behaviour (container refuses to start).
    """
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", value)
    _purge_config_module()
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("retrieval_api.config")
    msg = str(exc_info.value)
    assert "PORTAL_INTERNAL_SECRET" in msg
    assert "SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-14" in msg


def test_missing_portal_internal_secret_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """PORTAL_INTERNAL_SECRET absent triggers the validator: field default is empty string."""
    monkeypatch.delenv("PORTAL_INTERNAL_SECRET", raising=False)
    _purge_config_module()
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("retrieval_api.config")
    assert "PORTAL_INTERNAL_SECRET" in str(exc_info.value)


def test_aggregated_message_lists_all_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When BOTH INTERNAL_SECRET and PORTAL_INTERNAL_SECRET are empty, the
    aggregated error names both — the operator gets one fix-once message
    instead of a stuttering chain of single-field failures.
    """
    monkeypatch.setenv("INTERNAL_SECRET", "")
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", "")
    _purge_config_module()
    with pytest.raises(ValidationError) as exc_info:
        importlib.import_module("retrieval_api.config")
    msg = str(exc_info.value)
    assert "INTERNAL_SECRET" in msg
    assert "PORTAL_INTERNAL_SECRET" in msg
