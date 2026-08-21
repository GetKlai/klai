"""SPEC-PRIVACY-QUERY-SHADOW-001 REQ-15 — tenant self-service endpoints.

Tenant-admin scoped endpoints for org-level settings the tenant can flip
without an operator round-trip: the telemetry-level toggle (privacy
posture vs debug capability, owned by the tenant not Klai), and — from
SPEC-PRIVACY-PII-POLICY-ADMIN-001 PR1 — the tenant's PII masking policy
(which entity types are masked, and the tenant's allow-list exclusions).

Auth contract: ``Depends(get_caller_at_least(ProfileRole.ADMIN))`` resolves
the OIDC subject to a ``UserPermissions`` and raises 403 if the caller is
not an org admin. The shared service-layer ``set_telemetry_level``
guarantees identical DB behaviour to the operator-side endpoint
(REQ-11) — single audit-log + cache-invalidation path, only the
``operator_kind`` field differs.

The PII policy endpoints (``pii-entities``, ``pii-allow-list``) follow
REQ-1's instruction to copy ``PATCH /api/admin/orgs/{slug}/platform-unlocks``'s
shape (full-set replacement, validated against a known key set,
audit-logged) rather than introducing a new ``Capability`` — REQ-1 is
explicit that the codebase has no per-feature RBAC finer than admin for
org-wide settings, and this is not the place to add the first instance.
They live on this router (not a new one) because ``/api/orgs/me/*`` is
already the tenant-self-service surface and REQ-1's write endpoint is
exactly that kind of setting.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _load_org_or_500
from app.core.database import get_db
from app.core.permissions import UserPermissions, get_caller_at_least
from app.core.profiles import ProfileRole
from app.services.audit import log_event
from app.services.pii_allow_list import PiiAllowListError, validate_allow_list
from app.services.pii_entity_policy import PiiEntityPolicyError, validate_entity_selection
from app.services.telemetry_level import set_telemetry_level

logger = logging.getLogger(__name__)

# /api/orgs/me/* — tenant-self-service surface. The "me" suffix mirrors
# /api/me's user-self-service convention; "orgs/me" reads as "the
# org of the calling user". The handler refuses any org_id that doesn't
# match the caller's resolved org (defense-in-depth).
router = APIRouter(prefix="/api/orgs", tags=["orgs"])


class TelemetryLevelUpdate(BaseModel):
    level: Literal["off", "shadow", "full"]


class TelemetryLevelOut(BaseModel):
    telemetry_level: Literal["off", "shadow", "full"]


@router.post("/me/telemetry-level", response_model=TelemetryLevelOut)
async def set_my_org_telemetry_level(
    body: TelemetryLevelUpdate,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> TelemetryLevelOut:
    """Tenant-admin endpoint to flip their own org's telemetry mode.

    REQ-15 contract:
    - Caller MUST hold the ``admin`` role on the resolved org (else 403)
    - DB update is scoped to ``perms.org_id`` — cross-org attempts via
      a manipulated path are impossible because the org is read from
      the caller's JWT, not the URL
    - Audit row records ``operator_kind='tenant_admin'``,
      ``reason='tenant self-service via admin UI'``,
      ``operator_user_id=<zitadel sub>``
    - Cache invalidation runs so the next chat completion picks up the
      new level within ~30s (kb_ver Redis pointer expiry)
    """
    try:
        _, new_level = await set_telemetry_level(
            db,
            org_id=perms.org_id,
            new_level=body.level,
            operator_kind="tenant_admin",
            operator_user_id=perms.user_id,
            reason="tenant self-service via admin UI",
        )
    except LookupError as exc:
        # Should never happen — get_caller_at_least just returned a perms
        # for this org. Preserved for defense-in-depth against a race with
        # another operation deleting the org row.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        # pydantic Literal already restricts the input; this is the
        # service-layer's reason validation.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return TelemetryLevelOut(telemetry_level=new_level)


# ---------------------------------------------------------------------------
# SPEC-PRIVACY-PII-POLICY-ADMIN-001 PR1 — tenant PII policy write path
# ---------------------------------------------------------------------------


class PiiEntitiesUpdate(BaseModel):
    entities: list[str]


class PiiEntitiesOut(BaseModel):
    entities: list[str]


@router.patch("/me/pii-entities", response_model=PiiEntitiesOut)
async def set_my_org_pii_entities(
    body: PiiEntitiesUpdate,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> PiiEntitiesOut:
    """Tenant-admin endpoint: replace the org's opted-in PII entity set.

    REQ-1 contract, copied from ``PATCH /api/admin/orgs/{slug}/platform-unlocks``:

    - **Full-set replacement.** The caller sends the complete desired set;
      this is not a diff/patch of individual flags.
    - **Validated against a known key set** via
      ``pii_entity_policy.validate_entity_selection`` — the single
      validation path its own docstring requires every write path to use.
      ``PERSON``, ``SECRET`` and ``NL_BSN`` are rejected here (422) with a
      reason naming the requirement; the DB CHECK
      ``chk_portal_orgs_pii_masked_entities`` rejects the same three at the
      storage layer independently, in case this Python path is ever
      bypassed (an operator SQL update, a future direct-write bug).
    - **Gated at ``ProfileRole.ADMIN``** — the same bar as
      ``telemetry-level``, the closest existing per-org privacy setting.
      REQ-1 is explicit that this does NOT get a new ``Capability``: there
      is no per-feature RBAC finer than admin for org-wide settings
      anywhere in this codebase, and this would be the only instance.
    - **Tenant-scoped structurally, not by a URL parameter.** ``org_id``
      comes from ``perms`` (resolved from the caller's JWT by
      ``get_caller_at_least``), never from the request body or path — a
      manipulated org id in the request cannot target another tenant.
    - **Audit-logged** via ``log_event`` (``portal_audit_log``), the same
      surface ``telemetry_level`` uses for tenant self-service writes.
    """
    try:
        validated = validate_entity_selection(body.entities)
    except PiiEntityPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    org = await _load_org_or_500(db, perms.org_id)
    previous = list(org.pii_masked_entities or [])
    new_entities = sorted(validated)

    org.pii_masked_entities = new_entities  # type: ignore[assignment]
    await db.commit()

    audit_details = {
        "previous_entities": previous,
        "new_entities": new_entities,
        "operator_kind": "tenant_admin",
    }
    try:
        await log_event(
            org_id=perms.org_id,
            actor=perms.user_id,
            action="pii_masked_entities_changed",
            resource_type="portal_org",
            resource_id=str(perms.org_id),
            details=audit_details,
        )
    except Exception:
        # The commit above already landed, and log_event owns its own
        # session, so raising here would 500 a caller whose change DID
        # persist -- inviting a retry that writes a second audit row for
        # one change. Same contract as telemetry_level.set_telemetry_level:
        # fire-and-forget, but never silent.
        logger.warning(
            "pii_masked_entities_audit_log_failed",
            extra={"org_id": perms.org_id, **audit_details},
            exc_info=True,
        )

    logger.info(
        "pii_masked_entities_updated",
        extra={"org_id": perms.org_id, "previous": previous, "new": new_entities},
    )
    return PiiEntitiesOut(entities=new_entities)


class PiiAllowListEntryIn(BaseModel):
    value: str
    match: Literal["exact", "regex"]
    note: str | None = None


class PiiAllowListEntryOut(BaseModel):
    value: str
    match: Literal["exact", "regex"]
    note: str | None = None


class PiiAllowListUpdate(BaseModel):
    entries: list[PiiAllowListEntryIn] = Field(default_factory=list)


class PiiAllowListOut(BaseModel):
    entries: list[PiiAllowListEntryOut]


@router.patch("/me/pii-allow-list", response_model=PiiAllowListOut)
async def set_my_org_pii_allow_list(
    body: PiiAllowListUpdate,
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> PiiAllowListOut:
    """Tenant-admin endpoint: replace the org's PII allow-list (D1).

    Same shape and same auth gate as ``set_my_org_pii_entities`` above —
    full-set replacement, validated, audit-logged, tenant-scoped via
    ``perms`` rather than a URL/body org id.

    Validation goes through ``pii_allow_list.validate_allow_list``, REQ-9's
    safety envelope for tenant-supplied regex: non-compiling patterns and
    nested-quantifier (catastrophic-backtracking) shapes are rejected
    before anything is written, entry values are length-capped, and the
    list itself is capped at ``pii_allow_list.MAX_ALLOW_LIST_ENTRIES``
    entries. See that module's docstring for what this validation does
    and does not guarantee — it does not execute the pattern against text.

    **This PR stores and validates only.** Wiring ``pii_allow_list`` into
    Presidio's ``allow_list`` / ``allow_list_match`` parameters (REQ-3
    resolution step 4) is out of scope here; a stored entry has no runtime
    effect yet.
    """
    try:
        validated = validate_allow_list(entry.model_dump() for entry in body.entries)
    except PiiAllowListError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    org = await _load_org_or_500(db, perms.org_id)
    previous = list(org.pii_allow_list or [])

    org.pii_allow_list = validated  # type: ignore[assignment]
    await db.commit()

    audit_details = {
        "previous_entries": previous,
        "new_entries": validated,
        "operator_kind": "tenant_admin",
    }
    try:
        await log_event(
            org_id=perms.org_id,
            actor=perms.user_id,
            action="pii_allow_list_changed",
            resource_type="portal_org",
            resource_id=str(perms.org_id),
            details=audit_details,
        )
    except Exception:
        # The commit above already landed, and log_event owns its own
        # session, so raising here would 500 a caller whose change DID
        # persist -- inviting a retry that writes a second audit row for
        # one change. Same contract as telemetry_level.set_telemetry_level:
        # fire-and-forget, but never silent.
        logger.warning(
            "pii_allow_list_audit_log_failed",
            extra={"org_id": perms.org_id, "previous_count": len(previous), "new_count": len(validated)},
            exc_info=True,
        )

    logger.info(
        "pii_allow_list_updated",
        extra={"org_id": perms.org_id, "previous_count": len(previous), "new_count": len(validated)},
    )
    return PiiAllowListOut(entries=[PiiAllowListEntryOut(**entry) for entry in validated])
