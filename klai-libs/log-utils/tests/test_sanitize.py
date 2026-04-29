"""Tests for log_utils.sanitize -- SPEC-SEC-INTERNAL-001 REQ-4."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import structlog.testing

from log_utils import sanitize_from_settings, sanitize_response_body


def _make_response(body: str, status: int = 500) -> httpx.Response:
    return httpx.Response(status_code=status, text=body)


def _make_status_error(body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://upstream.example/")
    response = _make_response(body)
    return httpx.HTTPStatusError(message="boom", request=request, response=response)


# --- AC-4.1 + AC-4.6: known secret value is scrubbed ----------------------------------

def test_secret_substring_is_replaced() -> None:
    """REQ-4.1: known secret value is replaced by <redacted>; unrelated text survives."""
    secret = "my-internal-secret-12345"
    body = f"gRPC error: invalid token {secret} for user mark@voys.nl"
    out = sanitize_response_body(_make_response(body), [secret])
    assert secret not in out
    assert "<redacted>" in out
    assert "mark@voys.nl" in out  # email is not a secret


def test_secret_in_status_error_response_is_replaced() -> None:
    """REQ-4.1: unwraps httpx.HTTPStatusError.response.text correctly."""
    secret = "abcdef0123456789"
    body = f"upstream said {secret}"
    out = sanitize_response_body(_make_status_error(body), {secret})
    assert secret not in out


def test_two_occurrences_of_same_secret_are_both_redacted() -> None:
    secret = "tokenvaluexyz9999"
    body = f"first {secret} middle {secret} last"
    out = sanitize_response_body(_make_response(body), [secret])
    assert secret not in out
    assert out.count("<redacted>") == 2


def test_overlapping_secret_lengths_handled_long_first() -> None:
    """A short secret that is a prefix of a longer one must not corrupt the longer match."""
    short = "internalsecret"
    long_ = "internalsecret-extension-9876"
    body = f"the long is {long_} and the short is {short}!"
    out = sanitize_response_body(_make_response(body), [short, long_])
    assert short not in out
    assert long_ not in out


# --- AC-4.2: truncation -------------------------------------------------------------

def test_output_is_truncated_to_default_max_len() -> None:
    body = "A" * 10_000
    out = sanitize_response_body(_make_response(body), [])
    assert len(out) <= 512


def test_output_respects_custom_max_len() -> None:
    body = "B" * 4_096
    out = sanitize_response_body(_make_response(body), [], max_len=128)
    assert len(out) == 128


# --- AC-4.4: idempotent / safe inputs ----------------------------------------------

def test_none_returns_empty_string() -> None:
    assert sanitize_response_body(None, []) == ""


def test_empty_body_returns_empty_string() -> None:
    out = sanitize_response_body(_make_response(""), ["some-secret-12345"])
    assert out == ""


def test_object_without_text_returns_empty_string() -> None:
    """REQ-4.5: idempotent / safe on weird inputs."""
    assert sanitize_response_body(SimpleNamespace(other="x"), []) == ""


# --- Boundary safety (deviation from SPEC literal numbered list) -------------------

def test_secret_straddling_max_len_boundary_is_redacted() -> None:
    """A secret that crosses byte 512 of the body is still fully redacted.

    The SPEC's numbered list reads truncate-then-strip; this implementation
    does strip-then-truncate so a partial-secret tail can never appear in
    the truncated output.
    """
    secret = "thisisaverysensitivesecretvalue1234"  # 35 chars
    prefix = "x" * 500
    suffix = "garbage"
    body = f"{prefix}{secret}{suffix}"
    out = sanitize_response_body(_make_response(body), [secret], max_len=512)
    assert secret not in out
    assert "<redacted>" in out


# --- REQ-4.2 short-secret guardrail ------------------------------------------------

def test_short_secret_is_not_redacted() -> None:
    """REQ-4.2: secret values shorter than 8 chars are skipped to prevent over-redaction."""
    body = "fly to the moon"
    out = sanitize_response_body(_make_response(body), ["the"])  # 3 chars
    assert out == "fly to the moon"


def test_empty_secret_in_iterable_is_ignored() -> None:
    body = "no leak here"
    out = sanitize_response_body(_make_response(body), ["", "x"])
    assert out == "no leak here"


# --- AC-4.3: redaction logging -----------------------------------------------------

def test_redaction_count_emitted_to_structlog() -> None:
    """REQ-4.3: response_body_sanitized debug entry on at-least-one redaction."""
    secret = "secret-token-1234"
    body = f"first {secret} second {secret} third"
    with structlog.testing.capture_logs() as logs:
        sanitize_response_body(_make_response(body), [secret])
    matching = [e for e in logs if e.get("event") == "response_body_sanitized"]
    assert len(matching) == 1
    assert matching[0]["redaction_count"] == 2
    assert matching[0]["original_length"] == len(body)


def test_no_log_emitted_when_no_redaction_happens() -> None:
    body = "nothing to redact here"
    with structlog.testing.capture_logs() as logs:
        sanitize_response_body(_make_response(body), ["unrelated-secret-9999"])
    assert not any(e.get("event") == "response_body_sanitized" for e in logs)


def test_no_log_emitted_on_empty_body() -> None:
    """REQ-4.4 + REQ-4.5: empty input emits no sanitized log entry."""
    with structlog.testing.capture_logs() as logs:
        sanitize_response_body(_make_response(""), ["abcdef0123456789"])
    assert not any(e.get("event") == "response_body_sanitized" for e in logs)


# --- DoS bound (input cap) ---------------------------------------------------------

def test_huge_body_is_capped_before_scanning() -> None:
    """A multi-MB body is clipped to the input cap; a secret beyond the cap is not seen."""
    cap = 65_536
    secret = "leaked-secret-far-far-away-9999"
    huge = ("A" * cap) + secret  # secret lives past the cap
    out = sanitize_response_body(_make_response(huge), [secret], max_len=2_000)
    # We only guarantee the *output* is short and does not contain the secret.
    assert len(out) == 2_000
    assert secret not in out


# --- sanitize_from_settings convenience wrapper -----------------------------------

def test_sanitize_from_settings_uses_settings_secrets() -> None:
    settings = SimpleNamespace(
        internal_secret="sneakysecret-42abc",
        webhook_secret="anotherwebhooksecret-9999",
        hostname="api.example.com",  # not a secret-shaped name
    )
    body = "leaked sneakysecret-42abc and api.example.com"
    out = sanitize_from_settings(settings, _make_response(body))
    assert "sneakysecret-42abc" not in out
    assert "api.example.com" in out
