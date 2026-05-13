"""Redis-backed pending session for multi-org workspace selection (SPEC-AUTH-009 R3).

When an SSO user belongs to multiple orgs or has domain-match workspaces,
idp_callback stores session details in Redis with a short TTL and redirects
to /select-workspace?ref={uuid}. The user selects an org, and POST
/api/auth/select-workspace consumes the pending session to finalize.
"""

import json
import uuid
from typing import Literal

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_PENDING_SESSION_TTL = 600  # 10 minutes
_KEY_PREFIX = "pending_session:"


class PendingEntry(dict):
    """
    # @MX:NOTE SPEC-AUTH-009 R3/C3.1 -- discriminated entry in pending-session.
    # kind=member: user is already a portal_users member of this org.
    # kind=domain_match: org primary_domain matches user email domain;
    # user has no portal_users row yet.
    """

    __annotations__ = {
        "org_id": int,
        "name": str,
        "slug": str,
        "kind": Literal["member", "domain_match"],
        "auto_accept": bool,
    }


class PendingSessionService:
    """Store and retrieve pending SSO sessions in Redis."""

    async def store(
        self,
        session_id: str,
        session_token: str,
        zitadel_user_id: str,
        email: str,
        auth_request_id: str,
        entries: list,
    ) -> str:
        """Store pending session data and return a reference UUID."""
        ref = str(uuid.uuid4())
        data = json.dumps(
            {
                "session_id": session_id,
                "session_token": session_token,
                "zitadel_user_id": zitadel_user_id,
                "email": email,
                "auth_request_id": auth_request_id,
                "entries": entries,
            }
        )

        pool = await get_redis_pool()
        if pool:
            await pool.set(f"{_KEY_PREFIX}{ref}", data, ex=_PENDING_SESSION_TTL)
        else:
            logger.warning("Redis not available -- pending session cannot be stored")

        return ref

    async def retrieve(self, ref: str) -> dict | None:
        """Retrieve pending session data without consuming it."""
        pool = await get_redis_pool()
        if not pool:
            return None

        raw = await pool.get(f"{_KEY_PREFIX}{ref}")
        if not raw:
            return None

        return json.loads(raw)

    async def consume(self, ref: str) -> dict | None:
        """Retrieve and delete pending session data (one-time use)."""
        pool = await get_redis_pool()
        if not pool:
            return None

        key = f"{_KEY_PREFIX}{ref}"
        raw = await pool.get(key)
        if not raw:
            return None

        await pool.delete(key)
        return json.loads(raw)
