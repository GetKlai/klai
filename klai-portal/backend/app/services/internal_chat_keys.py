"""The one partner key a tenant's LibreChat uses to reach portal-api's chat endpoint.

A key with ``internal_chat`` answers as whichever employee the request names
(``app.services.chat_profile``), personal knowledge bases included, so only
provisioning mints one: the org admin API refuses it
(``admin_api_keys._reject_internal_chat``). It carries no knowledge-base rows,
because the employee's own profile decides the scope of every turn.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.partner_api_keys import PartnerAPIKey
from app.services.partner_keys import generate_partner_key, verify_partner_key

INTERNAL_CHAT_KEY_NAME = "LibreChat internal chat"
INTERNAL_CHAT_PERMISSIONS = {"chat": True, "general_chat": True, "internal_chat": True}
# check_rate_limit counts every request of the key in a 60 s sliding window,
# and this one key carries the whole tenant: one turn per employee message plus
# a title call on a conversation's first message. 6000/min is 1000 employees
# each sending three calls a minute, twice over, far above what one tenant
# types. Spend is capped downstream by the LiteLLM team budget, so this limit
# is only a brake on a runaway client loop, never on people.
INTERNAL_CHAT_RATE_LIMIT_RPM = 6000
INTERNAL_CHAT_CREATED_BY = "klai-provisioning"


async def _internal_chat_keys(db: AsyncSession, org_id: int) -> list[PartnerAPIKey]:
    result = await db.execute(
        select(PartnerAPIKey).where(
            PartnerAPIKey.org_id == org_id,
            PartnerAPIKey.permissions["internal_chat"].as_boolean().is_(True),
        )
    )
    return list(result.scalars().all())


async def mint_internal_chat_key(db: AsyncSession, org_id: int, *, rotate: bool = False) -> str | None:
    """Create the org's internal-chat key and return its plaintext, shown this once.

    Returns None when the org already has one and ``rotate`` is False. With
    ``rotate`` every existing internal-chat key of the org is deleted in the
    same commit, so the old plaintext stops working as the new one starts.
    """
    existing = await _internal_chat_keys(db, org_id)
    if existing and not rotate:
        return None
    for key in existing:
        await db.delete(key)

    plaintext, key_hash = generate_partner_key()
    db.add(
        PartnerAPIKey(
            org_id=org_id,
            name=INTERNAL_CHAT_KEY_NAME,
            key_prefix=plaintext[:12],
            key_hash=key_hash,
            permissions=dict(INTERNAL_CHAT_PERMISSIONS),
            rate_limit_rpm=INTERNAL_CHAT_RATE_LIMIT_RPM,
            created_by=INTERNAL_CHAT_CREATED_BY,
        )
    )
    await db.commit()
    return plaintext


async def holds_internal_chat_key(db: AsyncSession, org_id: int, plaintext: str) -> bool:
    """True when ``plaintext`` is the org's current internal-chat key."""
    return any(verify_partner_key(plaintext, key.key_hash) for key in await _internal_chat_keys(db, org_id))


async def revoke_internal_chat_key(db: AsyncSession, org_id: int) -> int:
    """Delete the org's internal-chat keys, so no unheld copy stays valid; returns how many."""
    existing = await _internal_chat_keys(db, org_id)
    for key in existing:
        await db.delete(key)
    await db.commit()
    return len(existing)
