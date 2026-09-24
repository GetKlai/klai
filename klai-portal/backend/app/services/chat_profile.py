"""What kind of chat turn this is, resolved once per request.

One pipeline serves the website widget, the partner API and the internal chat
(LibreChat). The fields below are the only ways those surfaces may differ, and
each maps to a reason in docs/architecture/chat-quality-history-and-plan.md
§7.2: who is asking (``surface``, ``user_id``), what may be used beyond the
knowledge base (``kb_mode``), what is searched (``kb_scope``, ``kb_slugs``),
and how the answer is delivered (``stream_live``). A difference between
surfaces that is not one of these is a duplicate to merge, not a new field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException, status
from sqlalchemy import select

from app.models.portal import PortalOrg, PortalUser
from app.services.internal_chat_identity import LibreChatIdentityError, has_knowledge_access, resolve_librechat_user

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.partner_dependencies import PartnerAuthContext

Surface = Literal["widget", "partner", "internal"]
# strict: answer from the knowledge base only; open: the knowledge base plus
# general knowledge, labelled as such; general: no retrieval at all.
KbMode = Literal["strict", "open", "general"]
KbScope = Literal["org", "personal", "both"]


@dataclass(frozen=True, slots=True)
class ChatProfile:
    surface: Surface
    kb_mode: KbMode = "strict"
    kb_scope: KbScope = "org"
    # None searches every knowledge base the caller may read.
    kb_slugs: tuple[str, ...] | None = None
    # Zitadel subject of the employee; only the internal surface has one.
    user_id: str | None = None
    # Stream tokens as they arrive. Open and general internal turns do: an Open
    # turn may use general knowledge and a general turn searches nothing, so
    # neither waits on the grounding check. Every other turn is held until the
    # check has decided what to show.
    stream_live: bool = False


_FORBIDDEN = {"error": {"type": "permission_error", "message": "Insufficient permissions"}}


async def resolve_chat_profile(db: AsyncSession, auth: PartnerAuthContext, librechat_user_id: object) -> ChatProfile:
    """Profile for one request; ``librechat_user_id`` is the body's OpenAI ``user`` field.

    Only a key with ``internal_chat`` reads that field. Any key without it keeps
    the widget or partner profile, whatever the body says, so a partner cannot
    widen its own scope by sending ``user``. For an internal key the field must
    resolve to an active user of the key's org, otherwise 403.
    """
    if str(auth.key_id).startswith("wgt_"):
        return ChatProfile(surface="widget")
    if not auth.permissions.get("internal_chat"):
        return ChatProfile(surface="partner")

    if not isinstance(librechat_user_id, str) or not librechat_user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    org = (await db.execute(select(PortalOrg).where(PortalOrg.id == auth.org_id))).scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)
    try:
        user = await resolve_librechat_user(db, org, librechat_user_id)
    except LibreChatIdentityError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN) from exc
    if user.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=_FORBIDDEN)

    return _internal_profile(user, knowledge=await has_knowledge_access(db, user))


def _internal_profile(user: PortalUser, *, knowledge: bool) -> ChatProfile:
    # kb_slugs_filter is tri-state: None = every org KB, [] = none, list = subset.
    slugs = user.kb_slugs_filter
    searchable = knowledge and user.kb_retrieval_enabled and (user.kb_personal_enabled or slugs != [])
    mode: KbMode
    scope: KbScope
    if searchable:
        mode = "strict" if user.kb_narrow else "open"
        scope = "personal" if slugs == [] else "both" if user.kb_personal_enabled else "org"
        kb_slugs = tuple(slugs) if slugs else None
    elif user.kb_narrow:
        # Strict with nothing to search: an empty scope, so the turn refuses
        # instead of turning into a general answer (what the LiteLLM hook does).
        mode, scope, kb_slugs = "strict", "org", ()
    else:
        mode, scope, kb_slugs = "general", "org", None
    return ChatProfile(
        surface="internal",
        kb_mode=mode,
        kb_scope=scope,
        kb_slugs=kb_slugs,
        user_id=user.zitadel_user_id,
        stream_live=mode in ("open", "general"),
    )
