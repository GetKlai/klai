"""App-facing API for Knowledge Base Connectors."""

import logging
import re
from datetime import datetime
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import _get_caller_org, bearer
from app.core.database import get_db
from app.models.connectors import PortalConnector
from app.models.knowledge_bases import PortalKnowledgeBase
from app.services import knowledge_ingest_client
from app.services.access import get_user_role_for_kb
from app.services.connector_credentials import SENSITIVE_FIELDS, credential_store
from app.services.events import emit_event
from app.services.klai_connector_client import SyncRunData, klai_connector_client

logger = logging.getLogger(__name__)


async def _auto_fill_canary_fingerprint(config: dict) -> dict:
    """Auto-compute canary_fingerprint when canary_url is set but fingerprint is missing.

    SPEC-CRAWL-004 REQ-9: the portal backend calls klai-connector to compute
    the fingerprint. If computation fails, both canary fields are cleared so
    the connector saves without auth guard (non-blocking).

    Must be called BEFORE credential encryption (needs cookies in plaintext).
    """
    canary_url = config.get("canary_url")
    canary_fp = config.get("canary_fingerprint")

    if not canary_url or canary_fp:
        # No canary, or fingerprint already present (from preview auto-detect) → no-op
        return config

    # Need to compute: canary_url is set but fingerprint is missing.
    cookies = config.get("cookies")
    fingerprint = await klai_connector_client.compute_fingerprint(canary_url, cookies)

    if fingerprint:
        config = {**config, "canary_fingerprint": fingerprint}
        logger.info("canary_fingerprint auto-computed for %s", canary_url)
    else:
        # Crawl failed → clear canary to avoid half-configured state
        config = {k: v for k, v in config.items() if k not in ("canary_url", "canary_fingerprint")}
        logger.warning("canary_fingerprint computation failed for %s — canary disabled", canary_url)

    return config


router = APIRouter(
    prefix="/api/app/knowledge-bases/{kb_slug}/connectors",
    tags=["connectors"],
)

# -- Webcrawler config schema (SPEC-CRAWL-003) --------------------------------

_CANARY_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{16}$")


class CookieEntry(BaseModel):
    """A single browser cookie for webcrawler auth injection."""

    name: str
    value: str
    domain: str = ""
    path: str = "/"


class WebcrawlerConfig(BaseModel):
    """Validated configuration schema for web_crawler connectors.

    All new fields (SPEC-CRAWL-003) are optional with None defaults so existing
    connectors continue to work without modification.

    Validation rules enforced portal-side (SPEC-CRAWL-003 Data Model Diff):
    - XOR: canary_url ↔ canary_fingerprint (both or neither)
    - canary_fingerprint must match ^[0-9a-f]{16}$
    - canary_url must start with base_url + (path_prefix if set)
    - login_indicator_selector: non-empty, no angle brackets, no 'script'
    """

    # Existing fields (unchanged)
    base_url: str
    path_prefix: str | None = None
    max_pages: int = 200
    max_depth: int = 3
    content_selector: str | None = None
    cookies: list[CookieEntry] | None = None

    # SPEC-CRAWL-003 new optional fields — safe None defaults
    canary_url: str | None = None
    canary_fingerprint: str | None = None
    login_indicator_selector: str | None = None

    @model_validator(mode="after")
    def _validate_canary_and_selector(self) -> "WebcrawlerConfig":
        """Validate canary config, fingerprint format, URL prefix, and selector safety.

        canary_url without canary_fingerprint is ALLOWED on input — the portal
        backend auto-computes the fingerprint via klai-connector on save
        (SPEC-CRAWL-004 REQ-9). canary_fingerprint without canary_url is still
        rejected (orphaned fingerprint has no meaning).
        """
        url_set = self.canary_url is not None
        fp_set = self.canary_fingerprint is not None
        if fp_set and not url_set:
            raise ValueError("canary_fingerprint requires canary_url to be set")

        # Fingerprint format: ^[0-9a-f]{16}$
        if fp_set and self.canary_fingerprint is not None:
            if not _CANARY_FINGERPRINT_RE.match(self.canary_fingerprint):
                raise ValueError(f"canary_fingerprint must match ^[0-9a-f]{{16}}$, got: {self.canary_fingerprint!r}")

        # canary_url must be within base_url + path_prefix
        if url_set and self.canary_url is not None:
            prefix = self.base_url.rstrip("/")
            if self.path_prefix:
                prefix = prefix + "/" + self.path_prefix.strip("/")
            if not self.canary_url.startswith(prefix):
                raise ValueError(f"canary_url must start with {prefix!r}, got: {self.canary_url!r}")

        # login_indicator_selector: non-empty, no angle brackets, no javascript: URI.
        # SPEC intent is to block HTML/JS injection in a CSS selector field. Angle
        # brackets cover `<script>` injection; the javascript: check catches
        # `a[href^=javascript:...]` exploit attempts. Do NOT ban the substring
        # `script` alone — that would reject legitimate selectors like `.transcript`
        # or `[data-script-version]`.
        if self.login_indicator_selector is not None:
            sel = self.login_indicator_selector
            if not sel:
                raise ValueError("login_indicator_selector must not be empty")
            if "<" in sel or ">" in sel:
                raise ValueError("login_indicator_selector must not contain '<' or '>'")
            if "javascript:" in sel.lower():
                raise ValueError("login_indicator_selector must not contain 'javascript:' URIs")

        return self


ConnectorType = Literal["github", "notion", "web_crawler", "google_drive", "ms_docs"]

# Default content_type per connector_type (SPEC-EVIDENCE-001, R10)
CONTENT_TYPE_DEFAULTS: dict[str, str] = {
    "web_crawler": "web_crawl",
    "github": "kb_article",
    "notion": "kb_article",
    "google_drive": "pdf_document",
    "ms_docs": "kb_article",
}


# -- Pydantic schemas --------------------------------------------------------


VALID_ASSERTION_MODES = frozenset({"factual", "belief", "hypothesis", "procedural", "quoted", "unknown"})


class ConnectorCreateRequest(BaseModel):
    name: str
    connector_type: ConnectorType
    config: dict = Field(default_factory=dict)
    schedule: str | None = None
    content_type: str | None = None
    allowed_assertion_modes: list[str] | None = None


class ConnectorUpdateRequest(BaseModel):
    name: str | None = None
    config: dict | None = None
    schedule: str | None = None
    is_enabled: bool | None = None
    content_type: str | None = None
    allowed_assertion_modes: list[str] | None = None


class ConnectorOut(BaseModel):
    id: str
    kb_id: int
    name: str
    connector_type: str
    config: dict
    schedule: str | None
    is_enabled: bool
    last_sync_at: datetime | None
    last_sync_status: str | None
    last_sync_documents_ok: int | None
    created_at: datetime
    created_by: str
    content_type: str | None
    allowed_assertion_modes: list[str] | None


# -- Helpers ------------------------------------------------------------------


async def _get_kb_with_owner_check(
    kb_slug: str,
    caller_id: str,
    org_id: int,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Look up KB by slug + org_id and verify caller has owner role."""
    kb = await _get_kb_for_org(kb_slug, org_id, db)
    role = await get_user_role_for_kb(kb.id, caller_id, db, kb_created_by=kb.created_by)
    if role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner access required to manage connectors",
        )
    return kb


async def _get_kb_for_org(
    kb_slug: str,
    org_id: int,
    db: AsyncSession,
) -> PortalKnowledgeBase:
    """Look up KB by slug + org_id (read-only, no role check)."""
    result = await db.execute(
        select(PortalKnowledgeBase).where(
            PortalKnowledgeBase.slug == kb_slug,
            PortalKnowledgeBase.org_id == org_id,
        )
    )
    kb = result.scalar_one_or_none()
    if not kb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found",
        )
    return kb


def _connector_out(c: PortalConnector) -> ConnectorOut:
    # Mask sensitive fields so they never appear in public API responses
    masked_config = dict(c.config) if c.config else {}
    for field in SENSITIVE_FIELDS.get(c.connector_type, []):
        if field in masked_config:
            masked_config[field] = "***"
    return ConnectorOut(
        id=str(c.id),
        kb_id=c.kb_id,
        name=c.name,
        connector_type=c.connector_type,
        config=masked_config,
        schedule=c.schedule,
        is_enabled=c.is_enabled,
        last_sync_at=c.last_sync_at,
        last_sync_status=c.last_sync_status,
        last_sync_documents_ok=c.last_sync_documents_ok,
        created_at=c.created_at,
        created_by=c.created_by,
        content_type=c.content_type,
        allowed_assertion_modes=c.allowed_assertion_modes,
    )


# -- Endpoints ----------------------------------------------------------------


@router.get("/", response_model=list[ConnectorOut])
async def list_connectors(
    kb_slug: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[ConnectorOut]:
    """List connectors for a KB. Any org member with access to the KB can view."""
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_for_org(kb_slug, org.id, db)
    result = await db.execute(select(PortalConnector).where(PortalConnector.kb_id == kb.id))
    return [_connector_out(c) for c in result.scalars().all()]


@router.post("/", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def create_connector(
    kb_slug: str,
    body: ConnectorCreateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ConnectorOut:
    """Create a connector for a KB. Requires contributor access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    resolved_content_type = body.content_type or CONTENT_TYPE_DEFAULTS.get(body.connector_type, "unknown")
    if body.allowed_assertion_modes is not None:
        invalid = set(body.allowed_assertion_modes) - VALID_ASSERTION_MODES
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid assertion modes: {sorted(invalid)}. Valid: {sorted(VALID_ASSERTION_MODES)}",
            )
    # SPEC-CRAWL-004: auto-compute canary fingerprint before encryption
    # (needs plaintext cookies from config).
    config_for_save = await _auto_fill_canary_fingerprint(body.config)

    # Encrypt sensitive fields if credential store is configured
    config_to_store = config_for_save
    encrypted_blob = None
    if credential_store is not None:
        encrypted_blob, config_to_store = await credential_store.encrypt_credentials(
            org_id=org.id,
            connector_type=body.connector_type,
            config=config_for_save,
            db=db,
        )
    connector = PortalConnector(
        kb_id=kb.id,
        org_id=org.id,
        name=body.name,
        connector_type=body.connector_type,
        config=config_to_store,
        schedule=body.schedule,
        content_type=resolved_content_type,
        allowed_assertion_modes=body.allowed_assertion_modes,
        encrypted_credentials=encrypted_blob,
        created_by=caller_id,
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    return _connector_out(connector)


@router.patch("/{connector_id}", response_model=ConnectorOut)
async def update_connector(
    kb_slug: str,
    connector_id: str,
    body: ConnectorUpdateRequest,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> ConnectorOut:
    """Update a connector. Requires contributor access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    if body.name is not None:
        connector.name = body.name
    if body.config is not None:
        # SPEC-CRAWL-004: auto-compute canary fingerprint before encryption
        config_for_save = await _auto_fill_canary_fingerprint(body.config)
        if credential_store is not None:
            encrypted_blob, stripped_config = await credential_store.encrypt_credentials(
                org_id=org.id,
                connector_type=connector.connector_type,
                config=config_for_save,
                db=db,
            )
            connector.config = stripped_config
            if encrypted_blob is not None:
                connector.encrypted_credentials = encrypted_blob
        else:
            connector.config = config_for_save
    if body.schedule is not None:
        connector.schedule = body.schedule
    if body.is_enabled is not None:
        connector.is_enabled = body.is_enabled
    if body.content_type is not None:
        connector.content_type = body.content_type
    if body.allowed_assertion_modes is not None:
        invalid = set(body.allowed_assertion_modes) - VALID_ASSERTION_MODES
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid assertion modes: {sorted(invalid)}. Valid: {sorted(VALID_ASSERTION_MODES)}",
            )
        connector.allowed_assertion_modes = body.allowed_assertion_modes
    await db.commit()
    await db.refresh(connector)
    return _connector_out(connector)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    kb_slug: str,
    connector_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete a connector. Requires contributor access."""
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connector not found",
        )
    # Clean up all ingested data before removing the DB record.
    # Raises on failure — keeps portal and ingest consistent (no orphaned data).
    await knowledge_ingest_client.delete_connector(
        org_id=org.zitadel_org_id,
        kb_slug=kb.slug,
        connector_id=str(connector.id),
    )
    await db.delete(connector)
    await db.commit()


@router.post("/{connector_id}/sync", response_model=SyncRunData, status_code=status.HTTP_202_ACCEPTED)
async def trigger_sync(
    kb_slug: str,
    connector_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> SyncRunData:
    """Trigger an on-demand sync for a connector. Requires owner access.

    Delegates to klai-connector execution service. Returns 202 with the new
    SyncRun immediately; sync runs in the background.
    """
    caller_id, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_with_owner_check(kb_slug, caller_id, org.id, db)
    result = await db.execute(
        select(PortalConnector).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")
    if not connector.is_enabled:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Connector is disabled")
    if connector.last_sync_status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync already running")

    try:
        sync_run = await klai_connector_client.trigger_sync(connector_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == status.HTTP_409_CONFLICT:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync already running") from exc
        logger.exception("klai-connector returned error for connector %s", connector_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Sync service error") from exc
    except httpx.HTTPError as exc:
        logger.exception("Failed to reach klai-connector for connector %s", connector_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Sync service unavailable") from exc

    # Optimistically mark as running so the UI reflects it immediately.
    connector.last_sync_status = "running"
    await db.commit()
    emit_event(
        "knowledge.uploaded",
        org_id=org.id,
        user_id=caller_id,
        properties={"scope": "org", "file_type": connector.connector_type},
    )
    return sync_run


@router.get("/{connector_id}/syncs", response_model=list[SyncRunData])
async def list_sync_runs(
    kb_slug: str,
    connector_id: str,
    limit: int = 20,
    credentials: HTTPAuthorizationCredentials = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> list[SyncRunData]:
    """List sync history for a connector (most recent first).

    Proxies to klai-connector execution service.
    """
    _, org, _ = await _get_caller_org(credentials, db)
    kb = await _get_kb_for_org(kb_slug, org.id, db)
    exists = await db.execute(
        select(PortalConnector.id).where(
            PortalConnector.id == connector_id,
            PortalConnector.kb_id == kb.id,
        )
    )
    if not exists.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Connector not found")

    try:
        return await klai_connector_client.get_sync_runs(connector_id, limit=limit)
    except httpx.HTTPError as exc:
        logger.exception("Failed to reach klai-connector for sync history of %s", connector_id)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Sync service unavailable") from exc
