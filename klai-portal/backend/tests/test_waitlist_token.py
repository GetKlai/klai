"""SPEC-LAUNCH-SOFTLAUNCH-001 B-2: waitlist invite-token sign + verify tests."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from app.services.waitlist_token import (
    DEFAULT_TTL_SECONDS,
    InviteTokenPayload,
    WaitlistTokenUnavailable,
    sign_invite_token,
    verify_invite_token,
)


@pytest.fixture
def _key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Set a deterministic key for the duration of a test."""
    key = "test-waitlist-key-do-not-use-in-prod-0123456789"
    monkeypatch.setenv("WAITLIST_TOKEN_KEY", key)
    # Re-import config so the new env value is picked up.
    import importlib
    import sys

    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.waitlist_token", None)
    importlib.import_module("app.core.config")
    importlib.import_module("app.services.waitlist_token")
    return key


def test_sign_and_verify_round_trip(_key: str) -> None:
    """A freshly-signed token verifies and yields the same fields."""
    from app.services.waitlist_token import sign_invite_token, verify_invite_token

    token = sign_invite_token("eline@vermeer.nl", "Vermeer Advocaten")
    payload = verify_invite_token(token)
    assert payload is not None
    assert payload.email == "eline@vermeer.nl"
    assert payload.company == "Vermeer Advocaten"
    assert payload.exp > int(time.time())
    assert payload.exp <= int(time.time()) + DEFAULT_TTL_SECONDS + 5


def test_sign_normalises_email_to_lowercase(_key: str) -> None:
    from app.services.waitlist_token import sign_invite_token, verify_invite_token

    token = sign_invite_token("ELINE@Vermeer.NL", "Vermeer Advocaten")
    payload = verify_invite_token(token)
    assert payload is not None
    assert payload.email == "eline@vermeer.nl"


def test_verify_rejects_tampered_payload(_key: str) -> None:
    from app.services.waitlist_token import sign_invite_token, verify_invite_token

    token = sign_invite_token("eline@vermeer.nl", "Vermeer Advocaten")
    payload_b64, sig_b64 = token.split(".")
    # Swap one byte in the payload — sig will not match.
    tampered = payload_b64[:-1] + ("A" if payload_b64[-1] != "A" else "B")
    assert verify_invite_function_helper(tampered, sig_b64) is None


def verify_invite_function_helper(payload_b64: str, sig_b64: str) -> InviteTokenPayload | None:
    from app.services.waitlist_token import verify_invite_token

    return verify_invite_token(f"{payload_b64}.{sig_b64}")


def test_verify_rejects_malformed(_key: str) -> None:
    from app.services.waitlist_token import verify_invite_token

    assert verify_invite_token("no-dot-here") is None
    assert verify_invite_token("") is None
    assert verify_invite_token("...") is None


def test_verify_rejects_expired(_key: str) -> None:
    """An expired token (negative TTL) returns None."""
    from app.services.waitlist_token import sign_invite_token, verify_invite_token

    token = sign_invite_token("eline@vermeer.nl", "Vermeer Advocaten", ttl_seconds=-10)
    assert verify_invite_token(token) is None


def test_sign_raises_when_key_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var = signing is impossible (caller should 503)."""
    monkeypatch.setenv("WAITLIST_TOKEN_KEY", "")
    import importlib
    import sys

    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.waitlist_token", None)
    importlib.import_module("app.core.config")
    wt = importlib.import_module("app.services.waitlist_token")

    with pytest.raises(WaitlistTokenUnavailable):
        wt.sign_invite_token("a@b.com", "Co")


def test_verify_returns_none_when_key_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env var = verification silently fails (None). Feature disabled."""
    # First sign a token WITH a key configured.
    monkeypatch.setenv("WAITLIST_TOKEN_KEY", "key-for-signing")
    import importlib
    import sys

    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.waitlist_token", None)
    importlib.import_module("app.core.config")
    wt = importlib.import_module("app.services.waitlist_token")
    token = wt.sign_invite_token("a@b.com", "Co")

    # Then clear the key and verify — should be None, not raise.
    monkeypatch.setenv("WAITLIST_TOKEN_KEY", "")
    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.waitlist_token", None)
    importlib.import_module("app.core.config")
    wt = importlib.import_module("app.services.waitlist_token")
    assert wt.verify_invite_token(token) is None


def test_verify_rejects_token_signed_with_different_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token signed with key A does not verify under key B."""
    import importlib
    import sys

    monkeypatch.setenv("WAITLIST_TOKEN_KEY", "key-A")
    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.waitlist_token", None)
    importlib.import_module("app.core.config")
    wt = importlib.import_module("app.services.waitlist_token")
    token = wt.sign_invite_token("a@b.com", "Co")

    monkeypatch.setenv("WAITLIST_TOKEN_KEY", "key-B")
    sys.modules.pop("app.core.config", None)
    sys.modules.pop("app.services.waitlist_token", None)
    importlib.import_module("app.core.config")
    wt = importlib.import_module("app.services.waitlist_token")
    assert wt.verify_invite_token(token) is None
