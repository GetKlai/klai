"""
Ingest routes:
  POST /ingest/v1/document     — direct document ingest
  POST /ingest/v1/webhook/gitea — Gitea push webhook
"""
import hashlib
import hmac
import json
import logging
import re
import time
import unicodedata
from datetime import datetime, timezone

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request

from knowledge_ingest import chunker, embedder, pg_store, qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.models import (
    GiteaPushEvent,
    IngestRequest,
    UpdateKBVisibilityRequest,
)

_SENTINEL = 253402300800  # 9999-12-31

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Secret header for service-to-service calls."""
    if not settings.knowledge_ingest_secret:
        return
    secret = request.headers.get("x-internal-secret", "")
    if not hmac.compare_digest(secret, settings.knowledge_ingest_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _extract_title(content: str, path: str) -> str:
    """Extract title from frontmatter or first H1, falling back to path."""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            try:
                fm = yaml.safe_load(content[3:end])
                if isinstance(fm, dict) and fm.get("title"):
                    return str(fm["title"])
            except Exception:
                pass
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return path.rsplit("/", 1)[-1].replace(".md", "")


def _extract_frontmatter_metadata(content: str) -> dict:
    """Extract indexable fields from YAML frontmatter for Qdrant payload."""
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    try:
        fm = yaml.safe_load(content[3:end])
        if not isinstance(fm, dict):
            return {}
        result = {}
        for key in ("tags", "provenance_type", "confidence", "source_note"):
            if fm.get(key) is not None:
                result[key] = fm[key]
        return result
    except Exception:
        return {}


def _parse_knowledge_fields(content: str, source_type: str | None) -> dict:
    """Extract knowledge model fields from YAML frontmatter. Returns defaults if absent."""
    defaults: dict = {
        "provenance_type": "observed",
        "assertion_mode": "factual",
        "synthesis_depth": 4 if source_type == "docs" else 0,
        "confidence": None,
        "belief_time_start": int(time.time()),
        "belief_time_end": _SENTINEL,
    }
    if not content.startswith("---"):
        return defaults
    end = content.find("\n---", 3)
    if end == -1:
        return defaults
    try:
        fm = yaml.safe_load(content[3:end])
        if not isinstance(fm, dict):
            return defaults
    except Exception:
        return defaults

    result = dict(defaults)
    if fm.get("provenance_type") in ("observed", "extracted", "synthesized", "revised"):
        result["provenance_type"] = fm["provenance_type"]
    if fm.get("assertion_mode") in ("factual", "procedural", "quoted", "belief", "hypothesis"):
        result["assertion_mode"] = fm["assertion_mode"]
    if isinstance(fm.get("synthesis_depth"), int) and 0 <= fm["synthesis_depth"] <= 4:
        result["synthesis_depth"] = fm["synthesis_depth"]
    if fm.get("confidence") in ("high", "medium", "low"):
        result["confidence"] = fm["confidence"]
    if isinstance(fm.get("belief_time_start"), str):
        try:
            result["belief_time_start"] = int(
                datetime.fromisoformat(fm["belief_time_start"])
                .replace(tzinfo=timezone.utc)
                .timestamp()
            )
        except Exception:
            pass
    return result


async def ingest_document(req: IngestRequest) -> dict:
    """Core ingest pipeline: chunk → embed → upsert."""
    chunks = chunker.chunk_markdown(
        req.content,
        chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    if not chunks:
        return {"status": "skipped", "reason": "empty document", "chunks": 0}

    texts = [c.text for c in chunks]
    vectors = await embedder.embed(texts)

    title = _extract_title(req.content, req.path)
    kf = _parse_knowledge_fields(req.content, req.source_type)

    # Soft-delete previous artifact for this path (AC-5: re-ingest creates new row)
    await pg_store.soft_delete_artifact(req.org_id, req.kb_slug, req.path)

    artifact_id = await pg_store.create_artifact(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        path=req.path,
        provenance_type=kf["provenance_type"],
        assertion_mode=kf["assertion_mode"],
        synthesis_depth=kf["synthesis_depth"],
        confidence=kf["confidence"],
        belief_time_start=kf["belief_time_start"],
        belief_time_end=kf["belief_time_end"],
        user_id=req.user_id,
    )

    extra_payload: dict = {"title": title, "artifact_id": artifact_id}
    if req.source_type:
        extra_payload["source_type"] = req.source_type
    extra_payload.update(_extract_frontmatter_metadata(req.content))

    await qdrant_store.upsert_chunks(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        path=req.path,
        chunks=texts,
        vectors=vectors,
        artifact_id=artifact_id,
        extra_payload=extra_payload,
        user_id=req.user_id,
    )

    logger.info("Ingested %s/%s for org %s (%d chunks, artifact %s)", req.kb_slug, req.path, req.org_id, len(chunks), artifact_id)
    return {"status": "ok", "chunks": len(chunks), "title": title, "artifact_id": artifact_id}


@router.post("/ingest/v1/document")
async def ingest_document_route(req: IngestRequest) -> dict:
    return await ingest_document(req)


@router.post("/ingest/v1/webhook/gitea")
async def gitea_webhook(request: Request) -> dict:
    """
    Handles Gitea push webhooks.
    Repo naming convention: org-{org_slug}/{kb_slug}
    Ingest uses org_id from Gitea org config (fetched via Gitea API).
    """
    # Must read raw bytes before json.loads; body stream is consumed after first read
    raw_body = await request.body()

    if settings.gitea_webhook_secret:
        signature = request.headers.get("x-gitea-signature", "")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing X-Gitea-Signature header")
        expected = hmac.new(
            settings.gitea_webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        body = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = GiteaPushEvent.model_validate(body)

    # Parse org_slug and kb_slug from repo full_name ("org-{slug}/{kb}")
    full_name = event.repository.full_name
    parts = full_name.split("/")
    if len(parts) != 2 or not parts[0].startswith("org-"):
        logger.warning("Ignoring webhook for repo %s (unexpected naming)", full_name)
        return {"status": "ignored", "reason": "unexpected repo format"}

    gitea_org_name = parts[0]   # e.g. "org-myslug"
    kb_slug = parts[1]           # e.g. "personal"
    org_slug = gitea_org_name[4:]  # strip "org-"

    # Fetch org_id (Zitadel org ID) from Gitea org metadata
    org_id = await _get_org_id(gitea_org_name)
    if not org_id:
        logger.warning("Could not resolve org_id for %s — skipping webhook", gitea_org_name)
        return {"status": "ignored", "reason": "org_id not found"}

    # Collect changed .md files
    changed: set[str] = set()
    removed: set[str] = set()
    for commit in event.commits:
        for path in commit.added + commit.modified:
            if path.endswith(".md") and not path.startswith("_"):
                changed.add(path)
        for path in commit.removed:
            if path.endswith(".md"):
                removed.add(path)

    ingested = 0
    deleted = 0

    # Ingest changed files
    for path in changed:
        content = await _fetch_gitea_file(full_name, path)
        if content is None:
            logger.warning("Could not fetch %s from %s", path, full_name)
            continue
        req = IngestRequest(
            org_id=org_id, kb_slug=kb_slug, path=path,
            content=content, source_type="docs",
        )
        try:
            await ingest_document(req)
            ingested += 1
        except Exception as exc:
            logger.warning("Failed to ingest %s: %s", path, exc)

    # Delete removed files
    for path in removed:
        try:
            await qdrant_store.delete_document(org_id, kb_slug, path)
            await pg_store.soft_delete_artifact(org_id, kb_slug, path)
            deleted += 1
        except Exception as exc:
            logger.warning("Failed to delete %s: %s", path, exc)

    return {"status": "ok", "ingested": ingested, "deleted": deleted, "org_slug": org_slug}


async def _get_org_id(gitea_org_name: str) -> str | None:
    """
    Fetch the Zitadel org_id from Gitea org description field.
    Convention: Gitea org description = Zitadel org ID.
    """
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitea_url,
            headers={"Authorization": f"token {settings.gitea_token}"},
            timeout=5.0,
        ) as client:
            resp = await client.get(f"/api/v1/orgs/{gitea_org_name}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data.get("description") or None
    except Exception as exc:
        logger.warning("Gitea API error for %s: %s", gitea_org_name, exc)
        return None


@router.delete("/ingest/v1/kb")
async def delete_kb_route(request: Request, org_id: str, kb_slug: str) -> dict:
    """Delete all Qdrant chunks for a knowledge base. Called by Docs on KB deletion."""
    _verify_internal_secret(request)
    await qdrant_store.delete_kb(org_id, kb_slug)
    logger.info("Deleted KB %s/%s from Qdrant (via API)", org_id, kb_slug)
    return {"status": "ok"}


@router.patch("/ingest/v1/kb/visibility")
async def update_kb_visibility_route(request: Request, req: UpdateKBVisibilityRequest) -> dict:
    """Update visibility for all chunks in a knowledge base. Called by Docs on visibility change."""
    _verify_internal_secret(request)
    await qdrant_store.update_kb_visibility(req.org_id, req.kb_slug, req.visibility)
    return {"status": "ok"}


async def _fetch_gitea_file(repo_full_name: str, path: str) -> str | None:
    """Fetch raw file content from Gitea."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitea_url,
            headers={"Authorization": f"token {settings.gitea_token}"},
            timeout=10.0,
        ) as client:
            resp = await client.get(f"/api/v1/repos/{repo_full_name}/raw/{path}")
            if resp.status_code != 200:
                return None
            return resp.text
    except Exception as exc:
        logger.warning("Failed to fetch %s from %s: %s", path, repo_full_name, exc)
        return None
