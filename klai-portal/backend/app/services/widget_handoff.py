from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.services.hubspot_custom_channel import ensure_channel_account, publish_incoming_message
from app.services.redis_client import get_redis_pool

logger = structlog.get_logger()

_PROVIDER = "hubspot"


def _clean_visitor_value(value: str | None, *, max_length: int) -> str | None:
    cleaned = value.strip() if value else ""
    return cleaned[:max_length] if cleaned else None


def build_handoff_context_text(
    *,
    summary: str | None,
    messages: list[dict[str, str]],
    visitor_name: str | None = None,
    visitor_email: str | None = None,
) -> str:
    parts: list[str] = ["Nieuwe live support overdracht vanuit Klai Webchat."]
    visitor_lines: list[str] = []
    if visitor_name:
        visitor_lines.append(f"Naam: {visitor_name}")
    if visitor_email:
        visitor_lines.append(f"E-mail: {visitor_email}")
    if visitor_lines:
        parts.append("Bezoeker:\n" + "\n".join(visitor_lines))
    if summary and summary.strip():
        parts.append(f"Samenvatting:\n{summary.strip()[:4000]}")

    transcript_lines: list[str] = []
    for message in messages[-20:]:
        role = message.get("role")
        content = (message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        label = "Bezoeker" if role == "user" else "Klai"
        transcript_lines.append(f"{label}: {content[:1200]}")

    if transcript_lines:
        parts.append("Laatste conversatie:\n" + "\n".join(transcript_lines))

    return "\n\n".join(parts)[:10000]


def _message_text_from_hubspot_payload(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, dict):
        return ""
    text_value = message.get("text")
    if isinstance(text_value, str) and text_value.strip():
        return text_value.strip()
    rich_text = message.get("richText")
    if isinstance(rich_text, str):
        return rich_text.strip()
    return ""


def _message_id_from_hubspot_payload(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    value = message.get("id")
    return str(value) if value else None


def _thread_id_from_hubspot_payload(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None
    value = message.get("conversationsThreadId") or message.get("threadId")
    return str(value) if value else None


def _agent_name_from_hubspot_payload(payload: dict[str, Any]) -> str | None:
    message = payload.get("message")
    if not isinstance(message, dict):
        return None

    candidates: list[Any] = [
        message.get("sender"),
        message.get("from"),
        message.get("createdBy"),
    ]
    senders = message.get("senders")
    if isinstance(senders, list):
        candidates.extend(senders)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("name", "fullName", "displayName", "email"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:120]
    return None


async def _publish_handoff_event(session_id: int, payload: dict[str, Any]) -> None:
    redis = await get_redis_pool()
    if redis is None:
        return
    await redis.publish(f"widget_handoff:{session_id}", json.dumps(payload))


async def _ensure_widget_conversation(
    db: AsyncSession,
    *,
    org_id: int,
    widget_uuid: str,
    session_key: str,
) -> int:
    result = await db.execute(
        text(
            """
            INSERT INTO widget_conversations
                (org_id, widget_id, session_key, last_message_at)
            VALUES
                (:org_id, CAST(:widget_uuid AS uuid), :session_key, NOW())
            ON CONFLICT (widget_id, session_key) DO UPDATE
                SET last_message_at = NOW()
            RETURNING id
            """
        ),
        {
            "org_id": org_id,
            "widget_uuid": widget_uuid,
            "session_key": session_key,
        },
    )
    row = result.first()
    if row is None:
        raise RuntimeError("Failed to create widget conversation for handoff")
    return int(row[0])


async def start_hubspot_handoff(
    db: AsyncSession,
    *,
    org_id: int,
    widget_public_id: str,
    session_key: str,
    summary: str | None,
    visitor_name: str | None,
    visitor_email: str | None,
    messages: list[dict[str, str]],
) -> dict[str, Any]:
    visitor_name = _clean_visitor_value(visitor_name, max_length=120)
    visitor_email = _clean_visitor_value(visitor_email, max_length=254)
    widget_row = (
        await db.execute(
            text(
                """
                SELECT id, name
                  FROM widgets
                 WHERE widget_id = :widget_public_id
                   AND org_id = :org_id
                   AND deleted_at IS NULL
                """
            ),
            {"widget_public_id": widget_public_id, "org_id": org_id},
        )
    ).first()
    if widget_row is None:
        raise RuntimeError("Widget not found for handoff")

    widget_uuid = str(widget_row[0])
    widget_name = str(widget_row[1])
    conversation_id = await _ensure_widget_conversation(
        db,
        org_id=org_id,
        widget_uuid=widget_uuid,
        session_key=session_key,
    )
    existing = (
        await db.execute(
            text(
                """
                SELECT id, status, integration_thread_id, hubspot_conversations_thread_id
                  FROM widget_handoff_sessions
                 WHERE provider = :provider
                   AND conversation_id = :conversation_id
                   AND status IN ('starting', 'active')
                 ORDER BY id DESC
                 LIMIT 1
                """
            ),
            {"provider": _PROVIDER, "conversation_id": conversation_id},
        )
    ).first()
    if existing is not None:
        return {
            "id": int(existing[0]),
            "status": str(existing[1]),
            "integration_thread_id": str(existing[2]),
            "hubspot_conversations_thread_id": str(existing[3]) if existing[3] else None,
        }

    integration_thread_id = f"klai-widget-{widget_public_id}-{conversation_id}"
    channel_account = await ensure_channel_account()
    handoff_context = build_handoff_context_text(
        summary=summary,
        messages=messages,
        visitor_name=visitor_name,
        visitor_email=visitor_email,
    )
    hubspot_visitor_name = visitor_name or f"{widget_name} visitor"

    inserted = (
        await db.execute(
            text(
                """
                INSERT INTO widget_handoff_sessions
                    (org_id, widget_id, conversation_id, provider, status,
                     integration_thread_id, hubspot_channel_account_id, summary)
                VALUES
                    (:org_id, CAST(:widget_uuid AS uuid), :conversation_id, :provider,
                     'starting', :integration_thread_id, :channel_account_id, :summary)
                RETURNING id
                """
            ),
            {
                "org_id": org_id,
                "widget_uuid": widget_uuid,
                "conversation_id": conversation_id,
                "provider": _PROVIDER,
                "integration_thread_id": integration_thread_id,
                "channel_account_id": channel_account.id,
                "summary": summary,
            },
        )
    ).first()
    if inserted is None:
        raise RuntimeError("Failed to create handoff session")
    session_id = int(inserted[0])

    try:
        message_payload = await publish_incoming_message(
            channel_account_id=channel_account.id,
            integration_thread_id=integration_thread_id,
            idempotency_id=str(uuid4()),
            text=handoff_context,
            visitor_id=f"{widget_public_id}:{conversation_id}",
            visitor_name=hubspot_visitor_name,
        )
    except Exception:
        await db.execute(
            text(
                """
                UPDATE widget_handoff_sessions
                   SET status = 'failed',
                       error_code = 'hubspot_publish_failed',
                       updated_at = NOW()
                 WHERE id = :session_id
                """
            ),
            {"session_id": session_id},
        )
        await db.commit()
        raise

    hubspot_thread_id = message_payload.get("conversationsThreadId")
    hubspot_message_id = message_payload.get("id")
    await db.execute(
        text(
            """
            UPDATE widget_handoff_sessions
               SET status = 'active',
                   hubspot_conversations_thread_id = :hubspot_thread_id,
                   activated_at = NOW(),
                   updated_at = NOW()
             WHERE id = :session_id
            """
        ),
        {
            "session_id": session_id,
            "hubspot_thread_id": str(hubspot_thread_id) if hubspot_thread_id else None,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO widget_handoff_messages
                (handoff_session_id, org_id, direction, content, hubspot_message_id, visible_to_visitor)
            VALUES
                (:session_id, :org_id, 'system', :content, :hubspot_message_id, false)
            """
        ),
        {
            "session_id": session_id,
            "org_id": org_id,
            "content": handoff_context,
            "hubspot_message_id": str(hubspot_message_id) if hubspot_message_id else None,
        },
    )
    await db.commit()
    return {
        "id": session_id,
        "status": "active",
        "integration_thread_id": integration_thread_id,
        "hubspot_conversations_thread_id": str(hubspot_thread_id) if hubspot_thread_id else None,
    }


async def send_handoff_visitor_message(
    db: AsyncSession,
    *,
    org_id: int,
    widget_public_id: str,
    session_key: str,
    content: str,
    visitor_name: str | None = None,
) -> dict[str, Any]:
    visitor_name = _clean_visitor_value(visitor_name, max_length=120)
    session = (
        await db.execute(
            text(
                """
                SELECT hs.id, hs.integration_thread_id, hs.hubspot_channel_account_id, wc.id
                  FROM widget_handoff_sessions hs
                  JOIN widget_conversations wc ON wc.id = hs.conversation_id
                  JOIN widgets w ON w.id = hs.widget_id
                 WHERE hs.provider = :provider
                   AND hs.org_id = :org_id
                   AND hs.status = 'active'
                   AND w.widget_id = :widget_public_id
                   AND wc.session_key = :session_key
                 ORDER BY hs.id DESC
                 LIMIT 1
                """
            ),
            {
                "provider": _PROVIDER,
                "org_id": org_id,
                "widget_public_id": widget_public_id,
                "session_key": session_key,
            },
        )
    ).first()
    if session is None:
        raise RuntimeError("No active handoff session")

    session_id = int(session[0])
    integration_thread_id = str(session[1])
    channel_account_id = str(session[2])
    conversation_id = int(session[3])
    idempotency_id = str(uuid4())
    message_payload = await publish_incoming_message(
        channel_account_id=channel_account_id,
        integration_thread_id=integration_thread_id,
        idempotency_id=idempotency_id,
        text=content,
        visitor_id=f"{widget_public_id}:{conversation_id}",
        visitor_name=visitor_name or "Klai visitor",
    )
    hubspot_message_id = message_payload.get("id")
    inserted = (
        await db.execute(
            text(
                """
                INSERT INTO widget_handoff_messages
                    (handoff_session_id, org_id, direction, content,
                     hubspot_message_id, integration_idempotency_id, visible_to_visitor)
                VALUES
                    (:session_id, :org_id, 'visitor', :content, :hubspot_message_id,
                     :idempotency_id, true)
                RETURNING id
                """
            ),
            {
                "session_id": session_id,
                "org_id": org_id,
                "content": content[:10000],
                "hubspot_message_id": str(hubspot_message_id) if hubspot_message_id else None,
                "idempotency_id": idempotency_id,
            },
        )
    ).first()
    await db.commit()
    return {
        "id": int(inserted[0]) if inserted else None,
        "handoff_session_id": session_id,
        "hubspot_message_id": str(hubspot_message_id) if hubspot_message_id else None,
    }


async def get_active_handoff_session_id(
    db: AsyncSession,
    *,
    org_id: int,
    widget_public_id: str,
    session_key: str,
) -> int | None:
    row = (
        await db.execute(
            text(
                """
                SELECT hs.id
                  FROM widget_handoff_sessions hs
                  JOIN widget_conversations wc ON wc.id = hs.conversation_id
                  JOIN widgets w ON w.id = hs.widget_id
                 WHERE hs.provider = :provider
                   AND hs.org_id = :org_id
                   AND hs.status = 'active'
                   AND w.widget_id = :widget_public_id
                   AND wc.session_key = :session_key
                 ORDER BY hs.id DESC
                 LIMIT 1
                """
            ),
            {
                "provider": _PROVIDER,
                "org_id": org_id,
                "widget_public_id": widget_public_id,
                "session_key": session_key,
            },
        )
    ).first()
    return int(row[0]) if row is not None else None


async def list_visible_handoff_messages(
    db: AsyncSession,
    *,
    handoff_session_id: int,
    after_id: int = 0,
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                """
                SELECT id, direction, content, hubspot_message_id, created_at, agent_name
                  FROM widget_handoff_messages
                 WHERE handoff_session_id = :handoff_session_id
                   AND visible_to_visitor = true
                   AND id > :after_id
                 ORDER BY id ASC
                 LIMIT 100
                """
            ),
            {"handoff_session_id": handoff_session_id, "after_id": after_id},
        )
    ).all()
    return [
        {
            "type": "message",
            "id": int(row[0]),
            "direction": str(row[1]),
            "content": str(row[2]),
            "hubspot_message_id": str(row[3]) if row[3] else None,
            "created_at": row[4].isoformat() if hasattr(row[4], "isoformat") else str(row[4]),
            "agent_name": str(row[5]) if row[5] else None,
        }
        for row in rows
    ]


async def record_hubspot_agent_reply(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    thread_id = _thread_id_from_hubspot_payload(payload)
    message_id = _message_id_from_hubspot_payload(payload)
    content = _message_text_from_hubspot_payload(payload)
    agent_name = _agent_name_from_hubspot_payload(payload)
    if not thread_id or not message_id or not content:
        return {"status": "ignored", "reason": "missing_required_fields"}

    # HubSpot webhooks are not tenant-authenticated requests. We must first map
    # the globally unique HubSpot thread id to a Klai tenant, then switch back to
    # normal tenant RLS before writing the visible agent message.
    await db.execute(text("SELECT set_config('app.cross_org_admin', 'true', false)"))
    try:
        session = (
            await db.execute(
                text(
                    """
                    SELECT id, org_id
                      FROM widget_handoff_sessions
                     WHERE provider = :provider
                       AND hubspot_conversations_thread_id = :thread_id
                       AND status = 'active'
                     ORDER BY id DESC
                     LIMIT 1
                    """
                ),
                {"provider": _PROVIDER, "thread_id": thread_id},
            )
        ).first()
    finally:
        await db.execute(text("SELECT set_config('app.cross_org_admin', '', false)"))
    if session is None:
        logger.info(
            "hubspot_custom_channel_webhook_unmapped",
            conversations_thread_id=thread_id,
            message_id=message_id,
        )
        return {"status": "ignored", "reason": "unmapped_thread"}

    session_id = int(session[0])
    org_id = int(session[1])
    await set_tenant(db, org_id)
    inserted = (
        await db.execute(
            text(
                """
                INSERT INTO widget_handoff_messages
                    (handoff_session_id, org_id, direction, content, hubspot_message_id, visible_to_visitor, agent_name)
                VALUES
                    (:session_id, :org_id, 'agent', :content, :hubspot_message_id, true, :agent_name)
                ON CONFLICT (hubspot_message_id) WHERE hubspot_message_id IS NOT NULL DO NOTHING
                RETURNING id, created_at
                """
            ),
            {
                "session_id": session_id,
                "org_id": org_id,
                "content": content[:10000],
                "hubspot_message_id": message_id,
                "agent_name": agent_name,
            },
        )
    ).first()
    await db.commit()
    if inserted is None:
        return {"status": "duplicate", "handoff_session_id": session_id}

    event = {
        "type": "agent_message",
        "id": int(inserted[0]),
        "handoff_session_id": session_id,
        "content": content[:10000],
        "hubspot_message_id": message_id,
        "agent_name": agent_name,
    }
    try:
        await _publish_handoff_event(session_id, event)
    except Exception:
        logger.exception("widget_handoff_redis_publish_failed", handoff_session_id=session_id)
    return {"status": "recorded", **event}
