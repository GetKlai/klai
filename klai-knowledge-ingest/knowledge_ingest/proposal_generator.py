"""
Proposal generator — suggests new taxonomy categories based on unmatched documents.

Called after a batch ingest when >= 3 documents had taxonomy_node_id = null.
Uses klai-fast to suggest a category name for the cluster, then submits via portal_client.
Deduplication: checks existing pending proposals before submitting (24h window enforced by portal).

SPEC-TAXONOMY-V2-001: adds generate_bootstrap_proposals_v2 (Clio-style density-driven bootstrap).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
import numpy as np
import structlog

from knowledge_ingest.config import settings
from knowledge_ingest.description_generator import generate_node_description
from knowledge_ingest.portal_client import TaxonomyProposal, submit_taxonomy_proposal
from knowledge_ingest.taxonomy_classifier import TaxonomyNode

logger = structlog.get_logger()

_MIN_UNMATCHED_FOR_PROPOSAL = 3


@dataclass
class DocumentSummary:
    title: str
    content_preview: str


_PROPOSAL_SYSTEM_PROMPT = (
    "You are a knowledge taxonomy assistant. "
    "Given a list of documents that don't fit existing categories, "
    "suggest a concise category name (2-5 words) that would cover them. "
    "\n\nReply with ONLY a JSON object, no markdown, no explanation: "
    '{"category_name": "<string>"}'
)


async def maybe_generate_proposal(
    org_id: str,
    kb_slug: str,
    unmatched_documents: list[DocumentSummary],
    existing_nodes: list[TaxonomyNode],
) -> None:
    """Generate and submit a taxonomy proposal if conditions are met.

    Conditions:
    - At least 3 unmatched documents in the batch
    - PORTAL_INTERNAL_TOKEN is configured
    - Suggested name doesn't already exist among KB's taxonomy nodes
    """
    if len(unmatched_documents) < _MIN_UNMATCHED_FOR_PROPOSAL:
        return

    if not settings.portal_internal_token:
        logger.warning(
            "taxonomy_proposal_skipped",
            reason="missing PORTAL_INTERNAL_TOKEN",
            kb_slug=kb_slug,
        )
        return

    # Generate suggested category name
    try:
        suggested_name = await asyncio.wait_for(
            _suggest_category_name(unmatched_documents),
            timeout=settings.taxonomy_classification_timeout,
        )
    except Exception as exc:
        logger.warning(
            "taxonomy_proposal_generation_failed",
            kb_slug=kb_slug,
            error=str(exc),
        )
        return

    if not suggested_name:
        return

    # Check that suggested name doesn't already exist
    existing_names = {node.name.lower() for node in existing_nodes}
    if suggested_name.lower() in existing_names:
        logger.info(
            "taxonomy_proposal_skipped",
            reason="name_already_exists",
            suggested_name=suggested_name,
            kb_slug=kb_slug,
        )
        return

    # Generate description (same pattern as generate_bootstrap_proposals)
    sample_titles = [doc.title for doc in unmatched_documents[:5]]
    try:
        description = await generate_node_description(suggested_name, None, sample_titles)
    except Exception:
        logger.warning(
            "taxonomy_proposal_description_failed",
            kb_slug=kb_slug,
            suggested_name=suggested_name,
        )
        description = ""

    # Submit proposal via portal_client
    proposal = TaxonomyProposal(
        proposal_type="new_node",
        suggested_name=suggested_name,
        document_count=len(unmatched_documents),
        sample_titles=sample_titles,
        description=description,
    )
    await submit_taxonomy_proposal(kb_slug=kb_slug, org_id=org_id, proposal=proposal)
    logger.info(
        "taxonomy_proposal_submitted",
        kb_slug=kb_slug,
        suggested_name=suggested_name,
        unmatched_count=len(unmatched_documents),
    )


_BOOTSTRAP_SYSTEM_PROMPT = (
    "You are a knowledge taxonomy assistant. "
    "Given a list of documents from a knowledge base, identify the 3-8 most logical, "
    "non-overlapping top-level categories that together cover all documents. "
    "Each category name should be concise (2-5 words) and distinct. "
    "If existing categories are listed, do NOT repeat them — only propose NEW categories "
    "that cover documents not fitting the existing ones. "
    "Return an empty list if no new categories are needed."
    "\n\nReply with ONLY a JSON object, no markdown, no explanation: "
    '{"categories": ["<string>", ...]}'
)


async def generate_bootstrap_proposals(
    org_id: str,
    kb_slug: str,
    documents: list[DocumentSummary],
    existing_category_names: list[str] | None = None,
) -> int:
    """Scan existing documents and generate bootstrap taxonomy proposals.

    Sends up to 50 document summaries to klai-fast, asks it to identify
    3-8 top-level categories, then submits one proposal per category.
    Returns number of proposals submitted.

    Skips silently when PORTAL_INTERNAL_TOKEN is not configured.
    """
    if not documents:
        return 0
    if not settings.portal_internal_token:
        logger.warning(
            "bootstrap_proposals_skipped",
            reason="missing PORTAL_INTERNAL_TOKEN",
            kb_slug=kb_slug,
        )
        return 0

    try:
        categories = await asyncio.wait_for(
            _suggest_multiple_categories(documents[:50], existing_category_names or []),
            timeout=30.0,
        )
    except Exception as exc:
        logger.warning(
            "bootstrap_proposals_generation_failed",
            kb_slug=kb_slug,
            error=str(exc),
        )
        return 0

    if not categories:
        return 0

    # Filter out names that already exist (case-insensitive) as a safety net,
    # even though the prompt tells the LLM not to propose them.
    existing_lower = {n.lower() for n in (existing_category_names or [])}
    categories = [c for c in categories if c.lower() not in existing_lower]

    if not categories:
        logger.info(
            "bootstrap_proposals_all_filtered",
            kb_slug=kb_slug,
            reason="all proposed categories already exist",
        )
        return 0

    # Generate descriptions for each proposed category in parallel
    sample_titles = [doc.title for doc in documents[:10]]
    desc_tasks = [
        generate_node_description(name, None, sample_titles) for name in categories if name
    ]
    descriptions = await asyncio.gather(*desc_tasks, return_exceptions=True)

    submitted = 0
    for i, name in enumerate(categories):
        desc = descriptions[i] if i < len(descriptions) and isinstance(descriptions[i], str) else ""
        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name=name,
            document_count=len(documents),
            sample_titles=[doc.title for doc in documents[:5]],
            description=desc,
        )
        await submit_taxonomy_proposal(kb_slug=kb_slug, org_id=org_id, proposal=proposal)
        submitted += 1
        logger.info(
            "bootstrap_proposal_submitted",
            kb_slug=kb_slug,
            suggested_name=name,
            description=desc,
        )

    logger.info(
        "bootstrap_proposals_complete",
        kb_slug=kb_slug,
        document_count=len(documents),
        proposals_submitted=submitted,
    )
    return submitted


async def _suggest_multiple_categories(
    documents: list[DocumentSummary],
    existing_names: list[str],
) -> list[str]:
    """Use klai-fast to suggest multiple category names for a set of documents."""
    doc_summaries = "\n".join(f"- {doc.title}: {doc.content_preview[:150]}" for doc in documents)
    user_message = f"Documents in this knowledge base:\n{doc_summaries}"
    if existing_names:
        user_message += (
            f"\n\nExisting categories (do NOT propose these again): {', '.join(existing_names)}"
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{settings.litellm_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.litellm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.taxonomy_classification_model,
                "messages": [
                    {"role": "system", "content": _BOOTSTRAP_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 200,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        content = (content or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(content)
        return [c for c in parsed.get("categories", []) if isinstance(c, str) and c.strip()]


@dataclass
class BootstrapResult:
    """Result from generate_bootstrap_proposals_v2.

    SPEC-TAXONOMY-V2-001 AC-8, AC-13.
    """

    documents_scanned: int
    proposals_submitted: int
    clusters_found: int
    reason: str | None = None


# ---------------------------------------------------------------------------
# V2 bootstrap system prompt (per cluster)
# ---------------------------------------------------------------------------

_BOOTSTRAP_V2_SYSTEM_PROMPT_TEMPLATE = (
    "You are a knowledge taxonomy assistant. You are naming a cluster of "
    "documents from a knowledge base."
    "{kb_description_block}"
    "\n\nGiven example documents that thematically belong together, suggest a "
    "concise category name (2-5 words) that captures their shared theme. "
    "Prefer the user's domain language over generic labels."
    '\n\nReply with ONLY a JSON object: {{"category_name": "<string>"}}'
)


async def _suggest_cluster_name(
    cluster_docs: list[DocumentSummary],
    kb_description: str,
) -> str | None:
    """Use klai-fast to name a single cluster. Returns None on error."""
    kb_description_block = ""
    if kb_description and kb_description.strip():
        kb_description_block = f" The knowledge base is described as:\n{kb_description.strip()}"

    system_prompt = _BOOTSTRAP_V2_SYSTEM_PROMPT_TEMPLATE.format(
        kb_description_block=kb_description_block,
    )

    doc_lines = "\n".join(f"- {doc.title}: {doc.content_preview[:200]}" for doc in cluster_docs)
    user_message = f"{len(cluster_docs)} documents in this cluster:\n{doc_lines}"

    async with httpx.AsyncClient(timeout=settings.taxonomy_classification_timeout) as client:
        resp = await client.post(
            f"{settings.litellm_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.litellm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.taxonomy_classification_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 50,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data["choices"][0]["message"]["content"] or "").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(content)
        return parsed.get("category_name") or None


_BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE = (
    "You are naming N pre-clustered groups of documents from a knowledge base."
    "{kb_description_block}"
    "\n\nThe clustering algorithm has already identified these as DISTINCT topics — "
    "your job is to label each one with a concise, DIFFERENTIATED name.\n\n"
    "Constraints:\n"
    "- Each name 2-5 words\n"
    "- All N names MUST be DISTINCT — no near-duplicates, no overlapping concepts\n"
    "- Use the user's domain language (Dutch terms if docs are in Dutch)\n"
    '- If clusters appear thematically related (e.g., multiple sub-types of "X"), '
    "differentiate by what's UNIQUE about each "
    "(specific tool, specific use-case, specific audience)\n\n"
    "Reply ONLY with JSON, no markdown:\n"
    '{{"names": [{{"cluster_id": <int>, "name": "<string>"}}, ...]}}'
)


async def _suggest_cluster_names_batched(
    cluster_doc_lists: dict[int, list[DocumentSummary]],
    kb_description: str,
) -> dict[int, str | None]:
    """Single-call cross-cluster aware naming.

    Returns {cluster_id: name | None}. None means parse failure or LLM
    omitted that cluster — caller falls back to per-cluster naming for
    those slots.

    SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4: cross-cluster awareness so the
    LLM picks differentiated names instead of 7 variants of "CRM integraties".
    """
    n_clusters = len(cluster_doc_lists)

    # Token-budget guard: too many clusters saturate context; fall back to per-cluster.
    if n_clusters > 30:
        logger.info(
            "bootstrap_batched_naming_skipped_too_many_clusters",
            n_clusters=n_clusters,
            threshold=30,
        )
        return {}

    kb_description_block = ""
    if kb_description and kb_description.strip():
        kb_description_block = f"\nThe knowledge base is described as:\n{kb_description.strip()}"

    system_prompt = _BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE.format(
        kb_description_block=kb_description_block,
    )

    # Build user message: enumerate clusters with up to 8 sample doc-titles each
    cluster_lines: list[str] = []
    for cid, docs in sorted(cluster_doc_lists.items()):
        titles = [doc.title[:200] for doc in docs[:8]]
        titles_str = "\n".join(f"  - {t}" for t in titles)
        cluster_lines.append(f"Cluster {cid}:\n{titles_str}")
    user_message = "\n\n".join(cluster_lines)

    try:
        async with httpx.AsyncClient(timeout=settings.taxonomy_classification_timeout) as client:
            resp = await client.post(
                f"{settings.litellm_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.litellm_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.taxonomy_classification_model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 1500,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data["choices"][0]["message"]["content"] or "").strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            parsed = json.loads(content)
    except Exception as exc:
        logger.warning(
            "bootstrap_batched_naming_failed",
            error=str(exc),
            n_clusters=n_clusters,
        )
        return {}

    # Validate structure
    if not isinstance(parsed, dict) or "names" not in parsed:
        logger.warning(
            "bootstrap_batched_naming_invalid_response",
            reason="missing 'names' key",
        )
        return {}

    names_list = parsed["names"]
    if not isinstance(names_list, list):
        logger.warning(
            "bootstrap_batched_naming_invalid_response",
            reason="'names' is not a list",
        )
        return {}

    result: dict[int, str | None] = {}
    valid_cids = set(cluster_doc_lists.keys())
    for item in names_list:
        if not isinstance(item, dict):
            continue
        cid = item.get("cluster_id")
        name = item.get("name")
        if not isinstance(cid, int) or cid not in valid_cids:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        result[cid] = name.strip()

    return result


async def generate_bootstrap_proposals_v2(
    org_id: str,
    kb_slug: str,
    document_summaries: list[DocumentSummary],
    document_embeddings: np.ndarray,
    existing_nodes: list[TaxonomyNode],
    kb_description: str,
) -> BootstrapResult:
    """Clio-style density-driven taxonomy bootstrap.

    SPEC-TAXONOMY-V2-001 full data flow (steps 1-8):
    1. Receive document-level embeddings (already rolled up by caller).
    2. Check doc_count >= 10 (AC-3).
    3. Run HDBSCAN with adaptive min_cluster_size (AC-1, AC-16).
    4. Cap clusters at taxonomy_bootstrap_max_clusters (AC-7).
    5. For each cluster: pick top-N closest-to-centroid docs (AC-4).
    6. Parallel LLM naming via asyncio.gather with Semaphore(5) (AC-4).
    7. Filter duplicates case-insensitively against existing nodes (AC-6).
    8. Submit remaining proposals via portal_client (AC-18).
    9. Log bootstrap_proposals_complete event (AC-9).

    Args:
        org_id: Zitadel org ID.
        kb_slug: knowledge base slug.
        document_summaries: list of DocumentSummary, one per document.
        document_embeddings: (n_docs, dim) float32 array, unit-normalised.
        existing_nodes: list of existing TaxonomyNode objects (for dedup).
        kb_description: KB description string for LLM context (may be empty).

    Returns:
        BootstrapResult with documents_scanned, proposals_submitted, clusters_found.
    """
    from knowledge_ingest.clustering import (
        closest_to_centroid,
        cluster_documents_hdbscan,
        compute_min_cluster_size,
    )

    doc_count = len(document_summaries)

    # AC-3: too-small KB guard
    if doc_count < 10:
        logger.info(
            "bootstrap_skipped_too_small_kb",
            org_id=org_id,
            kb_slug=kb_slug,
            doc_count=doc_count,
        )
        return BootstrapResult(
            documents_scanned=doc_count,
            proposals_submitted=0,
            clusters_found=0,
        )

    if not settings.portal_internal_token:
        logger.warning(
            "bootstrap_proposals_skipped",
            reason="missing PORTAL_INTERNAL_TOKEN",
            kb_slug=kb_slug,
        )
        return BootstrapResult(
            documents_scanned=doc_count,
            proposals_submitted=0,
            clusters_found=0,
        )

    # Step 3: HDBSCAN clustering (AC-1, AC-16, AC-17)
    # SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B1: pre_reduce=True enables UMAP before HDBSCAN.
    min_cluster_size = compute_min_cluster_size(
        doc_count,
        floor=settings.taxonomy_bootstrap_min_cluster_size_floor,
    )
    labels, metrics = cluster_documents_hdbscan(
        document_embeddings,
        min_cluster_size=min_cluster_size,
        pre_reduce=True,
    )
    clusters_found: int = metrics["clusters_found"]
    outlier_count: int = metrics["outlier_count"]
    cluster_persistence_mean: float | None = metrics["cluster_persistence_mean"]

    if clusters_found == 0:
        logger.info(
            "bootstrap_proposals_complete",
            org_id=org_id,
            kb_slug=kb_slug,
            clusters_found=0,
            outlier_count=outlier_count,
            cluster_persistence_mean=cluster_persistence_mean,
            proposals_submitted=0,
        )
        return BootstrapResult(
            documents_scanned=doc_count,
            proposals_submitted=0,
            clusters_found=0,
        )

    # Build cluster index → list of doc indices
    cluster_map: dict[int, list[int]] = {}
    for idx, lbl in enumerate(labels):
        if int(lbl) >= 0:
            cluster_map.setdefault(int(lbl), []).append(idx)

    # Step 4 (AC-7): cap at max_clusters, keep largest
    max_clusters = settings.taxonomy_bootstrap_max_clusters
    if len(cluster_map) > max_clusters:
        sorted_clusters = sorted(cluster_map.items(), key=lambda x: len(x[1]), reverse=True)
        kept = dict(sorted_clusters[:max_clusters])
        logger.info(
            "bootstrap_clusters_capped",
            org_id=org_id,
            kb_slug=kb_slug,
            total_clusters=len(cluster_map),
            kept_clusters=max_clusters,
        )
        cluster_map = kept
        clusters_found = len(cluster_map)

    top_n = settings.taxonomy_bootstrap_top_n_per_cluster

    # Step 5: pick top-N closest-to-centroid docs per cluster (AC-4)
    cluster_doc_lists: dict[int, list[DocumentSummary]] = {}
    for cid, indices in cluster_map.items():
        top_indices = closest_to_centroid(indices, document_embeddings, n=top_n)
        # Filter docs with too-short content_preview (mirrors v1 behavior)
        cluster_docs = [
            document_summaries[i]
            for i in top_indices
            if len(document_summaries[i].content_preview.strip()) >= 50
        ]
        if not cluster_docs:
            # Fallback: include all top docs even if short
            cluster_docs = [document_summaries[i] for i in top_indices]
        cluster_doc_lists[cid] = cluster_docs

    # Step 6: batched cross-cluster naming (B4), per-cluster fallback for misses
    # SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4: single LLM call names all clusters at once
    # so it can enforce distinctness across names (prevents 7 variants of "CRM integraties").
    semaphore = asyncio.Semaphore(5)

    async def _name_cluster(cid: int, docs: list[DocumentSummary]) -> tuple[int, str | None]:
        async with semaphore:
            try:
                name = await asyncio.wait_for(
                    _suggest_cluster_name(docs, kb_description),
                    timeout=settings.taxonomy_classification_timeout,
                )
                return cid, name
            except Exception as exc:
                logger.warning(
                    "bootstrap_cluster_naming_failed",
                    kb_slug=kb_slug,
                    cluster_id=cid,
                    error=str(exc),
                )
                return cid, None

    # Try batched naming first (cross-cluster aware)
    batched_names = await _suggest_cluster_names_batched(cluster_doc_lists, kb_description)
    naming_results: list[tuple[int, str | None]] = []

    # Collect results from batched call; identify clusters that need per-cluster fallback
    missing_cids = [cid for cid in cluster_doc_lists if not batched_names.get(cid)]
    for cid in cluster_doc_lists:
        name_from_batch = batched_names.get(cid)
        if name_from_batch:
            naming_results.append((cid, name_from_batch))

    # Per-cluster fallback ONLY for clusters batched call didn't name
    if missing_cids:
        logger.info(
            "bootstrap_naming_fallback_to_per_cluster",
            kb_slug=kb_slug,
            count=len(missing_cids),
        )
        fallback_tasks = [_name_cluster(cid, cluster_doc_lists[cid]) for cid in missing_cids]
        fallback_results: list[tuple[int, str | None]] = await asyncio.gather(*fallback_tasks)
        naming_results.extend(fallback_results)

    # Step 7: filter duplicates (AC-6)
    existing_names_lower = {node.name.lower() for node in existing_nodes}
    proposals_to_submit: list[tuple[int, str]] = []

    for cid, name in naming_results:
        if not name:
            continue
        if name.lower() in existing_names_lower:
            logger.info(
                "bootstrap_proposal_skipped_duplicate_name",
                kb_slug=kb_slug,
                suggested_name=name,
                cluster_id=cid,
            )
            continue
        proposals_to_submit.append((cid, name))

    # AC-8: all duplicates case
    if naming_results and all(
        not name or name.lower() in existing_names_lower
        for _, name in naming_results
        if name is not None
    ):
        # Check if we had valid names that were all duplicates
        valid_names = [name for _, name in naming_results if name]
        if valid_names and not proposals_to_submit:
            logger.info(
                "bootstrap_proposals_complete",
                org_id=org_id,
                kb_slug=kb_slug,
                clusters_found=clusters_found,
                outlier_count=outlier_count,
                cluster_persistence_mean=cluster_persistence_mean,
                proposals_submitted=0,
            )
            return BootstrapResult(
                documents_scanned=doc_count,
                proposals_submitted=0,
                clusters_found=clusters_found,
                reason="all_duplicates",
            )

    # SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B2: generate descriptions in parallel
    # (V1 had this; V2 dropped it — this restores it).
    desc_tasks = [
        generate_node_description(
            name,
            None,
            [doc.title for doc in cluster_doc_lists.get(cid, [])[:5]],
        )
        for cid, name in proposals_to_submit
    ]
    desc_results = await asyncio.gather(*desc_tasks, return_exceptions=True)

    # Step 8: submit proposals (AC-18)
    submitted = 0
    for i, (cid, name) in enumerate(proposals_to_submit):
        cluster_doc_list = cluster_doc_lists.get(cid, [])
        sample_titles = [doc.title for doc in cluster_doc_list[:5]]

        # Use generated description if available; fall back to "" on failure (AC-7)
        raw_desc = desc_results[i] if i < len(desc_results) else Exception("index out of range")
        if isinstance(raw_desc, str):
            description = raw_desc
        else:
            description = ""
            logger.warning(
                "bootstrap_description_generation_failed",
                kb_slug=kb_slug,
                cluster_id=cid,
                error=str(raw_desc) if raw_desc is not None else None,
            )

        proposal = TaxonomyProposal(
            proposal_type="new_node",
            suggested_name=name,
            document_count=len(cluster_doc_list),
            sample_titles=sample_titles,
            description=description,
        )
        await submit_taxonomy_proposal(kb_slug=kb_slug, org_id=org_id, proposal=proposal)
        submitted += 1

    # Step 9: AC-9 log
    # SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B5: log cluster_persistence_mean instead of dbcv_score.
    # dbcv_score assumed relative_validity_ which sklearn 1.8 does not expose.
    logger.info(
        "bootstrap_proposals_complete",
        org_id=org_id,
        kb_slug=kb_slug,
        clusters_found=clusters_found,
        outlier_count=outlier_count,
        cluster_persistence_mean=cluster_persistence_mean,
        proposals_submitted=submitted,
    )

    return BootstrapResult(
        documents_scanned=doc_count,
        proposals_submitted=submitted,
        clusters_found=clusters_found,
    )


async def _suggest_category_name(documents: list[DocumentSummary]) -> str | None:
    """Use klai-fast to suggest a category name for a cluster of unmatched documents."""
    doc_summaries = "\n".join(
        f"- {doc.title}: {doc.content_preview[:200]}" for doc in documents[:10]
    )
    user_message = f"Documents that don't fit existing categories:\n{doc_summaries}"

    async with httpx.AsyncClient(timeout=settings.taxonomy_classification_timeout) as client:
        resp = await client.post(
            f"{settings.litellm_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.litellm_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.taxonomy_classification_model,
                "messages": [
                    {"role": "system", "content": _PROPOSAL_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 50,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        content = (content or "").strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(content)
        return parsed.get("category_name") or None
