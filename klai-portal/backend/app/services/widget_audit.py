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


async def find_conversation_id(*, widget_id: str, session_key: str) -> tuple[int, bool] | None:
    """Return ``(conversation_id, is_test)`` for (widget_id, session_key).

    Same identification as ``record_widget_turn``: the owning org is derived
    from the widgets row (REQ-14), never from the caller, and the read then
    runs on that org's tenant-scoped session so Cat-D RLS applies. ``is_test``
    rides along so a fire-and-forget gap write can skip a conversation a
    reviewer already marked as a test message
    (SPEC-KNOWLEDGE-ACTIVITY-001, see ``_schedule_gap_event`` below).

    Returns ``None`` when the row does not exist yet. That is normal, not an
    error: the audit write is fire-and-forget, so a gap event fired from the
    same turn can legitimately arrive first
    (SPEC-KNOWLEDGE-ACTIVITY-001 §4.5).

    DB failures are NOT swallowed here — unlike the audit writer, this
    helper is called by readers that decide what a missing provenance costs
    them (see ``_schedule_gap_event`` in app.services.partner_chat).
    """
    async with cross_org_session() as lookup_db:
        row = (
            await lookup_db.execute(
                text("SELECT org_id FROM widgets WHERE id = CAST(:widget_id AS uuid)"),
                {"widget_id": widget_id},
            )
        ).first()
    if row is None:
        return None
    org_id = int(row[0])

    async with tenant_scoped_session(org_id) as db:
        conv = (
            await db.execute(
                text(
                    """
                    SELECT id, is_test FROM widget_conversations
                     WHERE widget_id = CAST(:widget_id AS uuid)
                       AND session_key = :session_key
                    """
                ),
                {"widget_id": widget_id, "session_key": session_key},
            )
        ).first()
    return None if conv is None else (int(conv.id), bool(conv.is_test))


async def record_widget_turn(
    *,
    widget_id: str,  # UUID-as-string from widgets.id
    session_key: str,  # salted hash of widget JWT jti, stable per widget-load
    role: Literal["user", "assistant"],
    content: str,
    sources: list[dict[str, Any]] | None = None,
    # Client-generated per-turn identifier echoed by the widget chat request
    # (``widget_turn_id``). Stored on the assistant row so
    # POST /partner/v1/widget/feedback can address this exact answer without
    # ever exposing widget_messages.id to the browser.
    turn_id: str | None = None,
    # Retrieval certainty of an assistant answer (top_score, band, gap_type,
    # sources_count, refused, broad_mode, language, model) — the material a
    # later rating-vs-certainty calibration needs, and which only exists while
    # the answer is generated. Written on the assistant row only: the DB CHECK
    # rejects signals on a visitor row, and the caller has none to give there.
    # @MX:SPEC: SPEC-KNOWLEDGE-ACTIVITY-001 §4.1
    answer_signals: dict[str, Any] | None = None,
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
    # Contact details from the widget's pre-chat step, sent on every turn by
    # the client and written to the conversation row rather than into the
    # message body. Latest non-empty value wins so a visitor who corrects a
    # typo is not stuck with the first attempt; skipping the step leaves both
    # NULL and touches nothing.
    visitor_name: str | None = None,
    visitor_email: str | None = None,
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
    # Browser-supplied, so clamp to the column widths here as well as in the
    # request model; an empty field must land as NULL and not as "".
    clean_visitor_name = (visitor_name or "").strip()[:120] or None
    clean_visitor_email = (visitor_email or "").strip()[:254] or None

    try:
        async with tenant_scoped_session(org_id) as db:
            upsert = await db.execute(
                text(
                    """
                    INSERT INTO widget_conversations
                        (org_id, widget_id, session_key, first_user_query,
                         ip_hash, user_agent_hash, language_detected,
                         loaded_origin, is_preview, visitor_name,
                         visitor_email, last_message_at)
                    VALUES
                        (:org_id, CAST(:widget_id AS uuid), :session_key,
                         :first_user_query, :ip_hash, :user_agent_hash,
                         :language_detected, :loaded_origin, :is_preview,
                         :visitor_name, :visitor_email, NOW())
                    ON CONFLICT (widget_id, session_key) DO UPDATE
                        SET last_message_at = NOW(),
                            visitor_name = COALESCE(
                                EXCLUDED.visitor_name,
                                widget_conversations.visitor_name
                            ),
                            visitor_email = COALESCE(
                                EXCLUDED.visitor_email,
                                widget_conversations.visitor_email
                            ),
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
                    "visitor_name": clean_visitor_name,
                    "visitor_email": clean_visitor_email,
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
                        (conversation_id, org_id, role, content, sources,
                         sequence, turn_id, answer_signals)
                    VALUES
                        (:conversation_id, :org_id, :role, :content,
                         CAST(:sources AS jsonb), :sequence, :turn_id,
                         CAST(:answer_signals AS jsonb))
                    """
                ),
                {
                    "conversation_id": conv_id,
                    "org_id": org_id,
                    "role": role,
                    "content": content[:10000],  # REQ-8: clamp to 10000 chars (AC8.1)
                    "sources": None if sources is None else json.dumps(sources),
                    "sequence": sequence,
                    "turn_id": turn_id,
                    # Assistant-only by the column CHECK, so a user turn must
                    # arrive here as NULL even if a caller passed signals.
                    "answer_signals": (
                        None if role != "assistant" or answer_signals is None else json.dumps(answer_signals)
                    ),
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
