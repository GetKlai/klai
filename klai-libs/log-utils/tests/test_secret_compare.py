"""Tests for log_utils.secret_compare -- SPEC-SEC-INTERNAL-001 REQ-1.7."""

from __future__ import annotations

import pytest

from log_utils import verify_shared_secret


def test_match_returns_true() -> None:
    assert verify_shared_secret("hello-secret-12345", "hello-secret-12345") is True


def test_mismatch_returns_false() -> None:
    assert verify_shared_secret("attacker-guess-1234", "real-secret-1234567") is False


def test_empty_header_returns_false_without_raising() -> None:
    assert verify_shared_secret("", "real-secret-1234567") is False


def test_none_header_returns_false_without_raising() -> None:
    assert verify_shared_secret(None, "real-secret-1234567") is False


def test_empty_configured_raises_value_error() -> None:
    with pytest.raises(ValueError, match="empty configured secret"):
        verify_shared_secret("anything", "")


def test_unicode_inputs_compare_correctly() -> None:
    secret = "geheim-één-1234"
    assert verify_shared_secret(secret, secret) is True
    assert verify_shared_secret("geheim-twee-1234", secret) is False
