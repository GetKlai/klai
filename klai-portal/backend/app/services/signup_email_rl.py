"""SPEC-SEC-HYGIENE-001 REQ-19: per-email rate limit on POST /api/signup.

Caddy already limits ``/api/signup`` at 10 events/min per client IP
(``@portal-api-sensitive`` zone). The per-IP limit does NOT prevent:

- A single actor cycling email addresses from a single IP.
- A single email attempting signup repeatedly from many IPs (botnet style).

This module layers a 24-hour Redis counter keyed on the SHA-256 of the
normalised email, on top of the IP limit. Email normalisation lowercases
the address and strips ``+alias`` from the local-part so
``Mark+signup@voys.nl`` and ``mark@voys.nl`` share a counter.

The counter is incremented BEFORE Zitadel org-creation (REQ-19.5) so
rejected attempts never consume Zitadel quota. Fail-open on Redis
unreachable (REQ-19.4) — same pattern as ``partner_dependencies``: an
internal infra outage must not block signups.
"""

from __future__ import annotations

import hashlib
import logging

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger(__name__)
_stdlib_logger = logging.getLogger(__name__)

# REQ-19.1: 3 successful signups within 24h → 429 on the 4th attempt.
EMAIL_RL_LIMIT = 3
# REQ-19.2: 24-hour window.
EMAIL_RL_WINDOW_SECONDS = 24 * 60 * 60


def normalise_email(email: str) -> str:
    """REQ-19.3: lowercase + strip +alias from local-part.

    ``Mark+signup@voys.nl`` → ``mark@voys.nl``. The normalised form is
    used ONLY for the rate-limit key — the account is still created with
    the user-supplied email, so display + login flow are unaffected.

    Inputs are validated as ``EmailStr`` upstream by Pydantic; this
    helper trusts that an ``@`` is present. Falls back to lowercased
    input if the ``@`` is missing (defensive: never raise).
    """
    email = email.lower().strip()
    local, sep, domain = email.partition("@")
    if not sep:
        return email
    if "+" in local:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def email_sha256(email: str) -> str:
    """SHA-256 of the normalised email — used as the Redis key suffix
    AND as the structlog observability field. Plaintext emails never
    enter Redis or logs (REQ-19.2).
    """
    return hashlib.sha256(normalise_email(email).encode("utf-8")).hexdigest()


async def check_signup_email_rate_limit(
    email: str,
    *,
    max_per_window: int = EMAIL_RL_LIMIT,
) -> bool:
    """Return True iff this signup attempt is permitted.

    REQ-19.1 / REQ-19.2: INCR + EXPIRE counter at
    ``signup_email_rl:<sha256(normalised_email)>`` with a 24-hour TTL.
    EXPIRE is set only on the first INCR of the window (count == 1) so
    repeated attempts inside the window do not extend it indefinitely.

    REQ-19.4 (fail-open): if Redis is unreachable OR any Redis call
    raises, log ``signup_email_rl_redis_unavailable`` and return True
    (allow the signup). An internal infra outage must not block the
    signup funnel — this matches the partner-API pattern.
    """
    digest = email_sha256(email)
    redis_pool = await get_redis_pool()
    if redis_pool is None:
        logger.warning("signup_email_rl_redis_unavailable", email_sha256=digest)
        return True

    key = f"signup_email_rl:{digest}"
    try:
        count = await redis_pool.incr(key)
        if count == 1:
            await redis_pool.expire(key, EMAIL_RL_WINDOW_SECONDS)
    except Exception:
        # REQ-19.4 fail-open. Use stdlib logger.exception so the traceback
        # makes it into VictoriaLogs alongside the structlog event below.
        _stdlib_logger.exception("signup_email_rl_redis_call_failed")
        # exc_info=True ensures the structlog event also carries the traceback
        # — the project's logger-traceback audit forbids bare logger.warning
        # inside an except block.
        logger.warning("signup_email_rl_redis_unavailable", email_sha256=digest, exc_info=True)
        return True

    if count > max_per_window:
        logger.info(
            "signup_email_rate_limited",
            email_sha256=digest,
            count=count,
            window_seconds=EMAIL_RL_WINDOW_SECONDS,
        )
        return False
    return True
