"""
Internal service-to-service endpoints.

Only accessible within the Docker network (klai-net) — never exposed publicly.
Protected by a shared Bearer secret (INTERNAL_SECRET env var).

Used by klai-mailer to look up a user's preferred language so it can append
?lang= to email action URLs (verify, password-reset, etc.).

Used by the LiteLLM knowledge hook (KB-010) to check knowledge product entitlement
and perform lazy LibreChat MongoDB ObjectId → Zitadel user ID mapping.

SPEC-SEC-005 hardening (2026-04):
- Per-caller-IP sliding-window rate limit (100 req/min, configurable) on all /internal/*.
- Fire-and-forget audit row written to portal_audit_log for every authenticated call.
- Rate-limit and audit run only AFTER shared-secret validation (token check first gate).
"""

import asyncio
import hmac
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import redis.asyncio as aioredis
import structlog
from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from jwt import PyJWKClient
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_effective_capabilities
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db, set_tenant
from app.core.permissions import resolve_user_permissions
from app.core.provisioning_names import validate_slug_for_provisioning
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.portal import PortalOrg, PortalUser
from app.models.templates import PortalTemplate
from app.services.connector_credentials import SENSITIVE_FIELDS, credential_store
from app.services.entitlements import get_effective_products
from app.services.events import emit_event
from app.services.gap_rescorer import schedule_rescore
from app.services.partner_rate_limit import check_rate_limit
from app.services.quality_scorer import schedule_quality_update
from app.services.redis_client import get_redis_pool
from app.services.retrieval_log import find_correlated_log
from app.services.zitadel import zitadel

logger = logging.getLogger(__name__)
structlog_logger = structlog.get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])

_REQUIRED_ENCRYPTED_CREDENTIAL_FIELDS: dict[str, set[str]] = {
    "confluence": {"api_token"},
    "airtable": {"api_key"},
}


def _sensitive_fields_for_connector(connector_type: str) -> set[str]:
    return set(SENSITIVE_FIELDS.get(connector_type, []))


def _plaintext_sensitive_fields(connector: PortalConnector) -> list[str]:
    config = connector.config or {}
    return sorted(field for field in _sensitive_fields_for_connector(connector.connector_type) if field in config)


def _raise_plaintext_sensitive_config(connector: PortalConnector, fields: list[str]) -> None:
    logger.error(
        "connector_plaintext_sensitive_config_detected",
        extra={
            "connector_id": str(connector.id),
            "connector_type": connector.connector_type,
            "fields": fields,
        },
    )
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error_code": "connector_plaintext_secret_detected"},
    )


def _assert_connector_credentials_readable(connector: PortalConnector) -> None:
    fields = _plaintext_sensitive_fields(connector)
    if fields:
        _raise_plaintext_sensitive_config(connector, fields)

    required_fields = _REQUIRED_ENCRYPTED_CREDENTIAL_FIELDS.get(connector.connector_type, set())
    if required_fields and connector.encrypted_credentials is None:
        logger.error(
            "connector_required_encrypted_credentials_missing",
            extra={
                "connector_id": str(connector.id),
                "connector_type": connector.connector_type,
                "fields": sorted(required_fields),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error_code": "connector_required_credentials_missing"},
        )


# SPEC-SEC-005 REQ-2.3: hold references to fire-and-forget audit tasks so the
# event loop cannot GC them mid-flight. Same pattern as partner_dependencies._pending.
_pending_audit: set[asyncio.Task] = set()  # type: ignore[type-arg]

# SPEC-SEC-005 REQ-1.5: distinct Redis namespace from partner_rl:* to prevent
# collision between internal caller IPs and partner API key IDs.
_INTERNAL_RL_KEY_PREFIX = "internal_rl:"

# SPEC-SEC-005 REQ-2.1 / REQ-2.2: raw SQL INSERT for portal_audit_log (RLS split-policy
# table). ORM inserts emit implicit RETURNING which triggers the SELECT policy and fails.
_AUDIT_INSERT_SQL = text(
    "INSERT INTO portal_audit_log "
    "(org_id, actor_user_id, action, resource_type, resource_id, details) "
    "VALUES (COALESCE(:org_id, 0), :actor_user_id, :action, :resource_type, :resource_id, "
    "CAST(:details AS jsonb))"
)


# SPEC-SEC-SESSION-001: caller-IP resolution moved to ``app.services.request_ip``
# once a third callsite (``app.api.auth`` for IDP-pending cookie binding)
# joined the existing internal-rate-limit and internal-audit consumers. The
# alias below preserves the private name so all in-module callsites and the
# ``test_internal_hardening`` patch surface stay unchanged.
from app.services.request_ip import resolve_caller_ip as _resolve_caller_ip  # noqa: E402


def _rate_limit_backend_unavailable(caller_ip: str, reason: str, *, exc_info: bool = False) -> None:
    """Apply the configured fail-mode for an unavailable rate-limit backend.

    SPEC-SEC-INTERNAL-001 REQ-5.2 / REQ-5.3 / AC-5: ``closed`` raises 503 so the
    blast-radius of a Redis outage is bounded; ``open`` preserves the
    legacy SEC-005 REQ-1.3 fail-open behaviour for environments that
    prioritise availability over rate-limit enforcement (staging / dev).

    ``exc_info`` is forwarded only when the caller is inside an active
    exception handler (the ``except Exception:`` branch in
    ``_check_rate_limit_internal``); the ``redis_pool is None`` branch
    has no exception context and passes ``exc_info=False``.
    """
    if settings.internal_rate_limit_fail_mode == "closed":
        structlog_logger.warning(
            "internal_rate_limit_fail_closed",
            caller_ip=caller_ip,
            reason=reason,
            exc_info=exc_info,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal rate limit backend unavailable",
        )
    structlog_logger.warning(
        "internal_rate_limit_redis_unavailable",
        caller_ip=caller_ip,
        reason=reason,
        exc_info=exc_info,
    )


async def _check_rate_limit_internal(caller_ip: str) -> None:
    """SPEC-SEC-005 REQ-1: per-caller-IP sliding-window rate limit for /internal/*.

    Reuses the partner_rate_limit sliding-window primitive with a distinct key
    namespace (internal_rl:<caller_ip>). Backend-unavailable behaviour is
    governed by SPEC-SEC-INTERNAL-001 REQ-5 via
    ``settings.internal_rate_limit_fail_mode`` -- ``closed`` (production
    default) raises HTTP 503; ``open`` (staging / dev) falls through.
    Raises HTTPException 429 with Retry-After header when the ceiling
    is exceeded under the normal Redis-available path.
    """
    redis_pool = await get_redis_pool()
    if redis_pool is None:
        _rate_limit_backend_unavailable(caller_ip, reason="redis_pool_none")
        return

    try:
        allowed, retry_after = await check_rate_limit(
            redis_pool,
            f"{_INTERNAL_RL_KEY_PREFIX}{caller_ip}",
            settings.internal_rate_limit_rpm,
        )
    except Exception:
        _rate_limit_backend_unavailable(caller_ip, reason="redis_exception", exc_info=True)
        return

    if not allowed:
        structlog_logger.info(
            "internal_rate_limit_exceeded",
            caller_ip=caller_ip,
            limit_rpm=settings.internal_rate_limit_rpm,
            retry_after=retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Internal rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


async def _log_internal_call(
    org_id: int | None,
    caller_ip: str,
    endpoint_path: str,
    method: str,
) -> None:
    """SPEC-SEC-005 REQ-2: fire-and-forget audit row writer for /internal/*.

    Opens an independent AsyncSessionLocal() so the write survives primary-endpoint
    rollbacks (fire-and-forget pattern, see portal-backend.md). Raw SQL INSERT because
    portal_audit_log is an RLS split-policy table (SELECT org-scoped, INSERT permissive).

    REQ-2.4 / AC-1: audit failure MUST NOT fail the primary request. Any exception is
    swallowed and logged as `internal_audit_write_failed`.

    REQ-2.7 / AC-9: emits a structlog `internal_call_audited` entry for VictoriaLogs
    cross-correlation via request_id. The structlog entry is emitted BEFORE the DB
    write so the cross-trace signal remains even if the insert fails.
    """
    resolved_org_id = org_id if org_id is not None else 0
    details_payload = {"caller_ip": caller_ip, "method": method}

    # REQ-2.7: structlog cross-trace entry. Separate from DB write so it is visible
    # even when the DB insert fails.
    structlog_logger.info(
        "internal_call_audited",
        org_id=resolved_org_id,
        caller_ip=caller_ip,
        endpoint_path=endpoint_path,
        method=method,
        action="internal_call",
        resource_type="internal_endpoint",
    )

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                _AUDIT_INSERT_SQL,
                {
                    "org_id": resolved_org_id,
                    "actor_user_id": f"internal:{caller_ip}",
                    "action": "internal_call",
                    "resource_type": "internal_endpoint",
                    "resource_id": endpoint_path,
                    "details": json.dumps(details_payload),
                },
            )
            await session.commit()
    except Exception:
        # REQ-2.4: audit is forensic, not a hard gate — never fail the primary request.
        structlog_logger.exception(
            "internal_audit_write_failed",
            caller_ip=caller_ip,
            endpoint_path=endpoint_path,
        )


async def _audit_internal_call(request: Request, org_id: int | None = None) -> None:
    """Fire-and-forget audit wrapper, called by each internal endpoint on its success path.

    Reads caller_ip / endpoint_path / method stashed on request.state by
    _require_internal_token and schedules _log_internal_call as an asyncio.create_task
    with a strong reference in _pending_audit so the task is not GC'd mid-flight.

    Callers pass the endpoint-resolved integer org_id when available (REQ-2.6 / AC-3),
    or 0 / None for endpoints that do not resolve a tenant.
    """
    caller_ip: str = getattr(request.state, "internal_caller_ip", "unknown")
    endpoint_path: str = getattr(request.state, "internal_endpoint_path", request.url.path)
    method: str = getattr(request.state, "internal_method", request.method)

    try:
        task = asyncio.create_task(
            _log_internal_call(
                org_id=org_id,
                caller_ip=caller_ip,
                endpoint_path=endpoint_path,
                method=method,
            )
        )
        _pending_audit.add(task)
        task.add_done_callback(_pending_audit.discard)
    except RuntimeError:
        # No running event loop — extremely unlikely inside a FastAPI handler.
        structlog_logger.warning(
            "internal_audit_schedule_failed",
            caller_ip=caller_ip,
            endpoint_path=endpoint_path,
        )


async def _require_internal_token(request: Request) -> None:
    """Validate the shared secret, enforce rate limit, and stash audit context.

    Order of operations (SPEC-SEC-005):
    1. Existing token validation — reject 401/503 BEFORE any other work. This guarantees
       unauthenticated traffic does NOT consume rate-limit budget or produce audit rows
       (AC-5, AC-8).
    2. Resolve caller IP (REQ-1.6).
    3. Per-caller-IP rate-limit check (REQ-1.1); raises 429 if exceeded.
    4. Stash caller_ip / endpoint_path / method on request.state so each handler can call
       _audit_internal_call(request, org_id=...) once its org_id is resolved.

    This coroutine is called directly at the top of each handler (not registered as a
    FastAPI dependency) to preserve the existing call sites unchanged.
    """
    if not settings.internal_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Internal API not configured")
    token = request.headers.get("Authorization", "")
    # hmac.compare_digest is constant-time; string equality leaks length/prefix timing.
    expected = f"Bearer {settings.internal_secret}"
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")

    # Token check passed. Proceed to rate limit + audit context.
    caller_ip = _resolve_caller_ip(request)
    await _check_rate_limit_internal(caller_ip)

    # Matched route template preferred over raw URL to avoid PII in query strings
    # bleeding into the audit table (REQ-2.5 / AC-12).
    route = request.scope.get("route")
    endpoint_path = getattr(route, "path", None) or request.url.path

    request.state.internal_caller_ip = caller_ip
    request.state.internal_endpoint_path = endpoint_path
    request.state.internal_method = request.method


class UserLanguageResponse(BaseModel):
    preferred_language: str


class OrgAdminEmailResponse(BaseModel):
    admin_email: str


@router.get("/org/{org_id}/admin-email", response_model=OrgAdminEmailResponse)
async def get_org_admin_email(
    org_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OrgAdminEmailResponse:
    """Return the primary admin email for an organisation.

    SPEC-SEC-MAILER-INJECTION-001 REQ-3.1 callback: klai-mailer resolves
    the expected recipient for `join_request_admin` via this endpoint
    rather than trusting an attacker-supplied `to` address.

    Returns the earliest-created `role='admin'` user with a populated
    email for the org. 404 if no such user exists.
    """
    await _require_internal_token(request)
    await set_tenant(db, org_id)

    result = await db.execute(
        select(PortalUser.email)
        .where(
            PortalUser.org_id == org_id,
            PortalUser.role == "admin",
            PortalUser.status == "active",
            PortalUser.email.isnot(None),
        )
        .order_by(PortalUser.created_at.asc())
        .limit(1)
    )
    admin_email = result.scalar_one_or_none()
    if not admin_email:
        await _audit_internal_call(request, org_id=org_id)
        raise HTTPException(status_code=404, detail="No admin user for org")

    await _audit_internal_call(request, org_id=org_id)
    return OrgAdminEmailResponse(admin_email=admin_email)


@router.get("/user-language", response_model=UserLanguageResponse)
async def get_user_language(
    email: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserLanguageResponse:
    """Return the preferred language for a user identified by email address.

    Used by klai-mailer to append ?lang=<lang> to email action URLs so that
    verify / password-reset links open the portal in the user's own language.

    Falls back to "nl" when the user is not found in the portal DB.
    """
    await _require_internal_token(request)

    user_id = await zitadel.find_user_id_by_email(email)
    logger.info("Internal user lookup: email=%s, found=%s", email, user_id is not None)
    if not user_id:
        # AC-3: unknown email → audit with org_id=0 and still return 200.
        await _audit_internal_call(request, org_id=0)
        return UserLanguageResponse(preferred_language="nl")

    result = await db.execute(
        select(PortalUser.preferred_language, PortalUser.org_id).where(PortalUser.zitadel_user_id == user_id)
    )
    row = result.first()
    if not row:
        await _audit_internal_call(request, org_id=0)
        return UserLanguageResponse(preferred_language="nl")
    await set_tenant(db, row.org_id)
    await _audit_internal_call(request, org_id=row.org_id)
    return UserLanguageResponse(preferred_language=row.preferred_language or "nl")


class UserProductsResponse(BaseModel):
    products: list[str]
    capabilities: list[str] = []


@router.get("/users/{zitadel_user_id}/products", response_model=UserProductsResponse)
async def get_user_products(
    zitadel_user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserProductsResponse:
    """Return enabled products and KB capabilities for a user.

    Called by Zitadel Action for JWT enrichment.
    Returns empty list if user not found (fail-closed behavior for JWT).

    SPEC-PORTAL-UNIFY-KB-001: extended with capabilities field so the frontend
    can fetch both products and capabilities in a single me-endpoint call.
    Extending the existing response minimises breaking changes — existing consumers
    that ignore unknown fields are unaffected.
    """
    await _require_internal_token(request)

    # Set tenant context so get_effective_products can query RLS-protected tables
    result = await db.execute(select(PortalUser.org_id).where(PortalUser.zitadel_user_id == zitadel_user_id))
    org_id = result.scalar_one_or_none()
    if org_id is not None:
        await set_tenant(db, org_id)

    products = await get_effective_products(zitadel_user_id, db)
    capabilities = await get_effective_capabilities(zitadel_user_id, db)
    await _audit_internal_call(request, org_id=org_id or 0)
    return UserProductsResponse(products=products, capabilities=sorted(capabilities))


# SPEC-PORTAL-RBAC-REFACTOR-001 REQ-19: serialised UserPermissions endpoint.
# Used by klai-knowledge-mcp (and future MCP-style services) as a fallback
# when the OAuth-token verify path doesn't yield a fresh effective_role —
# e.g. immediately after a role change while the caller still holds an
# old-claim JWT. Auth is the same X-Internal-Secret pattern as every other
# endpoint in this file.


class UserPermissionsResponse(BaseModel):
    """Serialised ``UserPermissions`` for cross-service consumers.

    Mirrors the dataclass fields 1:1 with primitive-only types so the
    receiver does not need access to the SQLAlchemy/Pydantic ORM models.
    Frozensets are serialised as sorted lists for deterministic output.
    """

    user_id: str
    org_id: int
    org_slug: str
    role: str
    plan: str
    # SPEC-PORTAL-EXTENSIONS-UNIFY-001: enabled_addons column dropped 2026-05-12.
    # platform_unlocked_features is now the single source of truth for
    # tenant-level extension state. No consumer of /internal/identity/permissions
    # was reading enabled_addons (audited 2026-05-12 across all klai services).
    platform_unlocked_features: list[str]
    effective_role: str
    effective_capabilities: list[str]
    effective_products: list[str]
    is_platform_admin: bool
    provisioning_status: str


@router.get(
    "/users/{zitadel_user_id}/permissions",
    response_model=UserPermissionsResponse,
)
async def get_user_permissions(
    zitadel_user_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> UserPermissionsResponse:
    """Return the full ``UserPermissions`` snapshot for a Zitadel user.

    SPEC-PORTAL-RBAC-REFACTOR-001 REQ-19: fallback for MCP-server when its
    JWT-claim is missing or stale (e.g. post-role-rotation). Returns the
    same data ``get_caller`` builds for in-process FastAPI requests, so
    the MCP server can apply identical role / capability gates without
    needing access to portal_users / portal_orgs directly.

    404 when the user has no portal_users row — fail-closed so a typo in
    the URL or a deleted user surfaces as "no permissions" not "empty
    set" (the latter would silently treat the caller as personal-tier
    on a deny-by-default policy and be hard to debug).
    """
    await _require_internal_token(request)

    perms = await resolve_user_permissions(zitadel_user_id, db)
    if perms is None:
        await _audit_internal_call(request, org_id=0)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "user_not_found", "user_id": zitadel_user_id},
        )

    await _audit_internal_call(request, org_id=perms.org_id)
    return UserPermissionsResponse(
        user_id=perms.user_id,
        org_id=perms.org_id,
        org_slug=perms.org_slug,
        role=perms.role.value,
        plan=perms.plan,
        platform_unlocked_features=sorted(perms.platform_unlocked_features),
        effective_role=perms.effective_role.value,
        effective_capabilities=sorted(c.value for c in perms.effective_capabilities),
        effective_products=sorted(perms.effective_products),
        is_platform_admin=perms.is_platform_admin,
        provisioning_status=perms.provisioning_status,
    )


class ConnectorConfigResponse(BaseModel):
    connector_id: str
    kb_id: int
    kb_slug: str
    zitadel_org_id: str  # Zitadel org ID string — used by klai-connector for Qdrant partitioning
    connector_type: str
    config: dict
    schedule: str | None
    is_enabled: bool
    allowed_assertion_modes: list[str] | None
    # owner_user_id: Zitadel user_id of the user who created this connector.
    # Forwarded by klai-connector to knowledge-ingest /ingest/v1/document
    # as ``req.user_id``. Required for personal-KB ownership check
    # (knowledge_ingest.routes.ingest::personal_kb_owner_mismatch) — without
    # it, syncs to ``personal-{user}`` KBs 403 because user_id=None.
    owner_user_id: str | None = None


@router.get("/connectors/{connector_id}", response_model=ConnectorConfigResponse)
async def get_connector_config(
    connector_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ConnectorConfigResponse:
    """Return connector config for klai-connector service."""
    await _require_internal_token(request)
    # portal_connectors has no RLS — use it to resolve org_id for tenant context.
    connector_stub = await db.get(PortalConnector, connector_id)
    if not connector_stub:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector_stub.org_id)
    result = await db.execute(
        select(PortalConnector, PortalKnowledgeBase, PortalOrg)
        .join(PortalKnowledgeBase, PortalConnector.kb_id == PortalKnowledgeBase.id)
        .join(PortalOrg, PortalConnector.org_id == PortalOrg.id)
        .where(PortalConnector.id == connector_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    connector, kb, org = row

    _assert_connector_credentials_readable(connector)

    # Merge decrypted credentials into config for internal consumers.
    # Public app endpoints never receive this merged shape; it is only sent to
    # klai-connector over the internal API after the service secret check.
    merged_config = dict(connector.config) if connector.config else {}
    if connector.encrypted_credentials is not None and credential_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "credential_store_unavailable"},
        )
    if connector.encrypted_credentials is not None:
        store = credential_store
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"error_code": "credential_store_unavailable"},
            )
        decrypted = await store.decrypt_credentials(
            org_id=connector.org_id,
            encrypted_credentials=connector.encrypted_credentials,
            db=db,
        )
        merged_config.update(decrypted)

    await _audit_internal_call(request, org_id=connector.org_id)
    return ConnectorConfigResponse(
        connector_id=str(connector.id),
        kb_id=connector.kb_id,
        kb_slug=kb.slug,
        zitadel_org_id=org.zitadel_org_id,
        connector_type=connector.connector_type,
        config=merged_config,
        schedule=connector.schedule,
        is_enabled=connector.is_enabled,
        allowed_assertion_modes=connector.allowed_assertion_modes,
        owner_user_id=connector.created_by,
    )


class SyncStatusCallback(BaseModel):
    sync_run_id: str
    status: str
    completed_at: datetime
    documents_total: int = 0
    documents_ok: int = 0
    documents_failed: int = 0
    bytes_processed: int = 0
    error_details: list[dict] | None = None


@router.post("/connectors/{connector_id}/sync-status", status_code=status.HTTP_204_NO_CONTENT)
async def receive_sync_status(
    connector_id: str,
    body: SyncStatusCallback,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Receive sync completion callback from klai-connector.

    Updates last_sync_at and last_sync_status on the portal connector record.
    Called by klai-connector after each sync run completes.
    """
    await _require_internal_token(request)
    connector = await db.get(PortalConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector.org_id)
    connector.last_sync_at = body.completed_at
    connector.last_sync_status = body.status
    connector.last_sync_documents_ok = body.documents_ok
    await db.commit()

    if body.status == "completed":
        # Load org's zitadel_org_id for re-scoring
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.id == connector.org_id))
        org = org_result.scalar_one_or_none()
        if org:
            await schedule_rescore(
                org_id=connector.org_id,
                zitadel_org_id=org.zitadel_org_id,
                kb_slug=None,  # connector sync covers all KBs
                db_factory=get_db,
                delay_seconds=0.0,  # no delay needed -- connector already fully synced
            )
    await _audit_internal_call(request, org_id=connector.org_id)


class CredentialsUpdate(BaseModel):
    """Partial update to a connector's encrypted credentials (SPEC-KB-025 + SPEC-KB-MS-DOCS-001 R9).

    Called by klai-connector after refreshing an OAuth access token. Only the
    fields to be updated are provided; the rest of the encrypted credential
    blob is preserved.

    ``refresh_token`` is optional and only sent when the provider rotated it
    (Microsoft rotates on every refresh). For providers that do not rotate,
    it is None/absent and the stored refresh_token is left untouched.
    """

    access_token: str
    token_expiry: str | None = None
    refresh_token: str | None = None


# @MX:ANCHOR: [AUTO] Writeback path for refreshed OAuth access tokens.
# @MX:REASON: Called by klai-connector OAuthAdapterBase.ensure_token(). SPEC-KB-025.
@router.patch("/connectors/{connector_id}/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def update_connector_credentials(
    connector_id: str,
    body: CredentialsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Merge refreshed OAuth tokens into the connector's encrypted credentials.

    Flow:
    1. Authorize via internal Bearer secret.
    2. Load connector and set tenant context.
    3. Decrypt current credentials (preserves refresh_token, etc.).
    4. Merge in the new access_token + optional token_expiry.
    5. Re-encrypt and persist. The config column is overwritten with the
       stripped non-secret config returned by the credential store.
    """
    await _require_internal_token(request)
    connector = await db.get(PortalConnector, connector_id)
    if connector is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    await set_tenant(db, connector.org_id)

    if credential_store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Credential store not configured",
        )

    _assert_connector_credentials_readable(connector)

    # Start from encrypted credentials only. Plaintext config fallback is a
    # security bug: it keeps leaked secrets alive and hides incomplete
    # remediation.
    merged: dict = {}
    if connector.encrypted_credentials is not None:
        merged = await credential_store.decrypt_credentials(
            org_id=connector.org_id,
            encrypted_credentials=connector.encrypted_credentials,
            db=db,
        )

    # Apply the patch — NEVER log access_token / refresh_token values.
    merged["access_token"] = body.access_token
    if body.token_expiry is not None:
        merged["token_expiry"] = body.token_expiry
    # SPEC-KB-MS-DOCS-001 R9: providers that rotate refresh_tokens send the new
    # one here so it survives restart. Absent/None = leave the stored RT intact.
    if body.refresh_token is not None:
        merged["refresh_token"] = body.refresh_token

    encrypted_blob, stripped_config = await credential_store.encrypt_credentials(
        org_id=connector.org_id,
        connector_type=connector.connector_type,
        config=merged,
        db=db,
    )
    connector.encrypted_credentials = encrypted_blob
    connector.config = stripped_config
    _assert_connector_credentials_readable(connector)
    await db.commit()
    await _audit_internal_call(request, org_id=connector.org_id)


class KnowledgeFeatureResponse(BaseModel):
    enabled: bool
    kb_retrieval_enabled: bool = True
    kb_personal_enabled: bool = True
    kb_slugs_filter: list[str] | None = None
    kb_narrow: bool = False
    kb_pref_version: int = 0
    # SPEC-SEC-IDENTITY-ASSERT-001 follow-up: retrieval-api's identity-verify
    # check matches against `PortalUser.zitadel_user_id`. The LiteLLM hook only
    # has the LibreChat MongoDB ObjectId at hand. Returning the resolved
    # zitadel_user_id here lets the hook send the right identifier on the
    # /retrieve call, and matches what knowledge-ingest stamps on personal-KB
    # qdrant chunks (klai-portal/backend/app/api/knowledge.py:172-204), so
    # the personal-scope filter also works.
    zitadel_user_id: str | None = None
    # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-2: per-tenant telemetry mode threaded
    # through to the LiteLLM hook + knowledge-mcp. Default 'shadow' for
    # backwards compatibility — when the field is absent in cached responses
    # from older portal-api builds, downstream callers fail-open to 'shadow'
    # per REQ-4.
    telemetry_level: Literal["off", "shadow", "full"] = "shadow"


@router.get("/v1/users/{librechat_user_id}/feature/knowledge", response_model=KnowledgeFeatureResponse)
async def get_knowledge_feature(
    librechat_user_id: str,
    org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeFeatureResponse:
    """Check whether a user has the knowledge product entitlement.

    Called by the LiteLLM knowledge hook on every chat request. Auth-gated (fail-closed):
    any error or unknown user returns enabled=False so KB injection never leaks to
    unauthorized users.

    Lazy mapping: on first call for an unknown librechat_user_id, performs a MongoDB
    lookup in the tenant's LibreChat database to resolve the Zitadel user ID and caches
    it in portal_users.librechat_user_id for all subsequent calls (pure PostgreSQL).
    """
    await _require_internal_token(request)

    # Set tenant context early using the org_id query param (Zitadel org ID).
    # This is needed so subsequent queries on RLS-protected tables work correctly.
    # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-2: also fetch telemetry_level so every
    # disabled-path return surfaces the org's level (not the default).
    org_lookup = await db.execute(
        select(PortalOrg.id, PortalOrg.telemetry_level).where(PortalOrg.zitadel_org_id == org_id)
    )
    org_row = org_lookup.one_or_none()
    portal_org_id = org_row[0] if org_row else None
    org_telemetry_level: Literal["off", "shadow", "full"] = org_row[1] if org_row else "shadow"
    if portal_org_id is not None:
        await set_tenant(db, portal_org_id)

    audit_org_id = portal_org_id or 0

    # Step 1: fast path — librechat_user_id already mapped in PostgreSQL
    result = await db.execute(select(PortalUser).where(PortalUser.librechat_user_id == librechat_user_id))
    user = result.scalar_one_or_none()

    if user is None:
        # Step 2: lazy MongoDB lookup to resolve LibreChat ObjectId → Zitadel user ID
        if not settings.librechat_mongo_root_uri:
            logger.warning("KB authz: LIBRECHAT_MONGO_ROOT_URI not set — fail-closed for user %s", librechat_user_id)
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)

        # Look up the org to get its LibreChat container name (= MongoDB database name)
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == org_id))
        org = org_result.scalar_one_or_none()
        if org is None or not org.librechat_container:
            logger.warning("KB authz: org %s has no librechat_container — fail-closed", org_id)
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)

        try:
            oid = ObjectId(librechat_user_id)
        except InvalidId:
            logger.warning("KB authz: invalid ObjectId %s — fail-closed", librechat_user_id)
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)

        mongo_client: AsyncIOMotorClient | None = None
        try:
            mongo_client = AsyncIOMotorClient(settings.librechat_mongo_root_uri)
            mongo_user = await mongo_client[org.librechat_container]["users"].find_one({"_id": oid})
        except Exception as exc:
            logger.warning(
                "KB authz: MongoDB lookup failed for %s — fail-closed: %s",
                librechat_user_id,
                exc,
                exc_info=True,
            )
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)
        finally:
            if mongo_client is not None:
                mongo_client.close()

        if mongo_user is None:
            logger.warning("KB authz: no LibreChat user found for ObjectId %s — fail-closed", librechat_user_id)
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)

        zitadel_user_id = mongo_user.get("openidId") or mongo_user.get("openid_id") or mongo_user.get("sub")
        if not zitadel_user_id:
            logger.warning("KB authz: LibreChat user %s has no openidId/sub — fail-closed", librechat_user_id)
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)

        # Resolve portal user and cache the mapping
        portal_result = await db.execute(select(PortalUser).where(PortalUser.zitadel_user_id == zitadel_user_id))
        user = portal_result.scalar_one_or_none()
        if user is None:
            logger.warning("KB authz: no portal user for zitadel_user_id %s — fail-closed", zitadel_user_id)
            await _audit_internal_call(request, org_id=audit_org_id)
            return KnowledgeFeatureResponse(enabled=False, telemetry_level=org_telemetry_level)

        user.librechat_user_id = librechat_user_id
        await db.commit()

    # Org-admins always get knowledge access
    if user.role == "admin":
        enabled = True
    else:
        products = await get_effective_products(user.zitadel_user_id, db)
        enabled = "knowledge" in products

    await _audit_internal_call(request, org_id=user.org_id or audit_org_id)
    return KnowledgeFeatureResponse(
        enabled=enabled,
        kb_retrieval_enabled=user.kb_retrieval_enabled,
        kb_personal_enabled=user.kb_personal_enabled,
        kb_slugs_filter=user.kb_slugs_filter,
        kb_narrow=user.kb_narrow,
        kb_pref_version=user.kb_pref_version,
        zitadel_user_id=user.zitadel_user_id,
        telemetry_level=org_telemetry_level,
    )


# SPEC-PRIVACY-QUERY-SHADOW-001 REQ-11: internal-admin telemetry-level toggle.
class TelemetryLevelChange(BaseModel):
    level: Literal["off", "shadow", "full"]
    reason: str


class TelemetryLevelOut(BaseModel):
    org_id: int
    old_level: Literal["off", "shadow", "full"]
    new_level: Literal["off", "shadow", "full"]


@router.post(
    "/admin/orgs/{org_id}/telemetry-level",
    response_model=TelemetryLevelOut,
)
async def admin_set_telemetry_level(
    org_id: int,
    body: TelemetryLevelChange,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TelemetryLevelOut:
    """Operator-only endpoint to flip a tenant's telemetry mode (REQ-11).

    Auth: same X-Internal-Secret pattern as the rest of /internal/* — the
    caller is presumed to be a klai-operator. The audit row records
    ``operator_kind='operator'`` so it is distinguishable from the
    tenant-self-service path (REQ-15) in the audit-log UI.
    """
    await _require_internal_token(request)

    if not body.reason or not body.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reason must be non-empty",
        )
    if len(body.reason) > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reason exceeds 500-char limit",
        )

    from app.services.telemetry_level import set_telemetry_level

    # Internal admin path: operator identity is implicit in the
    # X-Internal-Secret bearer (no per-user JWT). We record a stable
    # synthetic actor so the audit-log row is non-empty; klai-operators
    # can correlate via the request_id in observability logs.
    operator_user_id = "internal-admin"

    try:
        old_level, new_level = await set_telemetry_level(
            db,
            org_id=org_id,
            new_level=body.level,
            operator_kind="operator",
            operator_user_id=operator_user_id,
            reason=body.reason,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    await _audit_internal_call(request, org_id=org_id)
    return TelemetryLevelOut(org_id=org_id, old_level=old_level, new_level=new_level)


class PageSavedNotification(BaseModel):
    kb_slug: str
    zitadel_org_id: str


@router.post("/v1/orgs/{org_id}/page-saved", status_code=status.HTTP_204_NO_CONTENT)
async def notify_page_saved(
    org_id: int,
    body: PageSavedNotification,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Notify portal-api that a page was saved in a Klai-native KB.

    Called by klai-docs after processing a Gitea push webhook. Schedules a
    gap re-scoring job with a 5-second delay to allow Qdrant indexing to complete.
    """
    await _require_internal_token(request)
    await schedule_rescore(
        org_id=org_id,
        zitadel_org_id=body.zitadel_org_id,
        kb_slug=body.kb_slug,
        db_factory=get_db,
        delay_seconds=5.0,
    )
    await _audit_internal_call(request, org_id=org_id)


class RetrievalLogIn(BaseModel):
    org_id: str  # Zitadel org ID string
    user_id: str  # LibreChat ObjectId
    chunk_ids: list[str]
    reranker_scores: list[float]
    query_resolved: str
    embedding_model_version: str
    retrieved_at: datetime
    # SPEC-MCP-RETRIEVAL-001 REQ-9: optional OAuth client attribution.
    # ``None`` (the default) = LibreChat traffic; populated = third-party
    # MCP client (Claude Desktop / Cursor / ChatGPT).
    caller_client_id: str | None = None


@router.post("/v1/retrieval-log", status_code=status.HTTP_201_CREATED)
async def post_retrieval_log(
    body: RetrievalLogIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a retrieval log from the LiteLLM knowledge hook (SPEC-KB-015).

    Resolves zitadel org_id string to portal int org_id, then writes to Redis.
    Silent discard on any error (REQ-KB-015-03).

    SPEC-PRIVACY-QUERY-SHADOW-001 REQ-9 (reinterpreted): the retrieval-log
    is Redis-backed (1h TTL JSON blob), NOT a Postgres table. The
    spec.md REQ-9 referenced ``knowledge.retrieval_logs.query_resolved``
    which does not exist on prod. The privacy contract here gates the
    raw ``query_resolved`` field within the Redis blob:
      - off    → skip the Redis write entirely
      - shadow → write blob with ``query_resolved=""`` (empty placeholder)
      - full   → write blob with literal query_resolved (existing behaviour)
    """
    await _require_internal_token(request)

    audit_org_id: int = 0
    try:
        org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == body.org_id))
        org = org_result.scalar_one_or_none()
        if org is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
        audit_org_id = org.id

        # SPEC-PRIVACY-QUERY-SHADOW-001 REQ-9: gate the Redis write on
        # the canonical telemetry_level (never trust the upstream-supplied
        # value for a privacy decision).
        if org.telemetry_level == "off":
            await _audit_internal_call(request, org_id=audit_org_id)
            return {"ok": True, "skipped": "telemetry_off"}

        # 'shadow' redacts the raw query content; chunk_ids /
        # reranker_scores / model version still flow (they're aggregates).
        effective_query_resolved = body.query_resolved if org.telemetry_level == "full" else ""

        from app.services.retrieval_log import write_retrieval_log

        await write_retrieval_log(
            org_id=org.id,
            user_id=body.user_id,
            chunk_ids=body.chunk_ids,
            reranker_scores=body.reranker_scores,
            query_resolved=effective_query_resolved,
            embedding_model_version=body.embedding_model_version,
            retrieved_at=body.retrieved_at,
            caller_client_id=body.caller_client_id,
        )
    except HTTPException:
        raise
    except Exception:
        # REQ-KB-015-03: silent discard on any error
        logger.warning("retrieval_log_endpoint_failed", exc_info=True)

    await _audit_internal_call(request, org_id=audit_org_id)
    return {"ok": True}


class KbFeedbackIn(BaseModel):
    conversation_id: str
    message_id: str
    message_created_at: datetime
    rating: Literal["thumbsUp", "thumbsDown"]
    tag: str | None = None
    text: str | None = None
    librechat_user_id: str
    librechat_tenant_id: str
    model_alias: str | None = None


# @MX:ANCHOR: [AUTO] Public API boundary for KB feedback from LibreChat. SPEC-KB-015.
# @MX:REASON: Called by LibreChat patch (feedback.cjs) + LiteLLM hook + tests. fan_in >= 3.
@router.post("/v1/kb-feedback", status_code=status.HTTP_201_CREATED, response_model=None)
async def post_kb_feedback(
    body: KbFeedbackIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Process feedback from LibreChat (SPEC-KB-015).

    1. Resolve librechat_tenant_id -> org_id
    2. Check idempotency (Redis)
    3. Correlate with retrieval log
    4. Insert feedback event (raw SQL for RLS)
    5. Schedule Qdrant update if correlated
    6. Emit product event
    """
    await _require_internal_token(request)

    # 1. Resolve tenant
    org_result = await db.execute(select(PortalOrg).where(PortalOrg.librechat_container == body.librechat_tenant_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown tenant")

    await set_tenant(db, org.id)

    # 2. Idempotency check (REQ-KB-015-12)
    redis_pool = await get_redis_pool()
    idem_key = f"fb:{body.message_id}:{body.conversation_id}"
    if redis_pool:
        existing = await redis_pool.get(idem_key)
        if existing:
            await _audit_internal_call(request, org_id=org.id)
            return Response(status_code=200)

    # 3. Time-window correlation (REQ-KB-015-09)
    correlated_log = await find_correlated_log(
        org_id=org.id,
        user_id=body.librechat_user_id,
        message_created_at=body.message_created_at,
    )

    chunk_ids = correlated_log["chunk_ids"] if correlated_log else []
    correlated = correlated_log is not None

    # 4. Insert feedback event via raw SQL (RLS table -- split SELECT/INSERT policies)
    await db.execute(
        text("""
            INSERT INTO portal_feedback_events
            (org_id, conversation_id, message_id, rating, tag, feedback_text,
             chunk_ids, correlated, model_alias, occurred_at)
            VALUES (:org_id, :conversation_id, :message_id, :rating, :tag,
                    :feedback_text, :chunk_ids, :correlated, :model_alias, NOW())
        """),
        {
            "org_id": org.id,
            "conversation_id": body.conversation_id,
            "message_id": body.message_id,
            "rating": body.rating,
            "tag": body.tag,
            "feedback_text": body.text,
            "chunk_ids": chunk_ids or None,
            "correlated": correlated,
            "model_alias": body.model_alias,
        },
    )
    await db.commit()

    # 5. Set idempotency key (REQ-KB-015-12)
    if redis_pool:
        try:
            await redis_pool.set(idem_key, "1", ex=3600)
        except Exception:
            logger.warning("kb_feedback_idem_key_set_failed", exc_info=True)

    # 6. Schedule Qdrant quality update if correlated (REQ-KB-015-14)
    if correlated and chunk_ids:
        schedule_quality_update(chunk_ids, body.rating, org.id)

    # 7. Emit product event (REQ-KB-015-22)
    emit_event(
        "knowledge.feedback",
        org_id=org.id,
        properties={
            "rating": body.rating,
            "correlated": correlated,
            "chunk_count": len(chunk_ids),
        },
    )

    await _audit_internal_call(request, org_id=org.id)
    return {"ok": True}


class GapEventIn(BaseModel):
    org_id: str  # Zitadel org ID from LiteLLM team key metadata
    user_id: str
    query_text: str
    gap_type: str
    top_score: float | None = None
    nearest_kb_slug: str | None = None
    chunks_retrieved: int = 0
    retrieval_ms: int = 0
    taxonomy_node_ids: list[int] | None = None  # SPEC-KB-022 R6: from LiteLLM hook
    # SPEC-MCP-RETRIEVAL-001 REQ-9: optional OAuth client attribution.
    # ``None`` (the default) = LibreChat traffic; populated = third-party
    # MCP client (Claude Desktop / Cursor / ChatGPT).
    caller_client_id: str | None = None


@router.post("/v1/gap-events", status_code=status.HTTP_201_CREATED)
async def create_gap_event(
    payload: GapEventIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Record a knowledge gap event from the LiteLLM hook.

    SPEC-PRIVACY-QUERY-SHADOW-001 REQ-8: gating by per-tenant
    telemetry_level — never trust the upstream-supplied value, always
    re-fetch the canonical level from portal_orgs.

    - off    → 200 OK, no row inserted
    - shadow → INSERT with query_text='[REDACTED:shadow]'
    - full   → INSERT with literal query_text (existing behavior)
    """
    await _require_internal_token(request)
    from app.models.retrieval_gaps import PortalRetrievalGap

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == payload.org_id))
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")
    await set_tenant(db, org.id)

    # REQ-8: 'off' → skip the INSERT entirely. Tenant accepts the
    # support-side trade-off; we still respond 200 to keep the
    # idempotent contract for fire-and-forget callers.
    if org.telemetry_level == "off":
        await _audit_internal_call(request, org_id=org.id)
        return {"ok": True, "skipped": "telemetry_off"}

    # REQ-8: 'shadow' → REDACT the literal query text. The matching
    # telemetry.query_shadow row (written by retrieval-api in Unit 3)
    # carries the embedding + features for support-team triage.
    effective_query_text = payload.query_text if org.telemetry_level == "full" else "[REDACTED:shadow]"

    gap = PortalRetrievalGap(
        org_id=org.id,
        user_id=payload.user_id,
        query_text=effective_query_text,
        gap_type=payload.gap_type,
        top_score=payload.top_score,
        nearest_kb_slug=payload.nearest_kb_slug,
        chunks_retrieved=payload.chunks_retrieved,
        retrieval_ms=payload.retrieval_ms,
        taxonomy_node_ids=payload.taxonomy_node_ids,
        caller_client_id=payload.caller_client_id,
    )
    db.add(gap)
    await db.commit()

    # SPEC-KB-022 R6 + SPEC-KB-026 R4: async gap classification via knowledge-ingest
    if payload.taxonomy_node_ids is None and payload.nearest_kb_slug:

        async def _classify_gap(
            gap_id: int,
            org_int_id: int,
            org_zitadel_id: str,
            query_text: str,
            kb_slug: str,
        ) -> None:
            """Classify gap query against KB taxonomy via knowledge-ingest.

            Background task on a fresh session: `tenant_scoped_session`
            guarantees the connection is pinned and app.current_org_id is
            set before the UPDATE, so RLS does not silently filter the row
            to zero. rowcount==0 raises; the RLS guard event listener also
            catches this as a safety net.
            """
            try:
                from app.core.database import tenant_scoped_session
                from app.services.knowledge_ingest_client import classify_gap_taxonomy

                node_ids = await classify_gap_taxonomy(org_zitadel_id, kb_slug, query_text)
                if not node_ids:
                    return

                async with tenant_scoped_session(org_int_id) as session:
                    result = await session.execute(
                        update(PortalRetrievalGap)
                        .where(PortalRetrievalGap.id == gap_id)
                        .values(taxonomy_node_ids=node_ids)
                    )
                    if result.rowcount == 0:  # type: ignore[attr-defined]
                        raise RuntimeError(
                            f"gap_classification UPDATE matched 0 rows "
                            f"(gap_id={gap_id}, org_id={org_int_id}) — "
                            f"likely RLS/tenant-context mismatch"
                        )
                    await session.commit()

                logger.info(
                    "gap_classification_complete: gap_id=%s, node_ids=%s",
                    gap_id,
                    node_ids,
                )
            except Exception:
                logger.exception(
                    "gap_classification_failed: gap_id=%s",
                    gap_id,
                )

        _task = asyncio.create_task(  # noqa: RUF006
            _classify_gap(
                gap.id,
                org.id,
                payload.org_id,
                payload.query_text,
                payload.nearest_kb_slug,
            )
        )

    await _audit_internal_call(request, org_id=org.id)
    return {"ok": True}


class OnboardingStartRequest(BaseModel):
    """Body for /internal/onboarding/start.

    Triggered from a Twenty CRM Workflow's manual "Start onboarding"
    button on a Person record. The Workflow's HTTP-action posts the
    person's email + name + the Cal.com booking link. portal-api sends
    listmonk's transactional template ``onboarding_invite``.
    """

    name: str
    email: str | None = None
    cal_url: str | None = None


class OnboardingStartResponse(BaseModel):
    sent: bool
    subject: str = ""
    body_html: str = ""
    cal_url: str = ""
    sent_to: str = ""


class MailingSyncContactRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: str | None = None
    name: str | None = None
    source: str = Field(min_length=1, max_length=80)
    audiences: list[Literal["signups", "users", "updates_opt_in", "crm_selected"]] = Field(min_length=1)
    company: str | None = None
    twenty_person_id: str | None = Field(default=None, alias="twentyPersonId")
    portal_user_id: int | None = Field(default=None, alias="portalUserId")
    zitadel_user_id: str | None = Field(default=None, alias="zitadelUserId")
    org_id: int | None = Field(default=None, alias="orgId")
    product: str | None = None
    marketing_consent: bool | None = Field(default=None, alias="marketingConsent")


class MailingSyncContactResponse(BaseModel):
    synced: bool
    subscriber_id: int
    lists_added: list[int]


class MailingSendRequest(BaseModel):
    template: Literal["onboarding_invite"]
    email: str | None = None
    name: str
    cal_url: str | None = None


class MailingSendResponse(BaseModel):
    sent: bool
    template: str
    template_id: int
    sent_to: str


def _normalise_mailing_email(email: str | None) -> str:
    email_norm = (email or "").strip().lower()
    if "@" not in email_norm or "." not in email_norm.rsplit("@", 1)[-1]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A valid email is required")
    return email_norm


@router.post("/onboarding/start", response_model=OnboardingStartResponse)
async def start_onboarding_drip(
    request: Request,
    body: OnboardingStartRequest,
) -> OnboardingStartResponse:
    """Send the onboarding Mail 1 (welcome + Cal booking-CTA) to a waitlister.

    Auth: same ``Authorization: Bearer <INTERNAL_SECRET>`` pattern as
    every other ``/internal/*`` endpoint. The Twenty Workflow HTTP-action
    is the canonical caller; cURL with the same Bearer also works for
    manual triggers.

    Returns 200 with the rendered subject/body_html so the caller (Twenty
    Workflow run log + downstream CREATE_RECORD Note step) can show the
    operator exactly what was sent. Returns 502 if the mailer rejected
    (4xx/5xx) or was unreachable. The downstream rate-limit (per-recipient
    Redis bucket on the mailer) means duplicate clicks within the cooldown
    window will surface as 502 here -- that is the safety against the
    operator double-clicking the workflow button.
    """
    await _require_internal_token(request)

    from app.services import listmonk

    email = _normalise_mailing_email(body.email)
    cal_url = body.cal_url or "https://cal.getklai.com/klai/onboarding-intake"

    try:
        result = await listmonk.send_onboarding_invite(
            name=body.name,
            email=email,
            cal_url=cal_url,
        )
    except (listmonk.ListmonkUnavailable, listmonk.ListmonkAPIError) as exc:
        await _audit_internal_call(request, org_id=0)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="listmonk rejected or unreachable",
        ) from exc

    # Cross-tenant operation (the Person may belong to any tenant in Twenty),
    # so we audit with org_id=0 like /librechat/regenerate.
    await _audit_internal_call(request, org_id=0)

    return OnboardingStartResponse(
        sent=result.sent,
        subject="Welcome to Klai, you're in",
        body_html="",
        cal_url=cal_url,
        sent_to=result.sent_to,
    )


@router.post("/mailing/sync-contact", response_model=MailingSyncContactResponse)
async def mailing_sync_contact(
    request: Request,
    body: MailingSyncContactRequest,
) -> MailingSyncContactResponse:
    """Sync a CRM/signup/user contact to listmonk mailing lists."""
    await _require_internal_token(request)

    from app.services import listmonk

    email = _normalise_mailing_email(body.email)
    try:
        result = await listmonk.sync_contact(
            email=email,
            name=body.name,
            source=body.source,
            audiences=[str(audience) for audience in body.audiences],
            company=body.company,
            twenty_person_id=body.twenty_person_id,
            portal_user_id=body.portal_user_id,
            zitadel_user_id=body.zitadel_user_id,
            org_id=body.org_id,
            product=body.product,
            marketing_consent=body.marketing_consent,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (listmonk.ListmonkUnavailable, listmonk.ListmonkAPIError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="listmonk rejected or unreachable") from exc

    await _audit_internal_call(request, org_id=body.org_id or 0)
    return MailingSyncContactResponse(
        synced=True,
        subscriber_id=result.subscriber_id,
        lists_added=result.lists_added,
    )


@router.post("/mailing/send", response_model=MailingSendResponse)
async def mailing_send(
    request: Request,
    body: MailingSendRequest,
) -> MailingSendResponse:
    """Send a supported non-auth transactional mail through listmonk."""
    await _require_internal_token(request)

    from app.services import listmonk

    email = _normalise_mailing_email(body.email)
    cal_url = body.cal_url or "https://cal.getklai.com/klai/onboarding-intake"
    try:
        result = await listmonk.send_onboarding_invite(
            email=email,
            name=body.name,
            cal_url=cal_url,
        )
    except (listmonk.ListmonkUnavailable, listmonk.ListmonkAPIError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="listmonk rejected or unreachable") from exc

    await _audit_internal_call(request, org_id=0)
    return MailingSendResponse(
        sent=result.sent,
        template=body.template,
        template_id=result.template_id,
        sent_to=result.sent_to,
    )


class RegenerateResponse(BaseModel):
    tenants_updated: list[str]
    errors: list[str]


@router.post("/librechat/regenerate", response_model=RegenerateResponse)
async def regenerate_librechat_configs(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RegenerateResponse:
    """Regenerate per-tenant librechat.yaml from the base template for all active tenants.

    Called by CI after syncing a new base librechat.yaml to the server.
    For each tenant: re-runs _generate_librechat_yaml with the tenant's MCP servers,
    writes the result, flushes Redis, and restarts the container.
    """
    await _require_internal_token(request)

    import docker

    from app.services.provisioning.generators import _generate_librechat_yaml

    base_yaml_path = Path(settings.librechat_container_data_path) / "librechat.yaml"
    if not base_yaml_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Base config not found at {base_yaml_path}",
        )

    result = await db.execute(select(PortalOrg).where(PortalOrg.provisioning_status == "ready"))
    tenants = result.scalars().all()

    updated: list[str] = []
    errors: list[str] = []
    loop = asyncio.get_running_loop()

    # Step 1: Regenerate all tenant configs from the updated base template
    slugs_to_restart: list[str] = []
    for org in tenants:
        slug = org.slug
        if not slug:
            continue
        try:
            tenant_yaml_content = _generate_librechat_yaml(base_yaml_path, org.mcp_servers)
            tenant_yaml_dir = Path(settings.librechat_container_data_path) / slug
            tenant_yaml_dir.mkdir(parents=True, exist_ok=True)
            (tenant_yaml_dir / "librechat.yaml").write_text(tenant_yaml_content)
            slugs_to_restart.append(slug)
            updated.append(slug)
            logger.info("Regenerated config for tenant %s", slug)
        except Exception as exc:
            errors.append(f"{slug}: {exc}")
            logger.warning("Config regeneration failed for %s: %s", slug, exc, exc_info=True)

    if not slugs_to_restart:
        # Cross-tenant operation — no resolvable org_id. Use 0 per REQ-2.6.
        await _audit_internal_call(request, org_id=0)
        return RegenerateResponse(tenants_updated=updated, errors=errors)

    # Step 2: Targeted invalidation of the LibreChat config cache via protocol
    # (NOT docker exec -- SEC-021 docker-socket-proxy denies /exec/*/start).
    #
    # SPEC-SEC-INTERNAL-001 REQ-2: this previously called FLUSHALL, which
    # cleared every key in the Redis namespace -- including unrelated rate-limit
    # buckets, SSO cache rows, and partner-API state for every tenant. We now
    # SCAN MATCH the configured pattern (``configs:*`` by default per REQ-2.3,
    # tunable via ``LIBRECHAT_CACHE_KEY_PATTERN``) and UNLINK each match in
    # batches. UNLINK is non-blocking; SCAN with ``count=100`` keeps memory
    # bounded on large key spaces.
    #
    # Failure-mode: if cache invalidation raises, we still continue to the
    # container-restart step (REQ-2.5) -- LibreChat re-reads the yaml from
    # disk on startup, so the restart is the belt-and-braces recovery for a
    # partial invalidation.
    try:
        redis_client = aioredis.Redis(
            host=settings.redis_host,
            port=6379,
            password=settings.redis_password or None,
            decode_responses=True,
        )
        async with redis_client:
            cache_pattern = settings.librechat_cache_key_pattern
            deleted = 0
            batch: list[str] = []
            async for key in redis_client.scan_iter(match=cache_pattern, count=100):
                batch.append(key)
                if len(batch) >= 100:
                    deleted += await redis_client.unlink(*batch)
                    batch.clear()
            if batch:
                deleted += await redis_client.unlink(*batch)
        structlog_logger.info(
            "librechat_cache_invalidated",
            pattern=cache_pattern,
            deleted=deleted,
        )
    except RedisError as exc:
        structlog_logger.warning(
            "librechat_cache_invalidation_failed",
            pattern=settings.librechat_cache_key_pattern,
            exc_info=True,
        )
        errors.append(f"redis-cache-invalidation: {exc}")

    # Step 3: Restart all tenant containers via docker-socket-proxy.
    # Only /containers/{id}/restart is called here — allowed by CONTAINERS=1 + POST=1.
    def _restart_all(slugs: list[str]) -> list[str]:
        client = docker.from_env()
        restart_errors: list[str] = []
        for slug in slugs:
            container_name = validate_slug_for_provisioning(slug, domain=settings.domain).librechat_container
            try:
                ctr = client.containers.get(container_name)
                ctr.restart(timeout=10)
                logger.info("Restarted container %s", container_name)
            except docker.errors.APIError as exc:  # type: ignore[attr-defined]
                restart_errors.append(f"{slug}: {exc}")
                logger.warning("Restart failed for %s: %s", container_name, exc)
        return restart_errors

    restart_errors = await loop.run_in_executor(None, _restart_all, slugs_to_restart)
    errors.extend(restart_errors)

    await _audit_internal_call(request, org_id=0)
    return RegenerateResponse(tenants_updated=updated, errors=errors)


# ---------------------------------------------------------------------------
# SPEC-CHAT-TEMPLATES-001: GET /internal/templates/effective
#
# Called by the LiteLLM pre-call hook once per (org, user) chat session, cached
# 30s in-process on the hook side. Returns the list of prompt-template
# instructions to prepend to the system message.
#
# Fail-safe design (REQ-TEMPLATES-INTERNAL-E2): an unknown librechat_user_id
# (the user hasn't called chat yet, so no PortalUser mapping row exists)
# returns 200 with empty instructions — NEVER 404. Chat must never break
# because the mapping is missing.
#
# RLS: we resolve the org first, then call set_tenant() before any
# portal_templates query so the strict tenant_isolation policy admits the
# SELECT.
# ---------------------------------------------------------------------------


class TemplateInstruction(BaseModel):
    source: Literal["template"] = "template"
    name: str
    text: str


class TemplatesEffectiveResponse(BaseModel):
    instructions: list[TemplateInstruction]


@router.get("/templates/effective", response_model=TemplatesEffectiveResponse)
async def get_effective_templates(
    request: Request,
    zitadel_org_id: str,
    librechat_user_id: str,
    db: AsyncSession = Depends(get_db),
) -> TemplatesEffectiveResponse:
    """Resolve effective prompt templates for a (org, librechat_user) pair.

    Contract (SPEC-CHAT-TEMPLATES-001 REQ-TEMPLATES-INTERNAL):
    - 401 on missing/bad bearer (before any DB access).
    - 404 when the Zitadel org is unknown (config-fout).
    - 200 with empty instructions when:
        * librechat_user_id has no PortalUser row (fail-safe),
        * the user has NULL or empty active_template_ids,
        * every referenced template is inactive or deleted.
    - 200 with instructions[] in the order active_template_ids specifies,
      skipping inactive/missing templates silently.
    """
    await _require_internal_token(request)

    # Resolve org before set_tenant so an unknown zitadel_org_id still 404s.
    org_row = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
    org = org_row.scalar_one_or_none()
    if org is None:
        await _audit_internal_call(request, org_id=0)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    # Scope RLS to this org for all subsequent queries.
    await set_tenant(db, org.id)

    user_row = await db.execute(
        select(PortalUser).where(
            PortalUser.org_id == org.id,
            PortalUser.librechat_user_id == librechat_user_id,
        )
    )
    user = user_row.scalar_one_or_none()
    if user is None or not user.active_template_ids:
        # Fail-safe: missing mapping or no active templates → empty.
        await _audit_internal_call(request, org_id=org.id)
        return TemplatesEffectiveResponse(instructions=[])

    template_ids = list(user.active_template_ids)

    tpl_rows = await db.execute(
        select(PortalTemplate).where(
            PortalTemplate.org_id == org.id,
            PortalTemplate.id.in_(template_ids),
            PortalTemplate.is_active.is_(True),
        )
    )
    tpl_by_id = {t.id: t for t in tpl_rows.scalars().all()}

    # Preserve user-specified order; skip ids that don't map (deleted or inactive).
    instructions: list[TemplateInstruction] = []
    for tid in template_ids:
        tpl = tpl_by_id.get(tid)
        if tpl is None:
            continue
        instructions.append(TemplateInstruction(name=tpl.name, text=tpl.prompt_text))

    await _audit_internal_call(request, org_id=org.id)
    return TemplatesEffectiveResponse(instructions=instructions)


# ---------------------------------------------------------------------------
# SPEC-SEC-IDENTITY-ASSERT-001 REQ-1: /internal/identity/verify
# ---------------------------------------------------------------------------
#
# Source-of-truth for "is the claimed (user, org) tuple real?". Every
# Klai service that carries a tenant or user identity claim consults
# this endpoint via the shared library at klai-libs/identity-assert/.
#
# The endpoint is the THIN HTTP wrapper; verification logic lives in
# app.services.identity_verifier and the Redis cache lives in
# app.services.identity_verify_cache. This separation means service
# layer is unit-testable without spinning up a TestClient, and the
# cache layer is swappable in isolation.


class IdentityVerifyRequest(BaseModel):
    """Request body for POST /internal/identity/verify (REQ-1.1).

    ``claimed_org_slug`` is REQ-2.6: when present, the canonical
    ``portal_orgs.slug`` for the verified org must match the value the caller
    asserts (typically forwarded from a service-to-service ``X-Org-Slug``
    header). Mismatch yields ``org_slug_mismatch``. When absent, the slug is
    not checked but is still returned in the success body so callers can
    construct upstream URLs from a verified value rather than the caller-
    asserted header.
    """

    caller_service: str
    claimed_user_id: str
    claimed_org_id: str
    bearer_jwt: str | None = None
    claimed_org_slug: str | None = None


class IdentityVerifySuccess(BaseModel):
    """200 response when the claim is verified.

    ``org_slug`` is the canonical ``portal_orgs.slug`` for the verified org
    (REQ-2.6). Always populated — callers SHOULD use this value when building
    upstream URLs (e.g. klai-docs ``/api/orgs/{org_slug}/...``) instead of the
    caller-asserted ``X-Org-Slug`` header.
    """

    verified: Literal[True] = True
    user_id: str
    org_id: str
    org_slug: str
    cache_ttl_seconds: int
    # ``partner_key`` (F2 fix-forward, retrieval coupling audit 2026-05-06):
    # evidence used for synthetic ``partner:<key_id>`` identities verified
    # against partner_api_keys.
    evidence: Literal["jwt", "membership", "partner_key"]


class IdentityVerifyDeny(BaseModel):
    """403/400/503 response body when the claim is denied or unverifiable."""

    verified: Literal[False] = False
    reason: str


def _hash_zitadel_id(value: str) -> str:
    """16-hex-char SHA-256 prefix.

    Same convention as ``_hash_sub`` in
    ``klai-retrieval-api/retrieval_api/middleware/auth.py``. Keeps
    structlog entries free of raw UUIDs / Zitadel IDs (REQ-1.7).
    """

    import hashlib  # local import to avoid touching module-level imports

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


_identity_jwks_client: PyJWKClient | None = None


def _get_identity_jwks_resolver() -> PyJWKClient:
    """Return the process-wide PyJWKClient for end-user JWT validation.

    Reuses the same Zitadel JWKS endpoint as bff_oidc — Zitadel signs all
    tokens (id_tokens AND access_tokens) with the same key set. We do NOT
    reuse ``app.services.bff_oidc._get_jwks_client`` directly because that
    helper is private; instead we instantiate our own client backed by the
    same URL so the two don't share cache state (avoids contamination if
    bff_oidc rotates its client for any reason).
    """

    global _identity_jwks_client
    if _identity_jwks_client is None:
        _identity_jwks_client = PyJWKClient(
            f"{settings.zitadel_base_url}/oauth/v2/keys",
            cache_keys=True,
            max_cached_keys=16,
            lifespan=3600,
        )
    return _identity_jwks_client


@router.post(
    "/identity/verify",
    response_model=None,  # union response — FastAPI serialises via the explicit Response object
)
async def verify_identity(
    request: Request,
    body: IdentityVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Verify a service-asserted identity claim against portal source-of-truth.

    Implements SPEC-SEC-IDENTITY-ASSERT-001 REQ-1. See the SPEC and
    ``app/services/identity_verifier.py`` for the contract; this handler is
    the HTTP layer + cache + structured-log wrapper.

    Failure modes (REQ-1.2 / 1.3 / 1.4 / 1.6 / 2.6):

    - ``unknown_caller_service`` → HTTP 400 (caller is misconfigured; loud
      failure rather than silent rate-limit consumption).
    - ``invalid_jwt`` → HTTP 403 (the caller forwarded a JWT that fails
      signature/exp validation; never falls back to membership — REQ-1.8).
    - ``jwt_identity_mismatch`` → HTTP 403 (the JWT is valid but its sub /
      resourceowner do not match the claimed tuple).
    - ``no_membership`` → HTTP 403 (membership lookup found no active
      ``portal_users`` row).
    - ``org_slug_mismatch`` → HTTP 403 (REQ-2.6: ``claimed_org_slug`` does
      not match the canonical ``portal_orgs.slug`` for the verified org).
    - ``cache_unavailable`` → HTTP 503 (Redis call failed; auth-class
      control fails closed — REQ-1.6).
    """

    from app.services.identity_verifier import (
        KNOWN_CALLER_SERVICES,
        UserBoundEvidence,
        verify_identity_claim,
    )
    from app.services.identity_verifier import (
        evidence_path as _evidence_path,
    )
    from app.services.identity_verify_cache import (
        CacheUnavailable,
        cache_verified_decision,
        get_cached_decision,
    )

    await _require_internal_token(request)

    # REQ-1.2: unknown caller_service is a 400 (not silenced into rate-limit).
    if body.caller_service not in KNOWN_CALLER_SERVICES:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash=_hash_zitadel_id(body.claimed_user_id),
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="unknown_caller_service",
        )
        return Response(
            content=IdentityVerifyDeny(reason="unknown_caller_service").model_dump_json(),
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )

    # REQ-1.5: cache lookup before any DB or JWT work.
    redis_pool = await get_redis_pool()
    cache_user_id_hash = _hash_zitadel_id(body.claimed_user_id)

    if redis_pool is None:
        # No Redis → fail closed (REQ-1.6). This branch is hit when the pool
        # was never initialised; transient errors are caught by CacheUnavailable.
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash=cache_user_id_hash,
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="cache_unavailable",
        )
        return Response(
            content=IdentityVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    try:
        cached = await get_cached_decision(
            redis=redis_pool,
            caller_service=body.caller_service,
            claimed_user_id=body.claimed_user_id,
            claimed_org_id=body.claimed_org_id,
            bearer_jwt=body.bearer_jwt,
        )
    except CacheUnavailable:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash=cache_user_id_hash,
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="cache_unavailable",
        )
        return Response(
            content=IdentityVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    if (
        cached is not None
        and cached.evidence is not None
        and cached.user_id is not None
        and cached.org_id is not None
        and cached.org_slug is not None
    ):
        # REQ-2.6: even on a cache hit, an asserted ``claimed_org_slug`` must
        # still match the canonical slug we resolved at first verification.
        # The slug for a given org_id is stable within the 60s TTL so the
        # cached value is authoritative — no need to re-hit the DB.
        if body.claimed_org_slug is not None and body.claimed_org_slug != cached.org_slug:
            await _audit_internal_call(request, org_id=0)
            structlog_logger.warning(
                "identity_verify_decision",
                caller_service=body.caller_service,
                claimed_user_id_hash=cache_user_id_hash,
                claimed_org_id=body.claimed_org_id,
                verified=False,
                reason="org_slug_mismatch",
                cache_hit=True,
            )
            return Response(
                content=IdentityVerifyDeny(reason="org_slug_mismatch").model_dump_json(),
                status_code=status.HTTP_403_FORBIDDEN,
                media_type="application/json",
            )

        await _audit_internal_call(request, org_id=0)
        structlog_logger.info(
            "identity_verify_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash=cache_user_id_hash,
            claimed_org_id=body.claimed_org_id,
            verified=True,
            evidence=cached.evidence,
            evidence_path=_evidence_path(cached.evidence),
            cache_hit=True,
        )
        return Response(
            content=IdentityVerifySuccess(
                user_id=cached.user_id,
                org_id=cached.org_id,
                org_slug=cached.org_slug,
                cache_ttl_seconds=60,
                # cached.evidence is UserBoundEvidence — the user-bound cache
                # only stores "jwt" / "membership" / "partner_key" values.
                evidence=cast("UserBoundEvidence", cached.evidence),
            ).model_dump_json(),
            status_code=status.HTTP_200_OK,
            media_type="application/json",
        )

    # Cache miss: run the verifier.
    decision = await verify_identity_claim(
        db=db,
        jwks_resolver=_get_identity_jwks_resolver(),
        caller_service=body.caller_service,
        claimed_user_id=body.claimed_user_id,
        claimed_org_id=body.claimed_org_id,
        bearer_jwt=body.bearer_jwt,
        claimed_org_slug=body.claimed_org_slug,
    )

    if not decision.verified:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash=cache_user_id_hash,
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason=decision.reason,
            cache_hit=False,
        )
        return Response(
            content=IdentityVerifyDeny(reason=decision.reason or "unknown").model_dump_json(),
            status_code=status.HTTP_403_FORBIDDEN,
            media_type="application/json",
        )

    # Verified: cache and return 200.
    try:
        await cache_verified_decision(
            redis=redis_pool,
            caller_service=body.caller_service,
            claimed_user_id=body.claimed_user_id,
            claimed_org_id=body.claimed_org_id,
            decision=decision,
        )
    except CacheUnavailable:
        # Cache write failed AFTER a successful verification. We MUST NOT
        # return the verified decision — see REQ-1.6 "auth-class control
        # must not silently downgrade". A flap that disables the cache
        # would amplify DB load on the next call; failing this single
        # request closes that loop.
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash=cache_user_id_hash,
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="cache_unavailable",
        )
        return Response(
            content=IdentityVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    await _audit_internal_call(request, org_id=0)
    assert (
        decision.user_id is not None
        and decision.org_id is not None
        and decision.org_slug is not None
        and decision.evidence is not None
    )
    structlog_logger.info(
        "identity_verify_decision",
        caller_service=body.caller_service,
        claimed_user_id_hash=cache_user_id_hash,
        claimed_org_id=body.claimed_org_id,
        verified=True,
        evidence=decision.evidence,
        evidence_path=_evidence_path(decision.evidence),
        cache_hit=False,
    )
    return Response(
        content=IdentityVerifySuccess(
            user_id=decision.user_id,
            org_id=decision.org_id,
            org_slug=decision.org_slug,
            cache_ttl_seconds=60,
            # decision.evidence is UserBoundEvidence here — verify_identity_claim
            # only produces "jwt", "membership", or "partner_key" for the
            # user-bound path.
            evidence=cast("UserBoundEvidence", decision.evidence),
        ).model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# SPEC-SEC-IDENTITY-ASSERT-001 tenant-only path: /internal/identity/verify-tenant
# ---------------------------------------------------------------------------
#
# Opt-in primitive for service-to-service calls that carry no end-user identity
# (e.g. portal-api → knowledge-ingest stats endpoints). Separated from
# /internal/identity/verify so the type system prevents a user-bound endpoint
# from accidentally receiving a tenant-only result when claimed_user_id=None.
# See the architecture decision in identity.py and the retro entry for the
# 2026-05-06 crash.


class IdentityVerifyTenantRequest(BaseModel):
    """Request body for POST /internal/identity/verify-tenant.

    Intentionally has NO ``claimed_user_id`` and NO ``bearer_jwt`` fields.
    A tenant-only call with a JWT would be a contract violation — the JWT
    asserts an end-user, which is precisely what this path does not have.
    """

    caller_service: str
    claimed_org_id: str
    claimed_org_slug: str | None = None


class IdentityVerifyTenantSuccess(BaseModel):
    """200 response body for the tenant-only verification endpoint.

    Intentionally has NO ``user_id`` field — there is no end-user on this path.
    """

    verified: Literal[True] = True
    org_id: str
    org_slug: str
    cache_ttl_seconds: int
    evidence: Literal["tenant_only"]


@router.post(
    "/identity/verify-tenant",
    response_model=None,
)
async def verify_tenant_identity(
    request: Request,
    body: IdentityVerifyTenantRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Verify a tenant-only service-asserted identity claim.

    Implements the tenant-only split from SPEC-SEC-IDENTITY-ASSERT-001. Used
    by knowledge-ingest stats endpoints (and any future tenant-level endpoints)
    where there is no end-user. The response body carries no ``user_id`` field.

    Failure modes:
    - ``unknown_caller_service`` → HTTP 400.
    - ``tenant_not_found``       → HTTP 403 (org_id has no live portal_orgs row).
    - ``org_slug_mismatch``      → HTTP 403 (REQ-2.6: slug mismatch).
    - ``cache_unavailable``      → HTTP 503 (Redis call failed; fails closed).
    """

    from app.services.identity_verifier import (
        KNOWN_CALLER_SERVICES,
        verify_tenant_claim,
    )
    from app.services.identity_verify_cache import (
        CacheUnavailable,
        cache_verified_tenant_decision,
        get_cached_tenant_decision,
    )

    await _require_internal_token(request)

    if body.caller_service not in KNOWN_CALLER_SERVICES:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_tenant_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash="<service>",
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="unknown_caller_service",
        )
        return Response(
            content=IdentityVerifyDeny(reason="unknown_caller_service").model_dump_json(),
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )

    redis_pool = await get_redis_pool()

    if redis_pool is None:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_tenant_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash="<service>",
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="cache_unavailable",
        )
        return Response(
            content=IdentityVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    try:
        cached = await get_cached_tenant_decision(
            redis=redis_pool,
            caller_service=body.caller_service,
            claimed_org_id=body.claimed_org_id,
        )
    except CacheUnavailable:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_tenant_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash="<service>",
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="cache_unavailable",
        )
        return Response(
            content=IdentityVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    if cached is not None and cached.org_id is not None and cached.org_slug is not None:
        if body.claimed_org_slug is not None and body.claimed_org_slug != cached.org_slug:
            await _audit_internal_call(request, org_id=0)
            structlog_logger.warning(
                "identity_verify_tenant_decision",
                caller_service=body.caller_service,
                claimed_user_id_hash="<service>",
                claimed_org_id=body.claimed_org_id,
                verified=False,
                reason="org_slug_mismatch",
                cache_hit=True,
            )
            return Response(
                content=IdentityVerifyDeny(reason="org_slug_mismatch").model_dump_json(),
                status_code=status.HTTP_403_FORBIDDEN,
                media_type="application/json",
            )

        await _audit_internal_call(request, org_id=0)
        structlog_logger.info(
            "identity_verify_tenant_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash="<service>",
            claimed_org_id=body.claimed_org_id,
            verified=True,
            evidence="tenant_only",
            cache_hit=True,
        )
        return Response(
            content=IdentityVerifyTenantSuccess(
                org_id=cached.org_id,
                org_slug=cached.org_slug,
                cache_ttl_seconds=60,
                evidence="tenant_only",
            ).model_dump_json(),
            status_code=status.HTTP_200_OK,
            media_type="application/json",
        )

    # Cache miss: run the verifier.
    decision = await verify_tenant_claim(
        db=db,
        caller_service=body.caller_service,
        claimed_org_id=body.claimed_org_id,
        claimed_org_slug=body.claimed_org_slug,
    )

    if not decision.verified:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_tenant_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash="<service>",
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason=decision.reason,
            cache_hit=False,
        )
        return Response(
            content=IdentityVerifyDeny(reason=decision.reason or "unknown").model_dump_json(),
            status_code=status.HTTP_403_FORBIDDEN,
            media_type="application/json",
        )

    # Verified: cache and return 200.
    try:
        await cache_verified_tenant_decision(
            redis=redis_pool,
            caller_service=body.caller_service,
            claimed_org_id=body.claimed_org_id,
            decision=decision,
        )
    except CacheUnavailable:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "identity_verify_tenant_decision",
            caller_service=body.caller_service,
            claimed_user_id_hash="<service>",
            claimed_org_id=body.claimed_org_id,
            verified=False,
            reason="cache_unavailable",
        )
        return Response(
            content=IdentityVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    await _audit_internal_call(request, org_id=0)
    assert decision.org_id is not None and decision.org_slug is not None
    structlog_logger.info(
        "identity_verify_tenant_decision",
        caller_service=body.caller_service,
        claimed_user_id_hash="<service>",
        claimed_org_id=body.claimed_org_id,
        verified=True,
        evidence="tenant_only",
        cache_hit=False,
    )
    return Response(
        content=IdentityVerifyTenantSuccess(
            org_id=decision.org_id,
            org_slug=decision.org_slug,
            cache_ttl_seconds=60,
            evidence="tenant_only",
        ).model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# SPEC-TAXONOMY-V2-001: KB metadata endpoint for knowledge-ingest bootstrap
# ---------------------------------------------------------------------------


class KbMetadataResponse(BaseModel):
    slug: str
    description: str | None


@router.get(
    "/knowledge-bases/{kb_slug}/metadata",
    response_model=KbMetadataResponse,
)
async def get_kb_metadata_internal(
    kb_slug: str,
    zitadel_org_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> KbMetadataResponse:
    """Return KB metadata (description) for knowledge-ingest bootstrap.

    SPEC-TAXONOMY-V2-001 AC-5: provides kb.description for the LLM naming prompt.
    Requires ?zitadel_org_id to scope the RLS tenant.
    Returns 404 when KB not found — knowledge-ingest treats this as best-effort.
    """
    await _require_internal_token(request)

    org_result = await db.execute(select(PortalOrg).where(PortalOrg.zitadel_org_id == zitadel_org_id))
    org = org_result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Org not found")

    await set_tenant(db, org.id)
    await _audit_internal_call(request, org_id=org.id)

    kb_result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.slug == kb_slug,
            PortalKnowledgeBase.org_id == org.id,
        )
    )
    kb = kb_result.scalar_one_or_none()
    if not kb:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="KB not found")

    return KbMetadataResponse(slug=kb.slug, description=kb.description)


# ============================================================================
# SPEC-MCP-AUTH-001 REQ-9: /internal/mcp-token/verify
# ============================================================================
#
# Source-of-truth for "is this raw bearer token valid?". Called by
# klai-knowledge-mcp via the McpTokenAsserter shared library on every tool
# invocation. Mirrors the /internal/identity/verify pattern for shape +
# Redis-cached fail-closed semantics.


class McpTokenVerifyRequest(BaseModel):
    """Request body for ``POST /internal/mcp-token/verify``.

    The raw token is forwarded so portal-api can hash + lookup. We never
    expose the hash on the wire — that would let an attacker who can sniff
    internal traffic enumerate token-rows by pre-computed hash dictionaries.
    """

    caller_service: str
    raw_token: str


class McpTokenVerifySuccess(BaseModel):
    """200 response when the token is valid.

    ``user_id`` and ``org_id`` are strings (zitadel_user_id and
    str(portal_orgs.id) respectively) to mirror the existing
    /internal/identity/verify wire shape — knowledge-ingest and klai-docs
    callers expect strings.
    """

    verified: Literal[True] = True
    user_id: str
    org_id: str
    org_slug: str | None
    scopes: list[str]
    resource_uri: str
    cache_ttl_seconds: int


class McpTokenVerifyDeny(BaseModel):
    """403/503 response body when the token is denied or unverifiable."""

    verified: Literal[False] = False
    reason: str


@router.post(
    "/mcp-token/verify",
    response_model=None,
)
async def verify_mcp_token(
    request: Request,
    body: McpTokenVerifyRequest,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Verify a klai_mcp_<...> bearer token, return verified identity tuple.

    SPEC-MCP-AUTH-001 REQ-9 + REQ-12 + REQ-15-19. Failure modes:

    - ``HTTP 400 unknown_caller_service`` — body.caller_service not in the
      known-callers list.
    - ``HTTP 403 invalid_format`` — token missing the klai_mcp_ prefix.
    - ``HTTP 403 unknown_token`` — hash not in DB.
    - ``HTTP 403 token_revoked`` / ``token_expired`` / ``audience_mismatch``
      / ``user_inactive`` / ``org_deprovisioning``.
    - ``HTTP 503 cache_unavailable`` — Redis down (auth-class fail-closed).
    """
    from app.services.mcp_oauth import verify_access_token

    await _require_internal_token(request)

    if body.caller_service not in {"knowledge-mcp", "scribe-api", "retrieval-api"}:
        await _audit_internal_call(request, org_id=0)
        return Response(
            content=McpTokenVerifyDeny(reason="unknown_caller_service").model_dump_json(),
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="application/json",
        )

    redis_pool = await get_redis_pool()
    if redis_pool is None:
        await _audit_internal_call(request, org_id=0)
        return Response(
            content=McpTokenVerifyDeny(reason="cache_unavailable").model_dump_json(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/json",
        )

    # The verify lookup needs cross-org access (we don't yet know which org
    # owns the token). cross_org_session bypasses RLS via the privileged
    # role; the verifier then narrows the result via WHERE access_token_hash.
    from app.core.database import cross_org_session

    async with cross_org_session() as priv_db:
        result = await verify_access_token(
            priv_db,
            redis_pool,
            raw_token=body.raw_token,
            expected_resource=settings.mcp_oauth_resource_url,
        )

    if not result.verified:
        await _audit_internal_call(request, org_id=0)
        structlog_logger.warning(
            "mcp_token_verify_decision",
            caller_service=body.caller_service,
            verified=False,
            reason=result.reason,
        )
        # cache_unavailable is the only auth-class failure that should be 503;
        # everything else is a 403 (token-specific deny).
        status_code = (
            status.HTTP_503_SERVICE_UNAVAILABLE if result.reason == "cache_unavailable" else status.HTTP_403_FORBIDDEN
        )
        return Response(
            content=McpTokenVerifyDeny(reason=result.reason or "unknown").model_dump_json(),
            status_code=status_code,
            media_type="application/json",
        )

    # Verified: emit audit + return 200.
    # result.org_id is str (zitadel-id-style); _audit_internal_call expects int.
    audit_org_id = int(result.org_id) if result.org_id and result.org_id.isdigit() else 0
    await _audit_internal_call(request, org_id=audit_org_id)
    structlog_logger.info(
        "mcp_token_verify_decision",
        caller_service=body.caller_service,
        verified=True,
        user_id=result.user_id,
        org_id=result.org_id,
    )
    return Response(
        content=McpTokenVerifySuccess(
            user_id=result.user_id,  # type: ignore[arg-type]
            org_id=result.org_id,  # type: ignore[arg-type]
            org_slug=result.org_slug,
            scopes=list(result.scopes),
            resource_uri=result.resource_uri or "",
            cache_ttl_seconds=result.cache_ttl_seconds,
        ).model_dump_json(),
        status_code=status.HTTP_200_OK,
        media_type="application/json",
    )
