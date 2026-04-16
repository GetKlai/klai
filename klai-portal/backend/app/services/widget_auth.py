"""Widget authentication service.

SPEC-WIDGET-001 Task 2:
- generate_session_token: create HS256 JWT for widget chat sessions
- origin_allowed: exact origin validation (scheme + host + port)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import structlog

logger = structlog.get_logger()

_SESSION_TTL_SECONDS = 3600  # 1 hour


def generate_session_token(
    wgt_id: str,
    org_id: int,
    kb_ids: list[int],
    secret: str,
) -> str:
    """Generate a HS256-signed JWT session token for widget chat.

    # @MX:ANCHOR: Public widget session token entry point
    # @MX:REASON: Called from widget-config endpoint; claims control chat access

    Claims:
        wgt_id: widget identifier
        org_id: organisation integer id
        kb_ids: list of knowledge base ids the widget may access
        exp: expiry timestamp (UTC, 1 hour from now)

    Args:
        wgt_id: The widget_id string (e.g. wgt_abcdef...)
        org_id: Portal organisation integer id
        kb_ids: Knowledge base ids accessible by this widget
        secret: WIDGET_JWT_SECRET from settings

    Returns:
        HS256-signed JWT string
    """
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=_SESSION_TTL_SECONDS)

    payload = {
        "wgt_id": wgt_id,
        "org_id": org_id,
        "kb_ids": kb_ids,
        "exp": int(exp.timestamp()),
    }

    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session_token(token: str, secret: str) -> dict:
    """Decode and validate a widget session token.

    Raises jwt.ExpiredSignatureError if expired.
    Raises jwt.InvalidTokenError (or subclass) if invalid.

    Args:
        token: JWT string to decode
        secret: WIDGET_JWT_SECRET from settings

    Returns:
        Decoded payload dict
    """
    return jwt.decode(token, secret, algorithms=["HS256"])


def origin_allowed(origin: str, allowed_origins: list[str]) -> bool:
    """Validate origin against allowed list using exact match.

    # @MX:ANCHOR: [AUTO] CORS origin gate — called for every widget request
    # @MX:REASON: Security boundary; must remain fail-closed (empty list → False)
    # @MX:SPEC: SPEC-WIDGET-001 REQ-1.3

    Compares scheme + host + port exactly.
    Trailing slashes are stripped before comparison.
    An empty allowed list always returns False (fail-closed).

    Args:
        origin: The Origin header value from the request
        allowed_origins: List of allowed origin strings from widget_config

    Returns:
        True if origin is in the allowed list, False otherwise
    """
    if not allowed_origins:
        return False

    # Normalise by stripping trailing slashes
    normalised_origin = origin.rstrip("/")

    return any(normalised_origin == allowed.rstrip("/") for allowed in allowed_origins)
