"""
Redis-backed PKCE / state store for the BFF OIDC flow (SPEC-AUTH-008 R2, R3).

When /api/auth/oidc/start fires, we persist
    {code_verifier, return_to, user_agent_hash, created_at}
under key `klai:oidc_pending:<state>` for 10 minutes. The callback consumes
the record exactly once: this both proves the state round-tripped and
provides the code_verifier for the PKCE token exchange.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass

import structlog

from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_KEY_PREFIX = "klai:oidc_pending:"
_TTL_SECONDS = 600


@dataclass(slots=True)
class PendingAuth:
    """Transient state bound to a single OIDC authorize → callback round-trip."""

    code_verifier: str
    return_to: str
    user_agent_hash: str
    created_at: int


class OidcPendingService:
    """Store, consume, and (for tests) inspect pending OIDC authorize state."""

    async def put(
        self,
        *,
        state: str,
        code_verifier: str,
        return_to: str,
        user_agent_hash: str,
    ) -> None:
        pool = await get_redis_pool()
        if pool is None:
            raise RuntimeError("Redis pool is unavailable; OIDC flow cannot start")
        record = PendingAuth(
            code_verifier=code_verifier,
            return_to=return_to,
            user_agent_hash=user_agent_hash,
            created_at=int(time.time()),
        )
        await pool.set(
            _KEY_PREFIX + state,
            json.dumps(asdict(record), separators=(",", ":")),
            ex=_TTL_SECONDS,
        )

    async def consume(self, state: str) -> PendingAuth | None:
        pool = await get_redis_pool()
        if pool is None:
            return None
        key = _KEY_PREFIX + state
        raw = await pool.get(key)
        if raw is None:
            return None
        await pool.delete(key)
        data = json.loads(raw)
        return PendingAuth(**data)


oidc_pending = OidcPendingService()
