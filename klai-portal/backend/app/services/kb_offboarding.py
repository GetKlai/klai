"""KB-offboarding orchestrator.

SPEC-PORTAL-KB-OWNERSHIP-001 Phase 3 (REQ-2.x).

The orchestrator drives two operations on user-offboarding:

1. ``compute_offboard_preview(target_user_id, perms, db)`` — returns the
   set of org-owned KBs the target user is the SOLE owner of, plus all
   personal KBs the user owns, plus counts of partner API keys and MCP
   tokens that will be handed over or revoked. The admin uses the preview to choose a
   ``KbDisposition`` per KB before triggering ``offboard``.

2. ``apply_dispositions(target_user_id, dispositions, actor, db)`` —
   executes the chosen dispositions inside the offboard DB transaction:

   - ``action='transfer'`` (org KBs only): updates ``created_by`` to the
     receiving user, removes the offboarded user's
     ``portal_user_kb_access`` row (if any), upserts an owner-role row
     for the new user. Emits ``kb.transferred`` audit + structlog event.
   - ``action='delete'``: runs the same 3-step external-call chain as
     ``delete_app_knowledge_base`` (docs-app → knowledge-ingest →
     portal-DB). Emits ``kb.admin_deleted`` (org KB) or
     ``kb.personal_purged_on_offboard`` (personal KB) with
     ``meta.reason='offboarding'``.

3. ``transfer_user_api_keys`` hands every ``partner_api_keys`` row the
   offboarded user created to a recipient (the acting admin unless the
   admin picks another, checked by ``check_api_keys_recipient``), so
   integrations keep working; ``revoke_user_mcp_tokens`` soft-revokes
   every active ``portal_mcp_tokens`` row of the offboarded user (REQ-2.7).

Failure in any step raises and rolls back the entire transaction —
including the eventual ``user.status = 'offboarded'`` flip in the
caller. This is REQ-2.2 / AC-10 fail-loud semantics: a half-offboarded
user (memberships gone but KB transfer aborted, say) is worse than no
offboarding at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

import structlog
from fastapi import HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import set_tenant
from app.core.profiles import ProfileRole
from app.models.knowledge_bases import PortalKnowledgeBase, PortalUserKBAccess
from app.models.mcp_oauth import PortalMcpToken
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.models.portal import PortalOrg, PortalUser
from app.services import docs_client, knowledge_ingest_client
from app.services.access import is_personal_kb
from app.services.audit import log_event

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class OffboardPreviewKb(BaseModel):
    """One row in the offboard-preview's KB lists."""

    kb_id: int
    slug: str
    name: str
    owner_type: Literal["org", "user"]
    role_count: int = Field(
        default=1,
        description=(
            "Number of explicit owner-role grants on this KB. For org KBs, "
            "1 means the offboarded user is the only owner; >1 means "
            "co-owners exist (and the KB is NOT in the preview's "
            "solely-owned list)."
        ),
    )


class OffboardPreview(BaseModel):
    """Response shape for ``GET /admin/users/{id}/offboard-preview``.

    ``org_kbs_solely_owned`` lists only KBs where the offboarded user is
    the SOLE owner (creator AND no other portal_user_kb_access rows with
    role='owner'). Co-owned KBs are excluded — losing one of N owners
    needs no admin disposition.

    ``personal_kbs`` lists every KB with ``owner_type='user'`` and
    ``owner_user_id == target_user_id``. Personal KBs cannot be
    transferred (REQ-2.4) and are always purged on offboard (D2).
    """

    org_kbs_solely_owned: list[OffboardPreviewKb]
    personal_kbs: list[OffboardPreviewKb]
    api_keys_count: int = Field(description="REQ-2.1b — partner_api_keys created by the offboarded user.")
    mcp_tokens_count: int = Field(description="REQ-2.1b — active portal_mcp_tokens owned by the offboarded user.")


class UserDeletePreview(BaseModel):
    """Response shape for ``GET /admin/users/{id}/delete-preview``.

    Delete differs from offboard: every org KB created by the user must be
    explicitly transferred or deleted so ``created_by`` never points at a
    hard-deleted user row. Personal KBs stay delete-only.
    """

    org_kbs_created: list[OffboardPreviewKb]
    personal_kbs: list[OffboardPreviewKb]
    api_keys_count: int = Field(description="Partner API keys created by the deleted user.")
    mcp_tokens_count: int = Field(description="Active portal_mcp_tokens owned by the deleted user.")


class KbDisposition(BaseModel):
    """One row in the admin's offboard request body.

    For ``action='transfer'`` (org KBs only), ``transfer_to`` MUST be a
    valid zitadel_user_id of a remaining org member. The orchestrator
    refuses to transfer to the offboarded user themselves or to an
    unknown user_id.
    """

    kb_id: int
    action: Literal["transfer", "delete"]
    transfer_to: str | None = None

    @model_validator(mode="after")
    def _transfer_to_required_for_transfer(self) -> KbDisposition:
        if self.action == "transfer" and not self.transfer_to:
            raise ValueError("transfer_to is required when action='transfer'")
        if self.action == "delete" and self.transfer_to:
            raise ValueError("transfer_to must be omitted when action='delete'")
        return self


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


async def compute_offboard_preview(
    target_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> OffboardPreview:
    """Build the offboard-preview for the target user.

    Two queries on portal_knowledge_bases (Cat-D RLS-strict) — caller MUST
    have run set_tenant via the auth dep before calling this. The audit
    allowlist in tests/test_rls_callsite_audit.py covers this.
    """
    # Org-KBs where the target user is the creator (implicit owner).
    org_kbs_creator_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org_id,
            PortalKnowledgeBase.owner_type == "org",
            PortalKnowledgeBase.created_by == target_user_id,
        )
    )
    candidate_org_kbs = list(org_kbs_creator_result.scalars().all())

    # For each candidate org KB, count owner-role grants from OTHER users.
    # If 0 other-owner grants exist: target is solely-owned (include in preview).
    # If >0 other-owner grants exist: skip — co-ownership means no disposition needed.
    solely_owned: list[OffboardPreviewKb] = []
    for kb in candidate_org_kbs:
        other_owners_result = await db.execute(
            select(func.count(PortalUserKBAccess.id))
            .where(PortalUserKBAccess.kb_id == kb.id)
            .where(PortalUserKBAccess.role == "owner")
            .where(PortalUserKBAccess.user_id != target_user_id)
        )
        other_owner_count = other_owners_result.scalar_one() or 0
        if other_owner_count == 0:
            solely_owned.append(
                OffboardPreviewKb(
                    kb_id=kb.id,
                    slug=kb.slug,
                    name=kb.name,
                    owner_type="org",
                    role_count=1,
                )
            )

    personal_kb_rows = await _personal_kb_preview_rows(target_user_id, org_id, db)
    api_keys_count, mcp_tokens_count = await _credential_counts(target_user_id, org_id, db)

    return OffboardPreview(
        org_kbs_solely_owned=solely_owned,
        personal_kbs=personal_kb_rows,
        api_keys_count=api_keys_count,
        mcp_tokens_count=mcp_tokens_count,
    )


async def compute_user_delete_preview(
    target_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> UserDeletePreview:
    """Build the delete-preview for a hard user delete.

    Includes every org KB the user created, not only solely-owned KBs. A hard
    delete removes the portal user row, so every ``created_by`` reference must
    be handled explicitly.
    """
    await set_tenant(db, org_id)

    org_kbs_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org_id,
            PortalKnowledgeBase.owner_type == "org",
            PortalKnowledgeBase.created_by == target_user_id,
        )
    )

    org_kb_rows: list[OffboardPreviewKb] = []
    for kb in org_kbs_result.scalars().all():
        owner_count_result = await db.execute(
            select(func.count(PortalUserKBAccess.id))
            .where(PortalUserKBAccess.kb_id == kb.id)
            .where(PortalUserKBAccess.role == "owner")
        )
        explicit_owner_count = owner_count_result.scalar_one() or 0
        org_kb_rows.append(
            OffboardPreviewKb(
                kb_id=kb.id,
                slug=kb.slug,
                name=kb.name,
                owner_type="org",
                role_count=max(1, explicit_owner_count),
            )
        )

    personal_kb_rows = await _personal_kb_preview_rows(target_user_id, org_id, db)
    api_keys_count, mcp_tokens_count = await _credential_counts(target_user_id, org_id, db)

    return UserDeletePreview(
        org_kbs_created=org_kb_rows,
        personal_kbs=personal_kb_rows,
        api_keys_count=api_keys_count,
        mcp_tokens_count=mcp_tokens_count,
    )


async def _personal_kb_preview_rows(
    target_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> list[OffboardPreviewKb]:
    await set_tenant(db, org_id)

    personal_kbs_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.org_id == org_id,
            PortalKnowledgeBase.owner_type == "user",
            PortalKnowledgeBase.owner_user_id == target_user_id,
        )
    )
    return [
        OffboardPreviewKb(
            kb_id=kb.id,
            slug=kb.slug,
            name=kb.name,
            owner_type="user",
            role_count=1,
        )
        for kb in personal_kbs_result.scalars().all()
    ]


async def _credential_counts(
    target_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> tuple[int, int]:
    api_keys_count_result = await db.execute(
        select(func.count(PartnerAPIKey.id)).where(
            PartnerAPIKey.org_id == org_id,
            PartnerAPIKey.created_by == target_user_id,
        )
    )
    api_keys_count = api_keys_count_result.scalar_one() or 0

    user_id_result = await db.execute(
        select(PortalUser.id).where(
            PortalUser.zitadel_user_id == target_user_id,
            PortalUser.org_id == org_id,
        )
    )
    portal_user_pk = user_id_result.scalar_one_or_none()
    mcp_tokens_count = 0
    if portal_user_pk is not None:
        mcp_count_result = await db.execute(
            select(func.count(PortalMcpToken.id)).where(
                PortalMcpToken.user_id == portal_user_pk,
                PortalMcpToken.revoked_at.is_(None),
            )
        )
        mcp_tokens_count = mcp_count_result.scalar_one() or 0

    return api_keys_count, mcp_tokens_count


# ---------------------------------------------------------------------------
# Apply dispositions
# ---------------------------------------------------------------------------


async def apply_dispositions(
    target_user_id: str,
    dispositions: list[KbDisposition],
    actor_user_id: str,
    org: PortalOrg,
    db: AsyncSession,
) -> None:
    """Execute the admin's KB dispositions for the offboarded user.

    REQ-2.2 — runs inside the offboard DB transaction. Caller is
    responsible for committing AFTER both ``apply_dispositions`` AND its
    own user-status flip succeed. Any HTTPException raised here aborts
    the entire offboard, leaving the user ``active``.

    REQ-2.5 — caller is responsible for verifying that EVERY KB in the
    preview's ``org_kbs_solely_owned`` and ``personal_kbs`` has a
    matching disposition. This function does not re-validate
    completeness; missing dispositions surface as the corresponding KB
    being silently retained, which is a worse failure mode than 400.
    """
    for disposition in dispositions:
        kb = await _load_kb_or_404(disposition.kb_id, org.id, db)
        if disposition.action == "transfer":
            await _do_transfer(kb, disposition.transfer_to or "", actor_user_id, org.id, db)
        else:  # delete
            await _do_delete(kb, target_user_id, actor_user_id, org, db)


async def _load_kb_or_404(kb_id: int, org_id: int, db: AsyncSession) -> PortalKnowledgeBase:
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.id == kb_id,
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Disposition references unknown kb_id={kb_id}",
        )
    return kb


def _bad_recipient(error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"error_code": error_code, "message": message},
    )


async def _active_member_or_400(user_id: str, org_id: int, db: AsyncSession) -> PortalUser:
    """Whatever offboarding hands over goes to an active member of the same org.

    A recipient in another tenant, or one who already left, would leave the
    knowledge base or API key with an owner nobody can act as.
    """
    result = await db.execute(
        select(PortalUser).where(
            PortalUser.zitadel_user_id == user_id,
            PortalUser.org_id == org_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise _bad_recipient("transfer_recipient_not_member", f"User {user_id} is not a member of this org")
    if member.status != "active":
        raise _bad_recipient("transfer_recipient_not_active", f"User {user_id} is not active (status={member.status})")
    return member


async def _do_transfer(
    kb: PortalKnowledgeBase,
    new_owner_id: str,
    actor_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> None:
    """REQ-2.3 + REQ-2.4 — transfer ownership of an org KB to a new user.

    Personal KBs cannot be transferred (REQ-2.4). The check is
    is_personal_kb-based (single source of truth) so the gate moves with
    any future schema change.

    On the new-owner side: upserts a portal_user_kb_access row with
    role='owner'. We don't try to be clever about an existing access row
    for the new owner — if one exists, we delete it first and insert
    fresh, so the audit trail (granted_at, granted_by) reflects the
    transfer event.
    """
    if is_personal_kb(kb):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Personal knowledge bases cannot be transferred to another person",
        )

    if not new_owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"transfer_to is required for kb_id={kb.id}",
        )

    await _active_member_or_400(new_owner_id, org_id, db)

    previous_owner = kb.created_by
    kb.created_by = new_owner_id

    # Drop the old owner's explicit access row (if any) — the offboarded
    # user is leaving the workspace, no point keeping a dangling grant.
    await db.execute(
        delete(PortalUserKBAccess).where(
            PortalUserKBAccess.kb_id == kb.id,
            PortalUserKBAccess.user_id == previous_owner,
        )
    )
    # Upsert owner row for the new user. Delete-then-insert so granted_at
    # carries the transfer timestamp, not a stale earlier grant.
    await db.execute(
        delete(PortalUserKBAccess).where(
            PortalUserKBAccess.kb_id == kb.id,
            PortalUserKBAccess.user_id == new_owner_id,
        )
    )
    db.add(
        PortalUserKBAccess(
            kb_id=kb.id,
            user_id=new_owner_id,
            org_id=org_id,
            role="owner",
            granted_by=actor_user_id,
        )
    )

    await log_event(
        org_id=org_id,
        actor=actor_user_id,
        action="kb.transferred",
        resource_type="kb",
        resource_id=str(kb.id),
        details={
            "from_user": previous_owner,
            "to_user": new_owner_id,
            "kb_name": kb.name,
            "kb_slug": kb.slug,
            "reason": "offboarding",
        },
    )
    logger.info(
        "kb_transferred",
        org_id=org_id,
        actor_user_id=actor_user_id,
        kb_id=kb.id,
        kb_slug=kb.slug,
        from_user=previous_owner,
        to_user=new_owner_id,
        reason="offboarding",
    )


async def _do_delete(
    kb: PortalKnowledgeBase,
    target_user_id: str,
    actor_user_id: str,
    org: PortalOrg,
    db: AsyncSession,
) -> None:
    """Delete a KB during offboarding.

    Mirrors ``delete_app_knowledge_base``'s 3-step chain so the
    failure-mode (docs failure aborts before portal-DB delete) is
    identical. Audit-event differs based on owner_type:
      - org KB ➜ ``kb.admin_deleted`` with reason='offboarding'
      - personal KB ➜ ``kb.personal_purged_on_offboard``
    """
    # Step 1: docs-app cleanup (only when it has a Gitea repo).
    if kb.gitea_repo_slug or kb.docs_enabled:
        await docs_client.deprovision_kb(org.slug, kb.slug)

    # Step 2: knowledge-ingest cleanup.
    await knowledge_ingest_client.delete_kb(org.zitadel_org_id, kb.slug)

    # Step 3: audit BEFORE portal-DB delete so the row id / metadata are
    # captured even if some downstream step throws after.
    if is_personal_kb(kb):
        action = "kb.personal_purged_on_offboard"
    else:
        action = "kb.admin_deleted"
    await log_event(
        org_id=org.id,
        actor=actor_user_id,
        action=action,
        resource_type="kb",
        resource_id=str(kb.id),
        details={
            "previous_owner": kb.created_by,
            "kb_name": kb.name,
            "kb_slug": kb.slug,
            "reason": "offboarding",
            "owner_type": kb.owner_type,
            "target_user_id": target_user_id,
        },
    )
    logger.info(
        action.replace(".", "_"),
        org_id=org.id,
        actor_user_id=actor_user_id,
        kb_id=kb.id,
        kb_slug=kb.slug,
        previous_owner=kb.created_by,
        target_user_id=target_user_id,
        reason="offboarding",
    )

    # Step 4: portal-DB delete (cascades access rows).
    await db.delete(kb)


# ---------------------------------------------------------------------------
# Credentials: API keys are handed over, MCP tokens are revoked
# ---------------------------------------------------------------------------


async def check_api_keys_recipient(
    recipient_id: str,
    leaving_user_id: str,
    org_id: int,
    db: AsyncSession,
) -> None:
    """Refuse a new owner for the leaving user's partner API keys who could not hold them.

    Called before anything in the offboard or delete is mutated, because the
    KB dispositions call external services that a database rollback does not
    undo, and the delete path removes the Zitadel identity before it reaches
    the credentials step.
    """
    if recipient_id == leaving_user_id:
        raise _bad_recipient(
            "api_keys_recipient_is_leaving_user", "API keys cannot be handed over to the user who is leaving"
        )
    recipient = await _active_member_or_400(recipient_id, org_id, db)
    # Every /api/admin/api-keys route requires get_caller_at_least(ProfileRole.ADMIN),
    # so an owner below admin could neither rotate nor revoke the keys they received.
    if recipient.role != ProfileRole.ADMIN:
        raise _bad_recipient(
            "api_keys_recipient_not_admin", f"User {recipient_id} cannot manage API keys (role={recipient.role})"
        )


async def transfer_user_api_keys(
    target_user_id: str,
    recipient_id: str,
    org_id: int,
    db: AsyncSession,
) -> int:
    """Make ``recipient_id`` the creator of every partner API key the leaving user created.

    The key row keeps its hash, permissions, rate limit and org KB access, so
    every integration on it keeps authenticating. Access rows on personal
    knowledge bases are dropped first: the lookup in partner_dependencies
    admits a personal KB only when ``owner_user_id == created_by``, so after
    the handover a row on the recipient's personal KB would open it to the
    integration, and a row on the leaving user's personal KB has nothing left
    to grant. The caller validates the recipient with ``check_api_keys_recipient``.
    Returns the number of keys handed over.
    """
    user_keys = select(PartnerAPIKey.id).where(
        PartnerAPIKey.org_id == org_id,
        PartnerAPIKey.created_by == target_user_id,
    )
    await db.execute(
        delete(PartnerApiKeyKbAccess).where(
            PartnerApiKeyKbAccess.partner_api_key_id.in_(user_keys),
            PartnerApiKeyKbAccess.kb_id.in_(
                select(PortalKnowledgeBase.id).where(
                    PortalKnowledgeBase.org_id == org_id,
                    PortalKnowledgeBase.owner_type == "user",
                )
            ),
        )
    )
    result = await db.execute(
        update(PartnerAPIKey)
        .where(
            PartnerAPIKey.org_id == org_id,
            PartnerAPIKey.created_by == target_user_id,
        )
        .values(created_by=recipient_id)
    )
    return getattr(result, "rowcount", 0) or 0


async def delete_user_api_keys(target_user_id: str, org_id: int, db: AsyncSession) -> int:
    """Hard-delete the user's partner API keys.

    Only the platform-admin delete uses this: its actor is Klai staff, not a
    member of the tenant, so there is nobody in the org to hand the keys to.
    """
    result = await db.execute(
        delete(PartnerAPIKey).where(
            PartnerAPIKey.org_id == org_id,
            PartnerAPIKey.created_by == target_user_id,
        )
    )
    return getattr(result, "rowcount", 0) or 0


async def revoke_user_mcp_tokens(target_user_id: str, org_id: int, db: AsyncSession) -> int:
    """REQ-2.7 — soft-revoke the leaving user's MCP tokens.

    MCP tokens act as the person, not as an integration, so they end with the
    person. Soft revoke via ``revoked_at`` keeps the OAuth refresh-token
    replay-detection chain (``replaced_by_token_id``) intact for forensics.
    """
    # MCP tokens use the int portal_users.id as FK. Look it up; if the
    # portal_users row is already gone we have nothing to revoke.
    user_pk_result = await db.execute(
        select(PortalUser.id).where(
            PortalUser.zitadel_user_id == target_user_id,
            PortalUser.org_id == org_id,
        )
    )
    portal_user_pk = user_pk_result.scalar_one_or_none()
    if portal_user_pk is None:
        return 0
    result = await db.execute(
        update(PortalMcpToken)
        .where(
            PortalMcpToken.user_id == portal_user_pk,
            PortalMcpToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(tz=UTC))
    )
    return getattr(result, "rowcount", 0) or 0
