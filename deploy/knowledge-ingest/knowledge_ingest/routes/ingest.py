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
import unicodedata

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Request

from knowledge_ingest import chunker, embedder, pg_store, qdrant_store
from knowledge_ingest.config import settings
from knowledge_ingest.models import GiteaPushEvent, IngestRequest

logger = logging.getLogger(__name__)
router = APIRouter()


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

    await qdrant_store.upsert_chunks(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        path=req.path,
        chunks=texts,
        vectors=vectors,
        extra_payload={"title": title},
    )
    await pg_store.record_ingest(req.org_id, req.kb_slug, req.path, len(chunks))

    logger.info("Ingested %s/%s for org %s (%d chunks)", req.kb_slug, req.path, req.org_id, len(chunks))
    return {"status": "ok", "chunks": len(chunks), "title": title}


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
        req = IngestRequest(org_id=org_id, kb_slug=kb_slug, path=path, content=content)
        try:
            await ingest_document(req)
            ingested += 1
        except Exception as exc:
            logger.warning("Failed to ingest %s: %s", path, exc)

    # Delete removed files
    for path in removed:
        try:
            await qdrant_store.delete_document(org_id, kb_slug, path)
            deleted += 1
        except Exception as exc:
            logger.warning("Failed to delete %s from Qdrant: %s", path, exc)

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
