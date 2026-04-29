"""Tests for log_utils.settings_scan -- SPEC-SEC-INTERNAL-001 REQ-4.2."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, ClassVar

from log_utils import extract_secret_values


class _FakeBaseSettings:
    """Mimics pydantic-settings ``BaseSettings.model_fields`` shape."""

    model_fields: ClassVar[dict[str, Any]] = {
        "internal_secret": object(),
        "api_key": object(),
        "webhook_secret": object(),
        "hostname": object(),
        "max_retries": object(),
        "github_app_pat": object(),
    }

    def __init__(self) -> None:
        self.internal_secret = "abc12345-secret"
        self.api_key = "key12345"
        self.webhook_secret = "shh"  # too short -- must be skipped
        self.hostname = "api.example.com"  # not a secret-shaped name
        self.max_retries = 5  # not a string
        self.github_app_pat = "ghp_aaaaaaaaaaaaaaaaa"  # PAT


def test_extracts_secrets_from_pydantic_like_object() -> None:
    out = extract_secret_values(_FakeBaseSettings())
    assert "abc12345-secret" in out
    assert "key12345" in out
    assert "ghp_aaaaaaaaaaaaaaaaa" in out
    assert "shh" not in out  # too short
    assert "api.example.com" not in out  # name does not match
    assert 5 not in out  # type filtered out


def test_extracts_secrets_from_plain_namespace() -> None:
    settings = SimpleNamespace(
        portal_internal_secret="bearerXY-12345",
        knowledge_ingest_secret="zzzzzzzz-secret",
        public_url="http://example.com",  # name does not match
        unrelated_field=123,
    )
    out = extract_secret_values(settings)
    assert "bearerXY-12345" in out
    assert "zzzzzzzz-secret" in out
    assert "http://example.com" not in out


def test_handles_none() -> None:
    assert extract_secret_values(None) == set()


def test_skips_empty_secret_values() -> None:
    settings = SimpleNamespace(internal_secret="", webhook_secret="goodsecret-123")
    out = extract_secret_values(settings)
    assert out == {"goodsecret-123"}


def test_password_field_name_is_caught() -> None:
    settings = SimpleNamespace(database_password="changeme-but-long-enough-1")
    assert "changeme-but-long-enough-1" in extract_secret_values(settings)


def test_token_field_name_is_caught() -> None:
    settings = SimpleNamespace(slack_bot_token="xoxb-aaaaaaaaaaaaa")
    assert "xoxb-aaaaaaaaaaaaa" in extract_secret_values(settings)
