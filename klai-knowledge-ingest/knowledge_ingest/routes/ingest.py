"""
Ingest routes:
  POST /ingest/v1/document        — direct document ingest
  POST /ingest/v1/webhook/gitea   — Gitea push webhook
  POST /ingest/v1/kb/webhook      — register Gitea webhook for a KB
  DELETE /ingest/v1/kb/webhook    — de-register Gitea webhook for a KB
  POST /ingest/v1/kb/sync         — bulk re-index all pages of a KB
"""
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import httpx
import structlog
import yaml
from fastapi import APIRouter, HTTPException, Request

from knowledge_ingest import (
    chunker,
    embedder,
    kb_config,
    org_config,
    pg_store,
    qdrant_store,
)
from knowledge_ingest import (
    graph as graph_module,
)
from knowledge_ingest.clustering import classify_by_centroid, load_centroids
from knowledge_ingest.config import settings
from knowledge_ingest.content_labeler import generate_content_label
from knowledge_ingest.content_profiles import get_profile
from knowledge_ingest.db import get_pool
from knowledge_ingest.models import (
    BulkSyncRequest,
    GiteaPushEvent,
    IngestRequest,
    KBWebhookRequest,
    UpdateKBVisibilityRequest,
)
from knowledge_ingest.portal_client import fetch_taxonomy_nodes
from knowledge_ingest.proposal_generator import DocumentSummary, maybe_generate_proposal
from knowledge_ingest.taxonomy_classifier import classify_document

_SENTINEL = 253402300800  # 9999-12-31
_background_tasks: set = set()  # Prevents fire-and-forget tasks from being GC'd

logger = structlog.get_logger()
router = APIRouter()


def _verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Secret header for service-to-service calls.

    SPEC-SEC-011: no fail-open branch. The config validator guarantees the
    secret is non-empty at import time, so missing or mismatched headers
    always raise 401 (constant-time comparison via :func:`hmac.compare_digest`).
    """
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
                logger.debug("frontmatter_yaml_parse_error")
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


_ASSERTION_MODE_MIGRATION: dict[str, str] = {
    "fact": "factual",
    "claim": "belief",
    "speculation": "hypothesis",
    "note": "unknown",
}

_VALID_ASSERTION_MODES = frozenset(
    {"factual", "belief", "hypothesis", "procedural", "quoted", "unknown"}
)


def _parse_knowledge_fields(
    content: str,
    source_type: str | None,
    allowed_assertion_modes: list[str] | None = None,
) -> dict:
    """Extract knowledge model fields from YAML frontmatter. Returns defaults if absent.

    If ``allowed_assertion_modes`` is provided (connector-level hint) and the content
    has no frontmatter assertion_mode, the hint is applied:
    - Exactly one valid mode in the list → use it as default.
    - Multiple modes → keep "unknown" (too ambiguous to auto-assign).
    - Invalid values in the list are silently ignored.
    """
    defaults: dict = {
        "provenance_type": "observed",
        "assertion_mode": "unknown",
        "synthesis_depth": 4 if source_type == "docs" else 0,
        "confidence": None,
        "belief_time_start": int(time.time()),
        "belief_time_end": _SENTINEL,
    }

    # Apply connector-level hint before frontmatter: hint sets the default,
    # frontmatter can always override it.
    if allowed_assertion_modes:
        valid_hints = [m for m in allowed_assertion_modes if m in _VALID_ASSERTION_MODES]
        if len(valid_hints) == 1:
            defaults["assertion_mode"] = valid_hints[0]

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

    raw_mode = fm.get("assertion_mode")
    if raw_mode in _VALID_ASSERTION_MODES:
        result["assertion_mode"] = raw_mode
    elif raw_mode in _ASSERTION_MODE_MIGRATION:
        result["assertion_mode"] = _ASSERTION_MODE_MIGRATION[raw_mode]
    # else: keep default (either "unknown" or the connector hint)

    if isinstance(fm.get("synthesis_depth"), int) and 0 <= fm["synthesis_depth"] <= 4:
        result["synthesis_depth"] = fm["synthesis_depth"]
    if fm.get("confidence") in ("high", "medium", "low"):
        result["confidence"] = fm["confidence"]
    if isinstance(fm.get("belief_time_start"), str):
        try:
            result["belief_time_start"] = int(
                datetime.fromisoformat(fm["belief_time_start"])
                .replace(tzinfo=UTC)
                .timestamp()
            )
        except Exception:
            logger.debug("belief_time_parse_error", value=fm.get("belief_time_start"))
    return result


# SPEC-KB-021: imported here so callers in this module use the short name
from knowledge_ingest.source_label import compute_source_label as _compute_source_label  # noqa: E402


async def _graphiti_background(
    artifact_id: str,
    document_text: str,
    org_id: str,
    content_type: str,
    belief_time_start: int,
) -> None:
    """Background task: ingest document into Graphiti, then store episode_id (AC-1, AC-2)."""
    episode_id = await graph_module.ingest_episode(
        artifact_id=artifact_id,
        document_text=document_text,
        org_id=org_id,
        content_type=content_type,
        belief_time_start=belief_time_start,
    )
    if episode_id:
        await pg_store.update_artifact_extra(artifact_id, {"graphiti_episode_id": episode_id})


async def ingest_document(req: IngestRequest) -> dict:
    """Core ingest pipeline: chunk -> embed -> upsert."""
    t_ingest = time.monotonic()

    # Early exit if content is unchanged since last ingest
    content_hash = hashlib.sha256(req.content.encode()).hexdigest()
    stored_hash = await pg_store.get_active_content_hash(req.org_id, req.kb_slug, req.path)
    if stored_hash is not None and stored_hash == content_hash:
        logger.info(
            "ingest_skipped",
            reason="content_unchanged",
            kb_slug=req.kb_slug,
            path=req.path,
            org_id=req.org_id,
        )
        return {"status": "skipped", "reason": "content unchanged", "chunks": 0}

    # Determine chunks: skip_chunking uses pre-provided chunks or content as single chunk
    if req.skip_chunking:
        if req.chunks:
            texts = req.chunks
        else:
            texts = [req.content]
    else:
        # Use content profile chunk_tokens_max (converted to chars) when it
        # differs from the global default.  1 token ≈ 4 chars.
        profile = get_profile(req.content_type)
        profile_chunk_chars = profile.chunk_tokens_max * 4
        chunk_size = (
            profile_chunk_chars
            if profile_chunk_chars != settings.chunk_size
            else settings.chunk_size
        )
        chunks = chunker.chunk_markdown(
            req.content,
            chunk_size=chunk_size,
            overlap=settings.chunk_overlap,
        )
        if not chunks:
            return {"status": "skipped", "reason": "empty document", "chunks": 0}
        texts = [c.text for c in chunks]

    if not texts:
        return {"status": "skipped", "reason": "empty document", "chunks": 0}

    vectors = await embedder.embed(texts)

    title = _extract_title(req.content, req.path)
    kf = _parse_knowledge_fields(req.content, req.source_type, req.allowed_assertion_modes)

    # Apply synthesis_depth override from adapter if provided
    if req.synthesis_depth is not None:
        kf["synthesis_depth"] = req.synthesis_depth

    # Blind label generation (SPEC-KB-023 R1) — BEFORE taxonomy to avoid confirmation bias.
    # Uses klai-fast, 15s timeout, returns [] on failure (non-fatal).
    content_label = await generate_content_label(
        title=title,
        content_preview=req.content,
    )

    # Taxonomy classification (SPEC-KB-022 R1) — multi-label, one call per document.
    # Fetch taxonomy nodes for this KB; if none exist, skip classification entirely.
    taxonomy_nodes = await fetch_taxonomy_nodes(req.kb_slug, req.org_id)
    has_taxonomy = len(taxonomy_nodes) > 0
    taxonomy_node_ids: list[int] = []
    llm_tags: list[str] = []
    if has_taxonomy:
        # R2: try centroid-based classification first (SPEC-KB-024)
        centroid_matched = False
        try:
            centroids = load_centroids(req.org_id, req.kb_slug)
            if centroids:
                from knowledge_ingest import embedder as _embedder

                doc_vectors = await _embedder.embed([req.content[:512]])
                doc_vec = doc_vectors[0] if doc_vectors else None
                if doc_vec is not None:
                    centroid_result = classify_by_centroid(
                        embedding=doc_vec,
                        centroids=centroids,
                        threshold=settings.taxonomy_centroid_match_threshold,
                        taxonomy_node_ids={n.id for n in taxonomy_nodes},
                    )
                    if centroid_result is not None:
                        taxonomy_node_ids = centroid_result
                        centroid_matched = True
        except Exception:
            logger.debug("centroid_lookup_failed", exc_info=True)

        if not centroid_matched:
            matched_nodes, llm_tags = await classify_document(
                title=title,
                content_preview=req.content,
                taxonomy_nodes=taxonomy_nodes,
            )
            taxonomy_node_ids = [node_id for node_id, _conf in matched_nodes]

    # Merge frontmatter tags + LLM-suggested tags (frontmatter has priority, dedup)
    frontmatter_meta = _extract_frontmatter_metadata(req.content)
    frontmatter_tags: list[str] = frontmatter_meta.get("tags", [])
    if isinstance(frontmatter_tags, list):
        seen: set[str] = set()
        merged_tags: list[str] = []
        for tag in frontmatter_tags:
            t = str(tag).strip().lower()
            if t and t not in seen:
                merged_tags.append(t)
                seen.add(t)
        for tag in llm_tags:
            if tag not in seen:
                merged_tags.append(tag)
                seen.add(tag)
    else:
        merged_tags = llm_tags

    # Soft-delete previous artifact for this path (AC-5: re-ingest creates new row)
    await pg_store.soft_delete_artifact(req.org_id, req.kb_slug, req.path)

    # Merge connector provenance fields into extra so PG tracks the same metadata as Qdrant.
    # This enables delete_connector_artifacts() to find and remove PG records by connector.
    pg_extra: dict = dict(req.extra or {})
    if req.source_connector_id:
        pg_extra["source_connector_id"] = req.source_connector_id
    if req.source_type:
        pg_extra["source_type"] = req.source_type
    if req.source_ref:
        pg_extra["source_ref"] = req.source_ref

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
        content_type=req.content_type,
        extra=pg_extra or None,
        content_hash=content_hash,
    )

    pool = await get_pool()
    visibility = await kb_config.get_kb_visibility(req.org_id, req.kb_slug, pool)

    extra_payload: dict = {"title": title, "artifact_id": artifact_id}
    if req.source_type:
        extra_payload["source_type"] = req.source_type
    if req.source_connector_id:
        extra_payload["source_connector_id"] = req.source_connector_id
    if req.source_ref:
        extra_payload["source_ref"] = req.source_ref
    if req.content_type != "unknown":
        extra_payload["content_type"] = req.content_type
    # Evidence tier metadata (SPEC-EVIDENCE-001, R4)
    extra_payload["assertion_mode"] = kf["assertion_mode"]
    # Merge adapter extra metadata
    if req.extra:
        extra_payload.update(req.extra)
    # Merge temporal fields for Qdrant payload
    extra_payload["belief_time_start"] = kf["belief_time_start"]
    extra_payload["belief_time_end"] = kf["belief_time_end"]
    # Reuse frontmatter_meta extracted earlier for tag merging; strip tags to avoid
    # overwriting the merged_tags parameter passed separately to upsert_chunks.
    fm_meta_for_payload = {k: v for k, v in frontmatter_meta.items() if k != "tags"}
    extra_payload.update(fm_meta_for_payload)
    # Taxonomy data into extra_payload for enrichment pipeline passthrough
    if has_taxonomy:
        extra_payload["taxonomy_node_ids"] = taxonomy_node_ids
    if merged_tags:
        extra_payload["tags"] = merged_tags
    # content_label into extra_payload so enrichment pipeline preserves it (SPEC-KB-023)
    extra_payload["content_label"] = content_label
    # source_label + source enrichment fields for enrichment pipeline (SPEC-KB-021)
    extra_payload["source_label"] = _compute_source_label(req)
    if req.kb_name:
        extra_payload["kb_name"] = req.kb_name
    if req.connector_type:
        extra_payload["connector_type"] = req.connector_type
    if req.source_domain:
        extra_payload["source_domain"] = req.source_domain
    # Visibility is authoritative from kb_config — set last so req.extra cannot override it
    extra_payload["visibility"] = visibility

    await qdrant_store.upsert_chunks(
        org_id=req.org_id,
        kb_slug=req.kb_slug,
        path=req.path,
        chunks=texts,
        vectors=vectors,
        artifact_id=artifact_id,
        extra_payload=extra_payload,
        user_id=req.user_id,
        taxonomy_node_ids=taxonomy_node_ids if has_taxonomy else None,
        tags=merged_tags if merged_tags else None,
        has_taxonomy=has_taxonomy,
    )

    # Taxonomy proposal generation (SPEC-KB-022 R4) — fire-and-forget, non-blocking.
    # Self-bootstrapping: fires when taxonomy_node_ids is empty regardless of whether the KB
    # already has nodes. This covers both:
    #   - KB with 0 nodes: all documents are unmatched -> proposals generated from scratch
    #   - KB with nodes: only truly unmatched documents (confidence < 0.5) trigger proposals
    # The >= 3 threshold in maybe_generate_proposal prevents noise from single documents.
    if has_taxonomy and not taxonomy_node_ids:
        import asyncio as _asyncio
        _t = _asyncio.create_task(
            maybe_generate_proposal(
                org_id=req.org_id,
                kb_slug=req.kb_slug,
                unmatched_documents=[
                    DocumentSummary(title=title, content_preview=req.content[:500])
                ],
                existing_nodes=taxonomy_nodes,
            )
        )
        _background_tasks.add(_t)
        _t.add_done_callback(_background_tasks.discard)

    # Enqueue enrichment as async Procrastinate task (non-blocking)
    if await org_config.is_enrichment_enabled(req.org_id, pool):
        from knowledge_ingest import enrichment_tasks
        proc_app = enrichment_tasks.get_app()
        task_fn = (
            proc_app.enrich_document_interactive  # type: ignore[attr-defined]
            if req.source_type == "upload"
            else proc_app.enrich_document_bulk  # type: ignore[attr-defined]
        )
        try:
            from procrastinate.exceptions import AlreadyEnqueued
            await task_fn.configure(
                queueing_lock=f"{req.org_id}:{req.kb_slug}:{req.path}",
            ).defer_async(
                org_id=req.org_id,
                kb_slug=req.kb_slug,
                path=req.path,
                document_text=req.content,
                chunks=texts,
                title=title,
                artifact_id=artifact_id,
                user_id=req.user_id,
                extra_payload=extra_payload,
                synthesis_depth=kf["synthesis_depth"],
                content_type=req.content_type,
            )
        except AlreadyEnqueued:
            logger.info(
                "enrichment_already_queued",
                kb_slug=req.kb_slug,
                path=req.path,
                org_id=req.org_id,
            )

    # Graphiti episode ingest — queued via Procrastinate on graphiti-bulk (lowest priority).
    # The worker drains: ingest-kb → enrich-interactive → enrich-bulk → graphiti-bulk.
    # This ensures enrichment LLM calls finish before Graphiti starts, so they never
    # compete on the same 1 req/s upstream rate limit simultaneously.
    if settings.graphiti_enabled:
        from knowledge_ingest import enrichment_tasks
        proc_app = enrichment_tasks.get_app()
        await proc_app.ingest_graphiti_episode.configure(  # type: ignore[attr-defined]
            queueing_lock=f"graphiti:{artifact_id}",
        ).defer_async(
            artifact_id=artifact_id,
            document_text=req.content,
            org_id=req.org_id,
            content_type=req.content_type,
            belief_time_start=kf["belief_time_start"],
        )

    ingest_ms = int((time.monotonic() - t_ingest) * 1000)
    logger.info(
        "ingest_complete",
        kb_slug=req.kb_slug,
        path=req.path,
        org_id=req.org_id,
        artifact_id=artifact_id,
        chunks=len(texts),
        type=req.content_type,
        ingest_ms=ingest_ms,
    )
    return {"status": "ok", "chunks": len(texts), "title": title, "artifact_id": artifact_id}


# @MX:NOTE: caller-asserted identity contract
#
# /ingest accepts org_id + user_id from request body. This is BY-DESIGN
# safe ONLY because all current callers verify identity upstream:
# - portal-api: verifies session before forwarding
# - knowledge-mcp: calls portal-api /internal/identity/verify
# - scribe-api: calls portal-api /internal/identity/verify (post-B1 fix)
#
# Adding a NEW caller? You MUST verify identity upstream before
# forwarding — OR refactor this route to call /internal/identity/verify
# itself. Do NOT trust the body fields by default.
#
# Reference: SPEC-SEC-AUDIT-2026-04 finding C3.
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
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc

    event = GiteaPushEvent.model_validate(body)

    # AC-11: Only process pushes to the default branch
    if event.ref != "refs/heads/main":
        return {"status": "ignored", "reason": "not main branch"}

    # Parse org_slug and kb_slug from repo full_name ("org-{slug}/{kb}")
    full_name = event.repository.full_name
    parts = full_name.split("/")
    if len(parts) != 2 or not parts[0].startswith("org-"):
        logger.warning("webhook_ignored", reason="unexpected_repo_format", repo=full_name)
        return {"status": "ignored", "reason": "unexpected repo format"}

    gitea_org_name = parts[0]   # e.g. "org-myslug"
    kb_slug = parts[1]           # e.g. "personal"
    org_slug = gitea_org_name[4:]  # strip "org-"

    # Fetch org_id (Zitadel org ID) from Gitea org metadata
    org_id = await _get_org_id(gitea_org_name)
    if not org_id:
        logger.warning("webhook_ignored", reason="org_id_not_found", gitea_org=gitea_org_name)
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

    queued = 0
    deleted = 0

    # Schedule a debounced ingest for each changed file.
    # queueing_lock ensures at most one pending task per document: if the user
    # keeps saving, all intermediate saves hit AlreadyEnqueued and are silently
    # dropped. The task fetches the LATEST content from Gitea at execution time,
    # so the knowledge layer always receives the final version.
    # Falls back to immediate ingest when enrichment is disabled (no Procrastinate app).
    for path in changed:
        # Personal KB: the MCP posts with kb_slug=personal-{user_id}, but the Gitea
        # repo is still named "personal" (legacy). Handle both patterns:
        # - kb_slug starts with "personal-" → new pattern, user_id is in the slug
        # - kb_slug == "personal" → legacy Gitea repo, extract user_id from path
        #   and rewrite slug to personal-{user_id}
        webhook_user_id: str | None = None
        if kb_slug == "personal" and path.startswith("users/"):
            path_parts = path.split("/")
            if len(path_parts) >= 2 and path_parts[1]:
                webhook_user_id = path_parts[1]
                kb_slug = f"personal-{webhook_user_id}"
        elif kb_slug.startswith("personal-") and path.startswith("users/"):
            path_parts = path.split("/")
            if len(path_parts) >= 2 and path_parts[1]:
                webhook_user_id = path_parts[1]

        if settings.enrichment_enabled:
            try:
                import datetime as _dt

                from procrastinate.exceptions import AlreadyEnqueued

                from knowledge_ingest import enrichment_tasks
                proc_app = enrichment_tasks.get_app()
                await proc_app.ingest_from_gitea.configure(  # type: ignore[attr-defined]
                    queueing_lock=f"gitea:{org_id}:{kb_slug}:{path}",
                    schedule_in=_dt.timedelta(seconds=settings.ingest_debounce_seconds),
                ).defer_async(
                    org_id=org_id,
                    kb_slug=kb_slug,
                    path=path,
                    gitea_repo=full_name,
                    user_id=webhook_user_id,
                )
                queued += 1
            except AlreadyEnqueued:
                logger.debug("ingest_debounced", kb_slug=kb_slug, path=path)
            except Exception as exc:
                logger.warning("ingest_queue_failed", path=path, error=str(exc))
        else:
            # No Procrastinate (enrichment disabled) — ingest immediately as before
            content = await _fetch_gitea_file(full_name, path)
            if content is None:
                logger.warning("gitea_fetch_failed", path=path, repo=full_name)
                continue
            req = IngestRequest(
                org_id=org_id, kb_slug=kb_slug, path=path,
                content=content, source_type="docs",
                content_type="kb_article",
                user_id=webhook_user_id,
            )
            try:
                await ingest_document(req)
                queued += 1
            except Exception as exc:
                logger.warning("ingest_failed", path=path, error=str(exc))

    # Delete removed files — immediate, no debounce needed
    # Order: Qdrant → fetch episode IDs → Graphiti → PG metadata → PG soft-delete
    # Each step has its own try/except so partial failures don't block subsequent steps.
    for path in removed:
        try:
            await qdrant_store.delete_document(org_id, kb_slug, path)
        except Exception as exc:
            logger.warning(
                "page_qdrant_delete_failed",
                org_id=org_id, kb_slug=kb_slug, path=path, error=str(exc),
            )

        # Graphiti cleanup: fetch episode IDs before soft-delete (reads extra field)
        if settings.graphiti_enabled:
            try:
                episode_ids = await pg_store.get_page_episode_ids(org_id, kb_slug, path)
                if episode_ids:
                    await graph_module.delete_kb_episodes(org_id, episode_ids)
            except Exception as exc:
                logger.warning(
                    "page_graph_cleanup_failed",
                    org_id=org_id, kb_slug=kb_slug, path=path, error=str(exc),
                )

        # Metadata cleanup: derivations, artifact_entities, embedding_queue
        try:
            await pg_store.cleanup_page_metadata(org_id, kb_slug, path)
        except Exception as exc:
            logger.warning(
                "page_metadata_cleanup_failed",
                org_id=org_id, kb_slug=kb_slug, path=path, error=str(exc),
            )

        try:
            await pg_store.soft_delete_artifact(org_id, kb_slug, path)
            deleted += 1
        except Exception as exc:
            logger.warning(
                "page_soft_delete_failed",
                org_id=org_id, kb_slug=kb_slug, path=path, error=str(exc),
            )

    return {"status": "ok", "queued": queued, "deleted": deleted, "org_slug": org_slug}


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
        logger.warning("gitea_api_error", gitea_org=gitea_org_name, error=str(exc))
        return None


@router.delete("/ingest/v1/kb")
async def delete_kb_route(request: Request, org_id: str, kb_slug: str) -> dict:
    """Delete all data for a knowledge base: graph nodes + Qdrant chunks + PostgreSQL records.
    Called by the portal on KB deletion. Scoped to (org_id, kb_slug).
    """
    _verify_internal_secret(request)
    # Fetch episode IDs before PG deletion — graph cleanup requires them.
    episode_ids = await pg_store.get_episode_ids(org_id, kb_slug)
    await graph_module.delete_kb_episodes(org_id, episode_ids)
    await qdrant_store.delete_kb(org_id, kb_slug)
    await pg_store.delete_kb(org_id, kb_slug)
    logger.info("kb_deleted", org_id=org_id, kb_slug=kb_slug, episodes_deleted=len(episode_ids))
    return {"status": "ok"}


@router.delete("/ingest/v1/connector")
async def delete_connector_route(
    request: Request, org_id: str, kb_slug: str, connector_id: str
) -> dict:
    """Delete all data for a connector: FalkorDB graph nodes + Qdrant chunks + PostgreSQL records.

    Scoped to (org_id, kb_slug, connector_id). Called by the portal on connector deletion
    and by operators for manual cleanup. Only affects documents tagged with source_connector_id.
    """
    _verify_internal_secret(request)
    episode_ids = await pg_store.get_connector_episode_ids(org_id, kb_slug, connector_id)
    await graph_module.delete_kb_episodes(org_id, episode_ids)
    await qdrant_store.delete_connector(org_id, kb_slug, connector_id)
    artifacts_deleted = await pg_store.delete_connector_artifacts(org_id, kb_slug, connector_id)
    logger.info(
        "connector_deleted",
        org_id=org_id,
        kb_slug=kb_slug,
        connector_id=connector_id,
        episodes_deleted=len(episode_ids),
        artifacts_deleted=artifacts_deleted,
    )
    return {"status": "ok", "episodes_deleted": len(episode_ids), "artifacts_deleted": artifacts_deleted}  # noqa: E501


@router.patch("/ingest/v1/kb/visibility")
async def update_kb_visibility_route(request: Request, req: UpdateKBVisibilityRequest) -> dict:
    """Update visibility for a KB: persists to kb_config table and backfills all Qdrant chunks."""
    _verify_internal_secret(request)
    pool = await get_pool()
    await kb_config.set_kb_visibility(req.org_id, req.kb_slug, req.visibility, pool)
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
        logger.warning("gitea_fetch_failed", path=path, repo=repo_full_name, error=str(exc))
        return None


async def _list_gitea_md_files(repo_full_name: str) -> list[str]:
    """List all .md files in a Gitea repo (excluding _ prefixed files)."""
    try:
        async with httpx.AsyncClient(
            base_url=settings.gitea_url,
            headers={"Authorization": f"token {settings.gitea_token}"},
            timeout=10.0,
        ) as client:
            resp = await client.get(
                f"/api/v1/repos/{repo_full_name}/git/trees/HEAD",
                params={"recursive": "true"},
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                item["path"]
                for item in data.get("tree", [])
                if item.get("type") == "blob"
                and item["path"].endswith(".md")
                and not item["path"].startswith("_")
            ]
    except Exception as exc:
        logger.warning("gitea_list_failed", repo=repo_full_name, error=str(exc))
        return []


async def _register_gitea_webhook(gitea_repo: str, webhook_url: str) -> None:
    """Register a push webhook on a Gitea repo."""
    config: dict = {"url": webhook_url, "content_type": "json"}
    if settings.gitea_webhook_secret:
        config["secret"] = settings.gitea_webhook_secret
    async with httpx.AsyncClient(
        base_url=settings.gitea_url,
        headers={"Authorization": f"token {settings.gitea_token}"},
        timeout=10.0,
    ) as client:
        resp = await client.post(
            f"/api/v1/repos/{gitea_repo}/hooks",
            json={"type": "gitea", "config": config, "events": ["push"], "active": True},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Gitea returned {resp.status_code}: {resp.text}")


async def _deregister_gitea_webhook(gitea_repo: str, webhook_url: str) -> None:
    """Remove our push webhook from a Gitea repo (matched by callback URL)."""
    async with httpx.AsyncClient(
        base_url=settings.gitea_url,
        headers={"Authorization": f"token {settings.gitea_token}"},
        timeout=10.0,
    ) as client:
        resp = await client.get(f"/api/v1/repos/{gitea_repo}/hooks", params={"limit": 50})
        if resp.status_code != 200:
            logger.warning("gitea_hooks_list_failed", repo=gitea_repo, status=resp.status_code)
            return
        for hook in resp.json():
            if hook.get("config", {}).get("url") == webhook_url:
                await client.delete(f"/api/v1/repos/{gitea_repo}/hooks/{hook['id']}")
                return


@router.post("/ingest/v1/kb/webhook")
async def register_kb_webhook(request: Request, req: KBWebhookRequest) -> dict:
    """Register a Gitea push webhook for a KB. Called by Docs on KB creation."""
    _verify_internal_secret(request)
    webhook_url = f"{settings.knowledge_ingest_public_url}/ingest/v1/webhook/gitea"
    await _register_gitea_webhook(req.gitea_repo, webhook_url)
    logger.info("webhook_registered", org_id=req.org_id, kb_slug=req.kb_slug, repo=req.gitea_repo)
    return {"status": "ok"}


@router.delete("/ingest/v1/kb/webhook")
async def deregister_kb_webhook(request: Request, req: KBWebhookRequest) -> dict:
    """De-register the Gitea push webhook for a KB. Called by Docs on KB deletion."""
    _verify_internal_secret(request)
    webhook_url = f"{settings.knowledge_ingest_public_url}/ingest/v1/webhook/gitea"
    await _deregister_gitea_webhook(req.gitea_repo, webhook_url)
    logger.info("webhook_deregistered", org_id=req.org_id, kb_slug=req.kb_slug, repo=req.gitea_repo)
    return {"status": "ok"}


@router.post("/ingest/v1/kb/sync")
async def bulk_sync_kb_route(request: Request, req: BulkSyncRequest) -> dict:
    """Re-index all pages of a KB from Gitea. Called by Docs on KB creation or for recovery."""
    _verify_internal_secret(request)
    pages = await _list_gitea_md_files(req.gitea_repo)
    ingested = 0
    for path in pages:
        content = await _fetch_gitea_file(req.gitea_repo, path)
        if content is None:
            logger.warning("gitea_fetch_failed", path=path, repo=req.gitea_repo)
            continue
        ingest_req = IngestRequest(
            org_id=req.org_id,
            kb_slug=req.kb_slug,
            path=path,
            content=content,
            source_type="docs",
            content_type="kb_article",
        )
        try:
            await ingest_document(ingest_req)
            ingested += 1
        except Exception as exc:
            logger.warning("bulk_sync_failed", path=path, error=str(exc))
    logger.info("bulk_sync_complete", org_id=req.org_id, kb_slug=req.kb_slug, pages=ingested)
    return {"status": "ok", "pages": ingested}
