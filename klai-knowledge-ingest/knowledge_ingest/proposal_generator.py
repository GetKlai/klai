"""
Proposal generator — suggests new taxonomy categories based on unmatched documents.

Called after a batch ingest when >= 3 documents had taxonomy_node_id = null.
Uses klai-fast to suggest a category name for the cluster, then submits via portal_client.
Deduplication: checks existing pending proposals before submitting (24h window enforced by portal).
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx
import structlog

from knowledge_ingest.config import settings
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
    "Respond with JSON only: {\"category_name\": <string>}."
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
    except (asyncio.TimeoutError, Exception) as exc:
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

    # Submit proposal via portal_client
    proposal = TaxonomyProposal(
        proposal_type="new_node",
        suggested_name=suggested_name,
        document_count=len(unmatched_documents),
        sample_titles=[doc.title for doc in unmatched_documents[:5]],
    )
    await submit_taxonomy_proposal(kb_slug=kb_slug, org_id=org_id, proposal=proposal)
    logger.info(
        "taxonomy_proposal_submitted",
        kb_slug=kb_slug,
        suggested_name=suggested_name,
        unmatched_count=len(unmatched_documents),
    )


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
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return parsed.get("category_name") or None
