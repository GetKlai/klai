"""Recent LibreChat conversations for a feedback reporter.

Feedback submitted from ``/app/chat`` lacks the conversation the reporter was
looking at: the portal embeds LibreChat in a cross-origin iframe, so the
portal page URL never contains a conversation id. As enrichment we look up
the reporter's most recent conversations in the tenant's LibreChat MongoDB
(users are matched on ``openidId`` = the Zitadel ``sub``).

The result is a RECENCY-BASED CANDIDATE LIST, not a proven link: a reporter
can complain about an older conversation without touching it, in which case
``updatedAt`` stays old and the conversation may not appear here. Consumers
(admin prompt, triage LLM) must treat these as candidates.

Connection note: this uses the Mongo root credentials because portal-api has
no runtime access to per-tenant Mongo passwords (those live in the LibreChat
container env only). Scoping is enforced in code instead: the database name
is derived from the authenticated caller's org slug and the query filters on
the caller's own ``openidId``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pymongo
import structlog

from app.core.config import settings
from app.core.provisioning_names import provisioning_names_for_slug

logger = structlog.get_logger()

MAX_CONVERSATIONS = 3
_TITLE_MAX_LENGTH = 200
_MONGO_TIMEOUT_MS = 2000


def _sync_recent_conversations(
    db_name: str,
    zitadel_user_id: str,
    limit: int,
) -> list[dict] | None:
    """Return the reporter's most recent LibreChat conversations, or ``None``
    when no LibreChat user exists for this identity (user never opened chat)."""
    with pymongo.MongoClient(
        host=settings.mongodb_container_name,
        port=27017,
        username=settings.mongo_root_username,
        password=settings.mongo_root_password,
        authSource="admin",
        serverSelectionTimeoutMS=_MONGO_TIMEOUT_MS,
        connectTimeoutMS=_MONGO_TIMEOUT_MS,
        socketTimeoutMS=_MONGO_TIMEOUT_MS,
    ) as client:
        db = client[db_name]
        user = db.users.find_one({"openidId": zitadel_user_id}, {"_id": 1})
        if user is None:
            return None
        cursor = (
            db.conversations.find(
                # LibreChat stores the owning user's ``_id`` as a string.
                {"user": str(user["_id"])},
                {"conversationId": 1, "title": 1, "model": 1, "createdAt": 1, "updatedAt": 1, "_id": 0},
            )
            .sort("updatedAt", pymongo.DESCENDING)
            .limit(limit)
        )
        return list(cursor)


def _iso(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    # MongoDB stores datetimes in UTC but pymongo returns them naive by
    # default; attach UTC so the timestamp is unambiguous next to the
    # (timezone-aware) feedback submission time.
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _as_str(value: object, max_length: int | None = None) -> str | None:
    # Mongo fields are unvalidated external data; on schema drift (ObjectId,
    # nested document) a non-string value would later fail PostgreSQL JSONB
    # serialization outside our error handling. Only accept plain strings.
    if not isinstance(value, str):
        return None
    return value[:max_length] if max_length else value


def _conversation_entry(raw: dict, chat_origin: str) -> dict[str, str | None]:
    conversation_id = _as_str(raw.get("conversationId"))
    return {
        "conversation_id": conversation_id,
        "title": _as_str(raw.get("title"), _TITLE_MAX_LENGTH),
        "model": _as_str(raw.get("model")),
        "url": f"{chat_origin}/c/{conversation_id}" if conversation_id else None,
        "created_at": _iso(raw.get("createdAt")),
        "updated_at": _iso(raw.get("updatedAt")),
    }


async def recent_chat_conversations(
    org_slug: str,
    zitadel_user_id: str,
) -> list[dict[str, str | None]] | None:
    """Best-effort lookup of the reporter's recent conversations.

    Returns ``None`` on any failure or when the reporter has no LibreChat
    account; feedback handling must never fail on this enrichment.
    """
    try:
        names = provisioning_names_for_slug(org_slug, domain=settings.domain)
        raw = await asyncio.to_thread(
            _sync_recent_conversations,
            names.mongodb_database,
            zitadel_user_id,
            MAX_CONVERSATIONS,
        )
    except Exception:
        logger.warning("feedback_chat_context_lookup_failed", org_slug=org_slug, exc_info=True)
        return None
    if raw is None:
        logger.info("feedback_chat_context_no_librechat_user", org_slug=org_slug)
        return None
    return [_conversation_entry(entry, names.chat_origin) for entry in raw]
