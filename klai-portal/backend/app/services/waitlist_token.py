"""Waitlist invite-token signing and verification.

SPEC-LAUNCH-SOFTLAUNCH-001 B-2.

Stateless HMAC-signed tokens — no DB-table. A token carries the
fields the recipient needs to complete signup (email + company) plus
an expiry. Used to:

- pre-fill the signup form when a recipient clicks their invite link
- bypass the free-email-provider block on signup (B-3) for invited
  warm contacts who use a gmail/yahoo address

Why HMAC, not JWT-the-library: scope is one issuer (portal-api) and
one verifier (portal-api). The full JWT stack would add a dependency
without buying anything; the format here is `base64url(payload).base64url(sig)`
with HMAC-SHA256. Constant-time compare on verification.

If ``settings.waitlist_token_key`` is empty (e.g. dev / CI without
softlaunch infra), verification always returns None and signing
raises ``WaitlistTokenUnavailable``. This is deliberate: the feature
is opt-in via the env var, and failure mode is "no bypass possible"
not "crash".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import structlog

from app.core.config import settings

logger = structlog.get_logger()

# 72 hours per SPEC-LAUNCH-SOFTLAUNCH-001 Q1. Override via the
# ``ttl_seconds`` argument when sign_invite_token is called from a
# context that needs a different window (resend, test, etc.).
DEFAULT_TTL_SECONDS = 72 * 3600


class WaitlistTokenUnavailable(RuntimeError):
    """Signing requested but ``settings.waitlist_token_key`` is empty.

    Caller should surface 503 to the operator — the env var must be
    set in SOPS before invite mails can be issued.
    """


@dataclass(frozen=True)
class InviteTokenPayload:
    """Validated payload of a waitlist invite token."""

    email: str
    company: str
    exp: int  # Unix timestamp (seconds)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(encoded: str) -> bytes:
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


def _key_bytes() -> bytes | None:
    """Return the HMAC key bytes, or None if not configured."""
    raw = settings.waitlist_token_key.strip()
    if not raw:
        return None
    return raw.encode("utf-8")


def sign_invite_token(
    email: str,
    company: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> str:
    """Sign a waitlist invite token.

    Args:
        email: Email of the invited recipient. Embedded in the token so
            the signup endpoint can verify the submitted email matches.
        company: Company name from the original waitlist submission.
            Pre-fills the signup form's company field.
        ttl_seconds: Token validity window in seconds.

    Raises:
        WaitlistTokenUnavailable: ``waitlist_token_key`` setting is empty.
    """
    key = _key_bytes()
    if key is None:
        raise WaitlistTokenUnavailable(
            "WAITLIST_TOKEN_KEY is not configured — set it in SOPS before issuing invites."
        )

    payload = {
        "email": email.strip().lower(),
        "company": company.strip(),
        "exp": int(time.time()) + ttl_seconds,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{payload_b64}.{sig_b64}"


def verify_invite_token(token: str) -> InviteTokenPayload | None:
    """Verify an invite token. Returns payload on success, None on any failure.

    Failure modes (all return None, logged at INFO):
        - waitlist_token_key not configured
        - token doesn't parse (missing dot, bad base64)
        - signature mismatch
        - expired (exp < now)
        - payload missing required fields

    Constant-time signature comparison via ``hmac.compare_digest``.
    """
    key = _key_bytes()
    if key is None:
        logger.info("waitlist_token_unavailable", reason="key_not_configured")
        return None

    try:
        payload_b64, sig_b64 = token.split(".", 1)
    except ValueError:
        logger.info("waitlist_token_invalid", reason="malformed")
        return None

    try:
        expected_sig = hmac.new(key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        supplied_sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        logger.info("waitlist_token_invalid", reason="sig_decode_failed")
        return None

    if not hmac.compare_digest(expected_sig, supplied_sig):
        logger.info("waitlist_token_invalid", reason="signature_mismatch")
        return None

    try:
        raw = _b64url_decode(payload_b64).decode("utf-8")
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        logger.info("waitlist_token_invalid", reason="payload_decode_failed")
        return None

    if not isinstance(payload, dict):
        logger.info("waitlist_token_invalid", reason="payload_not_object")
        return None

    email = payload.get("email")
    company = payload.get("company")
    exp = payload.get("exp")
    if not isinstance(email, str) or not isinstance(company, str) or not isinstance(exp, int):
        logger.info("waitlist_token_invalid", reason="missing_fields")
        return None

    if exp < int(time.time()):
        logger.info("waitlist_token_invalid", reason="expired", exp=exp)
        return None

    return InviteTokenPayload(email=email, company=company, exp=exp)
