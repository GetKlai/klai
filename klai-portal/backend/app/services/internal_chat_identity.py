"""Resolve a LibreChat user id to the portal user of one org.

LibreChat sends its Mongo ObjectId as the OpenAI ``user`` field. The id is a
claim made by the org's own LibreChat server, so it is only ever looked up
inside that org: the tenant is set before the first ``portal_users`` select,
both selects also filter on ``org_id`` (``portal_users`` RLS is permissive
when no tenant is set), and the Mongo lookup reads the org's own LibreChat
database. Every failure raises; there is no anonymous fallback.
"""

from __future__ import annotations

import logging

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import set_tenant
from app.models.portal import PortalOrg, PortalUser
from app.services.entitlements import get_effective_products

logger = logging.getLogger(__name__)


class LibreChatIdentityError(Exception):
    """The LibreChat user id does not resolve to a user of this org."""


async def resolve_librechat_user(
    db: AsyncSession, org: PortalOrg, librechat_user_id: str, *, remember: bool = True
) -> PortalUser:
    """The portal user behind a LibreChat id; ``remember`` caches the mapping on the user row.

    Operator scripts that only read production pass ``remember=False``.
    """
    await set_tenant(db, org.id)

    # Fast path: the mapping was cached on an earlier call.
    result = await db.execute(
        select(PortalUser).where(PortalUser.org_id == org.id, PortalUser.librechat_user_id == librechat_user_id)
    )
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    if not settings.librechat_mongo_root_uri:
        logger.warning("KB authz: LIBRECHAT_MONGO_ROOT_URI not set — fail-closed for user %s", librechat_user_id)
        raise LibreChatIdentityError
    if not org.librechat_container:
        logger.warning("KB authz: org %s has no librechat_container — fail-closed", org.zitadel_org_id)
        raise LibreChatIdentityError
    try:
        oid = ObjectId(librechat_user_id)
    except InvalidId as exc:
        logger.warning("KB authz: invalid ObjectId %s — fail-closed", librechat_user_id)
        raise LibreChatIdentityError from exc

    mongo_client: AsyncIOMotorClient | None = None
    try:
        mongo_client = AsyncIOMotorClient(settings.librechat_mongo_root_uri)
        mongo_user = await mongo_client[org.librechat_container]["users"].find_one({"_id": oid})
    except Exception as exc:
        logger.warning(
            "KB authz: MongoDB lookup failed for %s — fail-closed: %s", librechat_user_id, exc, exc_info=True
        )
        raise LibreChatIdentityError from exc
    finally:
        if mongo_client is not None:
            mongo_client.close()

    if mongo_user is None:
        logger.warning("KB authz: no LibreChat user found for ObjectId %s — fail-closed", librechat_user_id)
        raise LibreChatIdentityError

    zitadel_user_id = mongo_user.get("openidId") or mongo_user.get("openid_id") or mongo_user.get("sub")
    if not zitadel_user_id:
        logger.warning("KB authz: LibreChat user %s has no openidId/sub — fail-closed", librechat_user_id)
        raise LibreChatIdentityError

    portal_result = await db.execute(
        select(PortalUser).where(PortalUser.org_id == org.id, PortalUser.zitadel_user_id == zitadel_user_id)
    )
    user = portal_result.scalar_one_or_none()
    if user is None:
        logger.warning("KB authz: no portal user for zitadel_user_id %s — fail-closed", zitadel_user_id)
        raise LibreChatIdentityError

    if remember:
        user.librechat_user_id = librechat_user_id
        await db.commit()
    return user


async def has_knowledge_access(db: AsyncSession, user: PortalUser) -> bool:
    # Org-admins always get knowledge access.
    if user.role == "admin":
        return True
    return "knowledge" in await get_effective_products(user.zitadel_user_id, db)
