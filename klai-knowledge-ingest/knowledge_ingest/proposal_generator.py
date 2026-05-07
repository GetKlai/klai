"""
Proposal generator — suggests taxonomy category names from clusters of documents.

Two callers, three LLM-prompt strategies, one shared naming-criteria base.

──────────────────────────────────────────────────────────────────────────────
Evolution history (read this BEFORE adding a fourth strategy)
──────────────────────────────────────────────────────────────────────────────

  SPEC-KB-021    : ``maybe_generate_proposal`` — post-ingest, when >= 3 docs
                   land that don't fit existing taxonomy nodes, suggest one
                   new category for them. Single LLM call, single name out.

  SPEC-KB-022    : ``generate_bootstrap_proposals`` (V1) — single-shot 50-doc
                   bootstrap where the LLM did *both* clustering AND naming
                   in one call. Replaced by V2 below; kept for a feature-flag
                   window then deleted (SPEC-TAXONOMY-V2-CONSOLIDATION-001).

  SPEC-TAXONOMY-V2-001 : ``generate_bootstrap_proposals_v2`` — Clio-style.
                   Pre-cluster documents with HDBSCAN, then ask the LLM to
                   *only* name (not cluster). One LLM call per cluster, in
                   parallel. New prompt: ``_BOOTSTRAP_V2_SYSTEM_PROMPT``.

  SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B4 : V2 in prod produced 7 near-duplicate
                   names around "CRM integraties" (HDBSCAN found valid sub-
                   clusters per CRM product, but per-cluster naming had no
                   awareness of siblings). Added ``_BATCHED_NAMING_SYSTEM_PROMPT``
                   — single LLM call sees all clusters, enforces distinct
                   names. Single-cluster prompt kept as fallback when batched
                   times out or returns nothing for a cluster_id.

  SPEC-TAXONOMY-V2-CONSOLIDATION-001 : the Unify-bug discovery — the batched
                   prompt's "differentiate by what's UNIQUE about each
                   cluster" rule biased the LLM to pick the most salient
                   brand-noun (e.g. "Unify-telefoons") even when only one of
                   eight docs in the cluster mentioned that brand. Two parallel
                   prompts had silently drifted (single said "prefer domain
                   language", batched said "differentiate by unique"), giving
                   different output bias depending on which path fired.

                   Fix-forward: extracted ``_NAMING_CRITERIA`` as a shared
                   base. Each prompt composes the base + its strategy-specific
                   framing (incremental / single / batched). The Unify-bug fix
                   ("name common theme, NOT salient minority brand") lands
                   in the base — applies to all prompts automatically. V1
                   bootstrap path + feature flag deleted.

──────────────────────────────────────────────────────────────────────────────

Why three prompts (not one): the *strategies* genuinely differ.

- Incremental: adds one node to an existing taxonomy. Must avoid existing names.
- Single-cluster: names one cluster, no siblings visible. (Batched fallback.)
- Batched: names N clusters in one call, must produce distinct names.

But the *naming criteria* are identical across strategies — and that's what
the shared base captures. Sibling-distinctness in batched is enforced *on top*
of the base, not by overriding the base's "common theme" rule.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field

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


# ---------------------------------------------------------------------------
# Shared naming criteria — single source of truth for what makes a good name.
# ---------------------------------------------------------------------------
# Every cluster-naming prompt below composes this block. Bug-fixes to the
# rules land HERE and apply everywhere automatically. Do not duplicate or
# override these rules in a strategy-specific prompt — extend, don't fork.

_NAMING_CRITERIA = (
    "Apply these rules to every cluster name:\n\n"
    "- The name MUST describe what is COMMON across ALL documents in the cluster.\n"
    "  Look for the shared theme, NOT the most prominent or branded item.\n"
    "- If documents span multiple brands, products, or providers, use a generic\n"
    '  descriptor (e.g., "Telefoonconfiguratie - diverse providers"), NOT the\n'
    "  most salient brand appearing in only some docs.\n"
    "- Use specific terms (brand, product, tool) ONLY when they apply to ALL\n"
    "  documents in the cluster.\n"
    "- 2-5 words, in the user's domain language (Dutch if docs are in Dutch).\n"
)


# ---------------------------------------------------------------------------
# Strategy 1: incremental — name ONE cluster of post-ingest unmatched docs.
# Used by ``maybe_generate_proposal``. Existing-node names are avoided
# at the call site (post-LLM dedup), not enforced in the prompt.
# ---------------------------------------------------------------------------

_PROPOSAL_SYSTEM_PROMPT = (
    "You are a knowledge taxonomy assistant. You are proposing a NEW "
    "category for documents that don't fit any existing category.\n\n"
    f"{_NAMING_CRITERIA}"
    "\nReply with ONLY a JSON object, no markdown, no explanation: "
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


# V1 ``generate_bootstrap_proposals`` (single-shot LLM clustering+naming) +
# its prompt + helper were deleted in SPEC-TAXONOMY-V2-CONSOLIDATION-001.
# V2 (HDBSCAN pre-clustering + per-cluster LLM naming) has been the default
# (``taxonomy_bootstrap_v2_enabled=True``) since SPEC-TAXONOMY-V2-001 shipped
# in PR #408. The fallback was never re-enabled in production.


@dataclass
class BootstrapResult:
    """Result from generate_bootstrap_proposals_v2.

    SPEC-TAXONOMY-V2-001 AC-8, AC-13.
    SPEC-TAXONOMY-MERGE-DETECT-001 AC-9: base_clusters_found added.
    """

    documents_scanned: int
    proposals_submitted: int
    clusters_found: int
    # When consolidate ran, clusters_found = post-consolidate parent count
    # and base_clusters_found = pre-consolidate base count. When consolidate
    # was skipped or fell back, base_clusters_found = clusters_found.
    base_clusters_found: int = 0
    reason: str | None = None


@dataclass
class ParentCategory:
    """One LLM-proposed parent category from consolidate step (SPEC-TAXONOMY-MERGE-DETECT-001).

    Built by ``_consolidate_to_parents``; consumed by the submit-loop in
    ``generate_bootstrap_proposals_v2`` to produce TaxonomyProposal payloads.
    """

    name: str
    rationale: str
    child_cluster_ids: list[int]
    # Description is generated AFTER group-and-assign via generate_node_description
    # — same path that production new_node proposals use.
    description: str = ""
    # Aggregated fields (populated by _consolidate_to_parents):
    document_count: int = 0
    sample_titles: list[str] = field(default_factory=list)
    centroid: list[float] | None = None
    child_cluster_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Strategy 2: V2 single-cluster — name ONE pre-clustered (HDBSCAN) group.
# Used as fallback when batched naming below times out or returns nothing
# for a cluster_id. Shares ``_NAMING_CRITERIA`` with batched, so the rules
# the LLM applies don't depend on which path fired.
# ---------------------------------------------------------------------------

_BOOTSTRAP_V2_SYSTEM_PROMPT_TEMPLATE = (
    "You are a knowledge taxonomy assistant. You are naming a cluster of "
    "documents from a knowledge base."
    "{kb_description_block}"
    "\n\n"
    f"{_NAMING_CRITERIA}"
    '\nReply with ONLY a JSON object: {{"category_name": "<string>"}}'
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


# ---------------------------------------------------------------------------
# Strategy 3: V2 batched — name N pre-clustered groups in ONE LLM call.
# Cross-cluster awareness lets the LLM enforce distinct names when HDBSCAN
# finds related sub-clusters (B4: prevents "7 variants of CRM integraties").
#
# IMPORTANT: the sibling-distinctness rule must NOT override the criteria
# base's "common theme" rule. The pre-Consolidation prompt said "differentiate
# by what's UNIQUE about each cluster (specific tool/use-case/audience)" —
# that biased the LLM toward picking the most salient minority brand as the
# label (the Unify-bug). The current formulation differentiates by COMMON-
# WITHIN-each-cluster-more-specifically, preserving distinctness without the
# salient-brand bias.
# ---------------------------------------------------------------------------

_BATCHED_NAMING_SYSTEM_PROMPT_TEMPLATE = (
    "You are naming N pre-clustered groups of documents from a knowledge base."
    "{kb_description_block}"
    "\n\nThe clustering algorithm has already identified these as DISTINCT "
    "topics — your job is to label each one with a concise, DIFFERENTIATED "
    "name.\n\n"
    f"{_NAMING_CRITERIA}"
    "\nAdditional cross-cluster constraint:\n"
    "- All N names MUST be DISTINCT — no near-duplicates, no overlapping concepts.\n"
    "- When clusters share an overarching theme but differ in scope, "
    "differentiate by what is COMMON within each cluster at a more specific "
    "level (the shared sub-theme of those docs), NOT by picking the most "
    "unique-looking item per cluster.\n\n"
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


# ---------------------------------------------------------------------------
# Strategy 4 (SPEC-TAXONOMY-MERGE-DETECT-001): Clio-style consolidation.
# After base clusters have been named (Strategy 3) and dedupped, this step
# groups them into 5-9 IA-friendly parent categories in a SINGLE LLM call.
#
# Inspired by Anthropic Clio (arxiv 2412.13678): "propose higher-level names
# that encompass these clusters" + "assign each base cluster to one parent",
# but collapsed to one call because we have 5-30 clusters (no neighborhood
# k-means step needed). Reuses _NAMING_CRITERIA so naming rules stay
# consistent across all strategies.
#
# Validated on Voys/support via the dry-run script
# (knowledge_ingest/scripts/dry_run_merge_consolidate.py): 15 base clusters
# → 7-8 parents within target_min/target_max, parent descriptions of
# production quality.
# ---------------------------------------------------------------------------

_MERGE_CONSOLIDATE_SYSTEM_PROMPT_TEMPLATE = (
    "You are a knowledge taxonomy assistant. You will see {n_clusters} "
    "fine-grained document clusters from a knowledge base. Your job: "
    "organise them into between {target_min} and {target_max} broader parent "
    "categories suitable for browsing/navigation."
    "{kb_description_block}"
    "\n\nThis is information architecture, not classification. The target "
    "count of {target_min}-{target_max} follows Miller's Law: more would "
    "overwhelm a sidebar, fewer would lose meaningful distinctions."
    "\n\n"
    f"{_NAMING_CRITERIA}"
    "\nAdditional rules for parent categories:\n"
    "- Each parent category SHOULD encompass 1-5 base clusters that share an "
    "  overarching theme.\n"
    "- A parent category MAY contain a single base cluster if that cluster "
    "  is genuinely distinct from all others — do not force-group unrelated "
    "  things just to lower the count.\n"
    "- Each base cluster MUST be assigned to exactly one parent category.\n"
    "- Parent names follow the same naming criteria as base clusters: name "
    "  the SHARED theme, not the most salient sub-item.\n"
    "- DO NOT keep clusters separate solely because their base names differ — "
    "  the base names came from a per-cluster naming step instructed to "
    "  differentiate, so naming-disagreement is expected and not a signal "
    "  that the clusters belong apart.\n"
    "- Aim for {target_min}-{target_max} parents. If you genuinely cannot "
    "  reach {target_max} without grouping unrelated things, fewer is OK. "
    "  If staying under {target_max} forces you to lump unrelated topics "
    "  together, slightly more is OK.\n"
    "\nBalance constraints (avoid one over-large parent — these scale with "
    "the actual corpus, not absolute numbers):\n"
    "- Total documents across all base clusters: {total_docs}. "
    "Total base clusters: {n_clusters}.\n"
    "- Soft cap on parent size: a single parent SHOULD NOT hold more than "
    "  ~25% of total documents (~{doc_cap} docs) OR more than ~33% of base "
    "  clusters (~{cluster_cap} clusters). If EITHER threshold is "
    "  exceeded, prefer to split that parent into more specific "
    "  sub-themes.\n"
    "- These caps are SOFT — quality of grouping outranks balance. If the "
    "  only sensible split would mix unrelated themes (e.g. forcing a "
    "  cohesive 'CRM' parent to split into arbitrary halves), keep it "
    "  together and accept the imbalance. Bias toward splitting when an "
    "  internal split-line is meaningful (configuration vs hardware vs "
    "  network), bias toward keeping when no clean split exists.\n"
    "- Aim for roughly balanced parent sizes — no one parent should "
    "  dominate sidebar browsing.\n"
    "- Splitting MAY push the parent count slightly above {target_max} — "
    "  that's acceptable. Hard cap at {hard_cap} parents.\n"
    "\nReply ONLY with JSON, no markdown:\n"
    '{{"parents": [{{"name": "<parent name>", "rationale": "<why these '
    'belong together, max 200 chars>", "child_cluster_ids": [<int>, ...]}}, '
    "...]}}"
)


async def _consolidate_to_parents(
    base_proposals: list[tuple[int, str]],
    cluster_doc_lists: dict[int, list[DocumentSummary]],
    cluster_map: dict[int, list[int]],
    document_embeddings: np.ndarray,
    kb_description: str,
    target_min: int,
    target_max: int,
) -> list[ParentCategory]:
    """Single-call Clio-style group-and-assign + parallel parent descriptions.

    Args:
        base_proposals: list of (cluster_id, name) tuples from the post-dedup set.
        cluster_doc_lists: cluster_id → list of DocumentSummary (for sample titles
            and base-cluster description input).
        cluster_map: cluster_id → list of doc indices into document_embeddings
            (for centroid computation).
        document_embeddings: (n_docs, dim) unit-normalised embedding matrix.
        kb_description: KB description for prompt context (may be empty).
        target_min: desired minimum number of parent categories.
        target_max: desired maximum number of parent categories.

    Returns:
        List of ParentCategory with name, description, child_cluster_ids,
        document_count, sample_titles (union, capped at 10), centroid
        (doc-count-weighted unit-normalised mean), and child_cluster_names.

    Raises:
        ValueError: malformed LLM response.
        Exception: any HTTP / timeout / JSON parsing error from the LLM call.

    Caller (generate_bootstrap_proposals_v2) catches these and falls back
    to submitting base clusters per AC-5.
    """
    import json as _json

    if len(base_proposals) <= 1:
        # Trivial case: nothing to consolidate.
        return []

    # We need per-cluster descriptions as input to the LLM (matches the
    # validated dry-run script behaviour). Generate them in parallel.
    desc_tasks_pre = [
        generate_node_description(
            name,
            None,
            [doc.title for doc in cluster_doc_lists.get(cid, [])[:5]],
        )
        for cid, name in base_proposals
    ]
    base_descriptions_results = await asyncio.gather(*desc_tasks_pre, return_exceptions=True)
    base_descriptions: dict[int, str] = {}
    for (cid, _), desc in zip(base_proposals, base_descriptions_results, strict=True):
        base_descriptions[cid] = desc if isinstance(desc, str) else ""

    # Build prompt context
    kb_description_block = ""
    if kb_description and kb_description.strip():
        kb_description_block = f"\n\nThe knowledge base is described as:\n{kb_description.strip()}"

    total_docs = sum(len(cluster_map[cid]) for cid, _ in base_proposals)
    n_clusters = len(base_proposals)
    doc_cap = max(1, total_docs // 4)
    cluster_cap = max(1, n_clusters // 3)
    hard_cap = target_max + 2

    system_prompt = _MERGE_CONSOLIDATE_SYSTEM_PROMPT_TEMPLATE.format(
        n_clusters=n_clusters,
        total_docs=total_docs,
        target_min=target_min,
        target_max=target_max,
        doc_cap=doc_cap,
        cluster_cap=cluster_cap,
        hard_cap=hard_cap,
        kb_description_block=kb_description_block,
    )

    cluster_lines: list[str] = []
    for cid, name in base_proposals:
        docs = cluster_doc_lists.get(cid, [])
        title_lines = "\n".join(f"      - {d.title[:140]}" for d in docs[:5])
        descr = base_descriptions.get(cid) or "(no description)"
        cluster_lines.append(
            f'Cluster {cid} "{name}" ({len(cluster_map[cid])} docs):\n'
            f"  Description: {descr}\n"
            f"  Sample titles:\n{title_lines}"
        )
    user_message = "\n\n".join(cluster_lines)

    max_tokens = 600 + 80 * n_clusters

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
                "temperature": 0.2,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()
        data = resp.json()

    content = (data["choices"][0]["message"]["content"] or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = _json.loads(content)
    if not isinstance(parsed, dict) or "parents" not in parsed:
        raise ValueError("Consolidate response missing 'parents' key")

    parents_list = parsed["parents"]
    if not isinstance(parents_list, list):
        raise ValueError("'parents' is not a list")

    valid_cids = {cid for cid, _ in base_proposals}
    name_by_cid = {cid: name for cid, name in base_proposals}
    parents: list[ParentCategory] = []
    seen_cids: set[int] = set()

    for item in parents_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        rationale = item.get("rationale") or ""
        if not isinstance(rationale, str):
            rationale = str(rationale)
        children_raw = item.get("child_cluster_ids", [])
        if not isinstance(children_raw, list):
            children_raw = []
        children: list[int] = []
        for cid in children_raw:
            if not isinstance(cid, int):
                continue
            if cid not in valid_cids or cid in seen_cids:
                continue
            seen_cids.add(cid)
            children.append(cid)
        if children:
            parents.append(
                ParentCategory(
                    name=name.strip(),
                    rationale=rationale.strip(),
                    child_cluster_ids=children,
                )
            )

    # AC-14: collect any unassigned clusters under an "Overig" parent so no
    # content silently disappears. Operator can review and merge/split.
    unassigned = sorted(cid for cid in valid_cids if cid not in seen_cids)
    if unassigned:
        logger.warning(
            "bootstrap_consolidate_unassigned_clusters",
            count=len(unassigned),
            cluster_ids=unassigned,
        )
        parents.append(
            ParentCategory(
                name="Overig",
                rationale="Niet door LLM toegewezen — operator review required.",
                child_cluster_ids=unassigned,
            )
        )

    # Aggregate fields per parent: document_count, sample_titles, centroid,
    # child_cluster_names. These are what the submit-loop turns into a
    # TaxonomyProposal payload.
    for p in parents:
        # document_count = sum of children's doc_count
        p.document_count = sum(len(cluster_map[cid]) for cid in p.child_cluster_ids)
        # sample_titles = round-robin from children, capped at 10
        p.sample_titles = _round_robin_titles(p.child_cluster_ids, cluster_doc_lists)
        # centroid = doc-count-weighted unit-normalised mean of children's centroids
        p.centroid = _weighted_centroid(p.child_cluster_ids, cluster_map, document_embeddings)
        # child_cluster_names for operator transparency
        p.child_cluster_names = [name_by_cid[cid] for cid in p.child_cluster_ids]

    # Generate user-facing descriptions per parent in parallel — same path
    # as production new_node proposals (generate_node_description on
    # parent_name + round-robin sample titles).
    desc_tasks = [generate_node_description(p.name, None, p.sample_titles[:10]) for p in parents]
    descriptions = await asyncio.gather(*desc_tasks, return_exceptions=True)
    for p, desc in zip(parents, descriptions, strict=True):
        p.description = desc if isinstance(desc, str) else ""

    return parents


def _round_robin_titles(
    child_cluster_ids: list[int],
    cluster_doc_lists: dict[int, list[DocumentSummary]],
) -> list[str]:
    """2 titles per child, capped at 10 total. Mirrors the dry-run script."""
    if not child_cluster_ids:
        return []
    per_child = max(2, 10 // max(1, len(child_cluster_ids)))
    titles: list[str] = []
    for cid in child_cluster_ids:
        for doc in cluster_doc_lists.get(cid, [])[:per_child]:
            titles.append(doc.title)
            if len(titles) >= 10:
                return titles
    return titles[:10]


def _weighted_centroid(
    child_cluster_ids: list[int],
    cluster_map: dict[int, list[int]],
    document_embeddings: np.ndarray,
) -> list[float] | None:
    """Doc-count-weighted unit-normalised mean of child centroids.

    Returns None if all children have zero-norm centroids (defensive — should
    not happen with bge-m3, but guards against pathological input).
    """
    indices: list[int] = []
    for cid in child_cluster_ids:
        indices.extend(cluster_map.get(cid, []))
    if not indices:
        return None
    centroid = document_embeddings[indices].mean(axis=0)
    norm = float(np.linalg.norm(centroid))
    if norm == 0:
        return None
    return (centroid / norm).tolist()


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
        cluster_selection_method=settings.taxonomy_bootstrap_cluster_selection_method,
    )
    clusters_found: int = metrics["clusters_found"]
    outlier_count: int = metrics["outlier_count"]
    cluster_probability_mean: float | None = metrics["cluster_probability_mean"]

    if clusters_found == 0:
        logger.info(
            "bootstrap_proposals_complete",
            org_id=org_id,
            kb_slug=kb_slug,
            clusters_found=0,
            outlier_count=outlier_count,
            cluster_probability_mean=cluster_probability_mean,
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
                cluster_probability_mean=cluster_probability_mean,
                proposals_submitted=0,
            )
            return BootstrapResult(
                documents_scanned=doc_count,
                proposals_submitted=0,
                clusters_found=clusters_found,
                reason="all_duplicates",
            )

    # Step 7.5: Clio-style consolidation to 5-9 parents (SPEC-TAXONOMY-MERGE-DETECT-001).
    # Runs only if there are MORE base clusters than the IA target_max — for
    # smaller post-dedup sets (≤ target_max) consolidation has no value.
    # AC-5: any consolidate failure falls back to submitting base clusters.
    base_clusters_count = len(proposals_to_submit)
    parents: list[ParentCategory] | None = None
    if (
        settings.taxonomy_consolidate_enabled
        and base_clusters_count > settings.taxonomy_consolidate_target_max
    ):
        consolidate_t0 = time.monotonic()
        try:
            parents = await _consolidate_to_parents(
                base_proposals=proposals_to_submit,
                cluster_doc_lists=cluster_doc_lists,
                cluster_map=cluster_map,
                document_embeddings=document_embeddings,
                kb_description=kb_description,
                target_min=settings.taxonomy_consolidate_target_min,
                target_max=settings.taxonomy_consolidate_target_max,
            )
            consolidate_latency_ms = int((time.monotonic() - consolidate_t0) * 1000)
            if parents:
                largest = max(parents, key=lambda p: p.document_count)
                largest_doc_pct = (
                    100.0 * largest.document_count / max(1, sum(p.document_count for p in parents))
                )
                logger.info(
                    "bootstrap_consolidate_complete",
                    kb_slug=kb_slug,
                    org_id=org_id,
                    base_clusters=base_clusters_count,
                    parents=len(parents),
                    largest_parent_doc_pct=round(largest_doc_pct, 1),
                    largest_parent_cluster_count=len(largest.child_cluster_ids),
                    latency_ms=consolidate_latency_ms,
                )
        except Exception as exc:
            # AC-5: fall back to base clusters. Bootstrap MUST NOT fail on consolidate failure.
            logger.warning(
                "bootstrap_consolidate_failed",
                kb_slug=kb_slug,
                org_id=org_id,
                error=str(exc),
                base_clusters=base_clusters_count,
                exc_info=True,
            )
            parents = None

    # Step 8: submit proposals (AC-18 base path / AC-7 consolidate path).
    submitted = 0
    if parents:
        # Consolidate path: descriptions and aggregated fields are already
        # populated inside _consolidate_to_parents.
        for p in parents:
            proposal = TaxonomyProposal(
                proposal_type="new_node",
                suggested_name=p.name,
                document_count=p.document_count,
                sample_titles=p.sample_titles[:5],
                description=p.description,
                cluster_centroid=p.centroid,
                child_cluster_names=p.child_cluster_names,
            )
            await submit_taxonomy_proposal(kb_slug=kb_slug, org_id=org_id, proposal=proposal)
            submitted += 1
    else:
        # Base path: SPEC-TAXONOMY-V2-001-FOLLOWUP-001 B2 (generate descriptions in parallel).
        desc_tasks = [
            generate_node_description(
                name,
                None,
                [doc.title for doc in cluster_doc_lists.get(cid, [])[:5]],
            )
            for cid, name in proposals_to_submit
        ]
        desc_results = await asyncio.gather(*desc_tasks, return_exceptions=True)

        for i, (cid, name) in enumerate(proposals_to_submit):
            cluster_doc_list = cluster_doc_lists.get(cid, [])
            sample_titles = [doc.title for doc in cluster_doc_list[:5]]

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

    # Step 9: AC-9 log + SPEC-TAXONOMY-MERGE-DETECT-001 base_clusters_found.
    # When consolidate ran, clusters_found = post-consolidate parent count.
    # When consolidate skipped/failed, clusters_found = base count (back-compat).
    final_clusters_found = len(parents) if parents else base_clusters_count
    logger.info(
        "bootstrap_proposals_complete",
        org_id=org_id,
        kb_slug=kb_slug,
        clusters_found=final_clusters_found,
        base_clusters_found=base_clusters_count,
        outlier_count=outlier_count,
        cluster_probability_mean=cluster_probability_mean,
        proposals_submitted=submitted,
    )

    return BootstrapResult(
        documents_scanned=doc_count,
        proposals_submitted=submitted,
        clusters_found=final_clusters_found,
        base_clusters_found=base_clusters_count,
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
