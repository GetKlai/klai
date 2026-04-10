"""
Proposal generator — suggests new taxonomy categories based on unmatched documents.

Called after a batch ingest when >= 3 documents had taxonomy_node_id = null.
Uses klai-fast to suggest a category name for the cluster, then submits via portal_client.
Deduplication: checks existing pending proposals before submitting (24h window enforced by portal).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import httpx
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
    "that cover documents not fitting the existing ones. Return an empty list if no new categories are needed."
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
        logger.info("bootstrap_proposals_all_filtered", kb_slug=kb_slug, reason="all proposed categories already exist")
        return 0

    # Generate descriptions for each proposed category in parallel
    sample_titles = [doc.title for doc in documents[:10]]
    desc_tasks = [
        generate_node_description(name, None, sample_titles)
        for name in categories if name
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
    doc_summaries = "\n".join(
        f"- {doc.title}: {doc.content_preview[:150]}"
        for doc in documents
    )
    user_message = f"Documents in this knowledge base:\n{doc_summaries}"
    if existing_names:
        user_message += (
            f"\n\nExisting categories (do NOT propose these again): "
            f"{', '.join(existing_names)}"
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


async def _suggest_category_name(documents: list[DocumentSummary]) -> str | None:
    """Use klai-fast to suggest a category name for a cluster of unmatched documents."""
    doc_summaries = "\n".join(
        f"- {doc.title}: {doc.content_preview[:200]}"
        for doc in documents[:10]
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
