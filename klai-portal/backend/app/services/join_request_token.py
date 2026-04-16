"""HMAC-SHA256 approval token for join requests (SPEC-AUTH-006 R5)."""

import hashlib
import hmac

from app.core.config import settings


def _get_key() -> bytes:
    """Return the HMAC key from the SSO cookie key (shared secret)."""
    return settings.sso_cookie_key.encode()


def generate_approval_token(request_id: int, zitadel_user_id: str) -> str:
    """Generate HMAC-SHA256 of (id + zitadel_user_id) using the SSO cookie key."""
    message = f"{request_id}:{zitadel_user_id}".encode()
    return hmac.new(_get_key(), message, hashlib.sha256).hexdigest()


def verify_approval_token(token: str, request_id: int, zitadel_user_id: str) -> bool:
    """Verify an approval token against the expected HMAC."""
    expected = generate_approval_token(request_id, zitadel_user_id)
    return hmac.compare_digest(token, expected)
