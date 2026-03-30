"""
Source endpoints:
  POST   /v1/notebooks/{nb_id}/sources    — file upload or URL/YouTube
  GET    /v1/notebooks/{nb_id}/sources
  DELETE /v1/notebooks/{nb_id}/sources/{src_id}
"""
import asyncio
import ipaddress
import logging
import socket
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.notebook import Notebook
from app.models.source import Source
from app.services.ingestion import ingest_source

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["sources"])

_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md"}
_UPLOAD_BASE = Path("/opt/klai/research-uploads")


# ── Response models ──────────────────────────────────────────────────────────

class SourceResponse(BaseModel):
    id: str
    name: str
    type: str
    status: str
    chunks_count: int | None
    error_message: str | None
    created_at: datetime

    class Config:
        from_attributes = True


class SourceListResponse(BaseModel):
    items: list[SourceResponse]


class UrlSourceCreate(BaseModel):
    type: str  # "url" | "youtube"
    url: str
    name: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _src_id() -> str:
    return "src_" + uuid.uuid4().hex[:24]


async def _get_notebook_for_source(
    nb_id: str,
    db: AsyncSession,
    user: CurrentUser,
) -> Notebook:
    result = await db.execute(select(Notebook).where(Notebook.id == nb_id))
    nb: Notebook | None = result.scalar_one_or_none()
    if nb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")

    if nb.scope == "personal" and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")
    if nb.scope == "org" and str(nb.tenant_id) != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notebook niet gevonden")

    return nb


def _detect_source_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    mapping = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx", ".pptx": "pptx", ".txt": "text", ".md": "text"}
    return mapping.get(ext, "pdf")


_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "169.254.169.254"}


async def _assert_url_safe(url: str) -> None:
    """Reject URLs that target private/internal resources (SSRF guard)."""
    try:
        parsed = urlparse(url)
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Ongeldige URL")

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Alleen http/https URLs zijn toegestaan",
        )

    hostname = parsed.hostname or ""
    if not hostname:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Ongeldige URL: geen hostname")

    if hostname.lower() in _BLOCKED_HOSTNAMES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="URL niet toegestaan")

    loop = asyncio.get_event_loop()
    try:
        addrs = await loop.run_in_executor(None, socket.getaddrinfo, hostname, None)
    except socket.gaierror:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="URL niet bereikbaar: hostname kan niet worden omgezet",
        )

    for addr_info in addrs:
        ip_str = addr_info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="URL verwijst naar intern netwerk",
            )


# ── POST /v1/notebooks/{nb_id}/sources — file upload ─────────────────────────

@router.post(
    "/notebooks/{nb_id}/sources",
    response_model=SourceResponse,
    status_code=202,
)
async def add_source_file(
    nb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(None),
    name: str | None = Form(None),
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    nb = await _get_notebook_for_source(nb_id, db, user)

    if nb.scope == "org" and not user.can_upload() and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen uploadrechten")

    if file is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Bestand of URL vereist",
        )

    filename = file.filename or "upload"
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bestandstype niet ondersteund: {ext}",
        )

    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Bestand te groot (max {settings.max_upload_mb} MB)",
        )

    # Save file to disk
    upload_dir = _UPLOAD_BASE / user.tenant_id / nb_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    src_id = _src_id()
    file_path = upload_dir / f"{src_id}{ext}"
    file_path.write_bytes(raw)

    source = Source(
        id=src_id,
        notebook_id=nb_id,
        tenant_id=nb.tenant_id,
        type=_detect_source_type(filename),
        name=name or filename,
        original_ref=filename,
        file_path=str(file_path),
        status="pending",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    background_tasks.add_task(ingest_source, src_id)

    return SourceResponse(
        id=source.id,
        name=source.name,
        type=source.type,
        status=source.status,
        chunks_count=source.chunks_count,
        error_message=source.error_message,
        created_at=source.created_at,
    )


# ── POST /v1/notebooks/{nb_id}/sources — URL / YouTube ───────────────────────

@router.post(
    "/notebooks/{nb_id}/sources/url",
    response_model=SourceResponse,
    status_code=202,
)
async def add_source_url(
    nb_id: str,
    body: UrlSourceCreate,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    nb = await _get_notebook_for_source(nb_id, db, user)

    if nb.scope == "org" and not user.can_upload() and nb.owner_user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Geen uploadrechten")

    if body.type not in ("url", "youtube"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="type moet 'url' of 'youtube' zijn",
        )

    # SSRF guard: only for URL type (YouTube IDs are not user-controlled URLs)
    if body.type == "url":
        await _assert_url_safe(body.url)

    src_id = _src_id()
    display_name = body.name or body.url[:80]

    source = Source(
        id=src_id,
        notebook_id=nb_id,
        tenant_id=nb.tenant_id,
        type=body.type,
        name=display_name,
        original_ref=body.url,
        file_path=None,
        status="pending",
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    background_tasks.add_task(ingest_source, src_id)

    return SourceResponse(
        id=source.id,
        name=source.name,
        type=source.type,
        status=source.status,
        chunks_count=source.chunks_count,
        error_message=source.error_message,
        created_at=source.created_at,
    )


# ── GET /v1/notebooks/{nb_id}/sources ────────────────────────────────────────

@router.get("/notebooks/{nb_id}/sources", response_model=SourceListResponse)
async def list_sources(
    nb_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SourceListResponse:
    await _get_notebook_for_source(nb_id, db, user)

    rows = await db.execute(
        select(Source).where(Source.notebook_id == nb_id).order_by(Source.created_at.desc())
    )
    sources = rows.scalars().all()

    return SourceListResponse(
        items=[
            SourceResponse(
                id=s.id,
                name=s.name,
                type=s.type,
                status=s.status,
                chunks_count=s.chunks_count,
                error_message=s.error_message,
                created_at=s.created_at,
            )
            for s in sources
        ]
    )


# ── DELETE /v1/notebooks/{nb_id}/sources/{src_id} ────────────────────────────

@router.delete("/notebooks/{nb_id}/sources/{src_id}", status_code=204)
async def delete_source(
    nb_id: str,
    src_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_notebook_for_source(nb_id, db, user)

    result = await db.execute(
        select(Source).where(Source.id == src_id, Source.notebook_id == nb_id)
    )
    source: Source | None = result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bron niet gevonden")

    # Delete chunks from DB and Qdrant
    from app.models.chunk import Chunk
    from app.services import qdrant_store

    await db.execute(delete(Chunk).where(Chunk.source_id == src_id))
    qdrant_store.delete_by_source(src_id)

    # Delete file if present
    if source.file_path:
        try:
            Path(source.file_path).unlink(missing_ok=True)
        except Exception:
            logger.warning("Could not delete file: %s", source.file_path)

    await db.execute(delete(Source).where(Source.id == src_id))
    await db.commit()
