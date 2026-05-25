"""Widget conversation audit-trail writer.

Fire-and-forget logger that records every chat turn flowing through
``/partner/v1/chat/completions`` when the caller is a widget.

# @MX:ANCHOR: [AUTO] Audit-trail single source — tenant isolation invariant
# @MX:REASON: Every write opens its own tenant_scoped_session so the row
#             survives a roll-back in the request-scoped session, and
#             RLS Cat-D scopes by app.current_org_id.
# @MX:SPEC: SPEC-WIDGET-ACTIVITY-001

Layout:
- ``hash_audit_value(value, settings)``: hex digest helper for IP /
  User-Agent that uses the per-deploy ``widget_jwt_secret`` as a salt
  so two tenants on the same IP never collide on the same hash.
- ``record_widget_turn(...)``: idempotent UPSERT on
  ``widget_conversations`` keyed on (widget_id, session_key), then
  INSERT into ``widget_messages`` with the next sequence number.
  Returns nothing — failures are logged but never re-raised.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Literal

import structlog
from sqlalchemy import text

from app.core.config import settings as _global_settings
from app.core.database import cross_org_session, tenant_scoped_session

logger = structlog.get_logger()


def hash_audit_value(value: str | None, secret: str | None = None) -> str | None:
    """Return ``hex(HMAC-SHA256(secret, value))[:64]``.

    Salting with the deploy-wide ``widget_jwt_secret`` prevents two
    tenants on the same IP from sharing an identical ``ip_hash``.
    Returns ``None`` when ``value`` is falsy so the column stays NULL.
    """
    if not value:
        return None
    key = (secret or _global_settings.widget_jwt_secret or "klai").encode()
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:64]


def session_key_from_token(token: str | None, secret: str | None = None) -> str | None:
    """Legacy stable per-session-token identifier for pre-``jti`` JWTs."""
    if not token:
        return None
    return hash_audit_value(token, secret)


def session_key_from_claims(
    *,
    org_id: int | str | None,
    wgt_id: str | None,
    jti: str | None,
    secret: str | None = None,
) -> str | None:
    """Stable per-session identifier from already verified widget JWT claims."""
    if not org_id or not wgt_id or not jti:
        return None
    return hash_audit_value(f"{org_id}:{wgt_id}:{jti}", secret)


async def record_widget_turn(
    *,
    widget_id: str,  # UUID-as-string from widgets.id
    session_key: str,  # salted hash of widget JWT jti, stable per widget-load
    role: Literal["user", "assistant"],
    content: str,
    sources: list[dict[str, Any]] | None = None,
    ip_hash: str | None = None,
    user_agent_hash: str | None = None,
    language_detected: str | None = None,
    # REQ-2 (Finding B-2): persist the Origin header for audit visibility.
    # Truncated to 200 chars. NULL when Origin was absent.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-2
    loaded_origin: str | None = None,
    # REQ-15 (Finding B-11): mark conversations minted via the admin preview
    # session so widget_activity_stats can exclude them from visitor totals.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-15
    is_preview: bool = False,
) -> None:
    """Append one turn to a widget conversation, creating the
    conversation row on first call.

    REQ-14 (Finding B-7, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): ``org_id`` is
    derived server-side from the widgets row — never taken from the caller.
    A forged JWT or future admin-impersonation token cannot write into the
    wrong tenant's audit trail because the lookup uses the row that owns
    the widget id, not the caller's claimed org.

    Idempotent on the conversation row (UNIQUE (widget_id, session_key)).
    Sequence is computed inside the same transaction so concurrent
    user+assistant turns within one session stay ordered.

    # @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-14 (Finding B-7)
    """
    if not content.strip():
        return

    # REQ-14: derive org_id from widgets table (single source of truth).
    # Uses cross_org_session for the bare SELECT; the per-tenant INSERT
    # path below then opens tenant_scoped_session(org_id) which sets the
    # proper RLS GUC.
    try:
        async with cross_org_session() as lookup_db:
            row = (
                await lookup_db.execute(
                    text("SELECT org_id FROM widgets WHERE id = CAST(:widget_id AS uuid)"),
                    {"widget_id": widget_id},
                )
            ).first()
    except Exception:
        logger.exception("widget_audit_org_lookup_failed", widget_id=widget_id)
        return
    if row is None:
        logger.warning("widget_audit_widget_not_found", widget_id=widget_id)
        return
    org_id = int(row[0])

    truncated_query = content[:240] if role == "user" else None

    try:
        async with tenant_scoped_session(org_id) as db:
            upsert = await db.execute(
                text(
                    """
                    INSERT INTO widget_conversations
                        (org_id, widget_id, session_key, first_user_query,
                         ip_hash, user_agent_hash, language_detected,
                         loaded_origin, is_preview, last_message_at)
                    VALUES
                        (:org_id, CAST(:widget_id AS uuid), :session_key,
                         :first_user_query, :ip_hash, :user_agent_hash,
                         :language_detected, :loaded_origin, :is_preview, NOW())
                    ON CONFLICT (widget_id, session_key) DO UPDATE
                        SET last_message_at = NOW(),
                            -- never overwrite an existing first_user_query
                            first_user_query = COALESCE(
                                widget_conversations.first_user_query,
                                EXCLUDED.first_user_query
                            ),
                            language_detected = COALESCE(
                                EXCLUDED.language_detected,
                                widget_conversations.language_detected
                            )
                    RETURNING id, message_count
                    """
                ),
                {
                    "org_id": org_id,
                    "widget_id": widget_id,
                    "session_key": session_key,
                    "first_user_query": truncated_query,
                    "ip_hash": ip_hash,
                    "user_agent_hash": user_agent_hash,
                    "language_detected": language_detected,
                    "loaded_origin": loaded_origin[:200] if loaded_origin else None,
                    "is_preview": is_preview,
                },
            )
            row = upsert.first()
            if row is None:
                return
            conv_id, prior_count = row
            sequence = prior_count

            await db.execute(
                text(
                    """
                    INSERT INTO widget_messages
                        (conversation_id, org_id, role, content, sources, sequence)
                    VALUES
                        (:conversation_id, :org_id, :role, :content,
                         CAST(:sources AS jsonb), :sequence)
                    """
                ),
                {
                    "conversation_id": conv_id,
                    "org_id": org_id,
                    "role": role,
                    "content": content[:10000],  # REQ-8: clamp to 10000 chars (AC8.1)
                    "sources": None if sources is None else json.dumps(sources),
                    "sequence": sequence,
                },
            )
            await db.execute(
                text(
                    """
                    UPDATE widget_conversations
                       SET message_count = message_count + 1,
                           last_message_at = NOW()
                     WHERE id = :conv_id
                    """
                ),
                {"conv_id": conv_id},
            )
            await db.commit()
    except Exception:
        # Audit must never break the user's chat experience.
        logger.exception(
            "widget_audit_record_failed",
            widget_id=widget_id,
            role=role,
        )
