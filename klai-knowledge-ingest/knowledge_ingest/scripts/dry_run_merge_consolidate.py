"""Dry-run taxonomy consolidation (Clio-style top-down hierarchy).

SPEC-TAXONOMY-MERGE-DETECT-001 v0.5.

Runs the existing V2 bootstrap pipeline (HDBSCAN + UMAP + batched naming +
description generation) and then asks an LLM to organise the resulting
fine-grained clusters into 5-9 broader parent categories — the count target
follows Miller's Law for IA-friendly browsing structures.

Inspired by Anthropic's Clio paper (arxiv 2412.13678) which uses
"propose higher-level names that encompass these clusters" + "assign each
base cluster to one parent" rather than pairwise merge judging.

Pipeline:
    1. Qdrant scroll + HDBSCAN cluster + batched naming + base descriptions
    2. Group-and-assign LLM call: 15 base clusters → 5-9 parents with
       balance constraints (no parent > 25% docs / 33% clusters)
    3. Per-parent description generation via generate_node_description
       (same path used by production new_node proposals).

Two modes:
    live     Run full pipeline. Writes a cache file with cluster info.
    replay   Skip Qdrant + naming + describing, replay group + assign on a
             cached cluster state. Faster iteration on the prompts.

Usage (live, on core-01 because Qdrant + LiteLLM are docker-internal):
    ssh core-01 "docker exec klai-core-knowledge-ingest-1 python -m \\
        knowledge_ingest.scripts.dry_run_merge_consolidate \\
        --org-id <zitadel_org_id> --kb-slug support"

Usage (replay):
    python -m knowledge_ingest.scripts.dry_run_merge_consolidate \\
        --mode replay --from /tmp/merge-consolidate-support-...json \\
        --target-min 5 --target-max 9
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from knowledge_ingest.clustering import (
    closest_to_centroid,
    cluster_documents_hdbscan,
    compute_min_cluster_size,
)
from knowledge_ingest.config import settings
from knowledge_ingest.description_generator import generate_node_description
from knowledge_ingest.portal_client import (
    fetch_kb_metadata,
    fetch_taxonomy_nodes,
)
from knowledge_ingest.proposal_generator import (
    _NAMING_CRITERIA,
    DocumentSummary,
    _suggest_cluster_names_batched,
)
from knowledge_ingest.routes.taxonomy import COLLECTION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("dry_run_merge_consolidate")


# ---------------------------------------------------------------------------
# Group-and-assign prompt — Clio-style top-down hierarchy with percentage-
# based balance caps that scale with the actual corpus.
# ---------------------------------------------------------------------------

_GROUP_AND_ASSIGN_SYSTEM_PROMPT_TEMPLATE = (
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


# ---------------------------------------------------------------------------
# Data classes.
# ---------------------------------------------------------------------------


@dataclass
class ClusterInfo:
    """One named cluster with description (the way the LLM needs to see it)."""

    cluster_id: int
    name: str
    description: str
    doc_count: int
    sample_titles: list[str]
    centroid: list[float]  # unit-normalised; kept for potential future use


@dataclass
class CacheBundle:
    """What we serialise to disk after a live run."""

    kb_slug: str
    org_id: str
    kb_description: str
    documents_scanned: int
    outlier_count: int
    clusters: list[ClusterInfo]


@dataclass
class ParentCategory:
    """One LLM-proposed parent category."""

    name: str
    rationale: str
    child_cluster_ids: list[int] = field(default_factory=list)
    # Description is generated AFTER group-and-assign via generate_node_description
    # — same path that production new_node proposals use, so the integration is
    # consistent with current node descriptions.
    description: str = ""


# ---------------------------------------------------------------------------
# Live mode — repeats taxonomy_bootstrap_proposals up to (and including)
# naming + description, but does NOT call submit_taxonomy_proposal.
# Returns a CacheBundle.
# ---------------------------------------------------------------------------


async def _scroll_kb_embeddings(
    org_id: str, kb_slug: str
) -> tuple[list[DocumentSummary], np.ndarray]:
    """Mirror the scroll loop from taxonomy_bootstrap_proposals."""
    qdrant_client = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )

    scroll_filter = Filter(
        must=[
            FieldCondition(key="org_id", match=MatchValue(value=org_id)),
            FieldCondition(key="kb_slug", match=MatchValue(value=kb_slug)),
        ]
    )

    seen_artifacts: set[str] = set()
    doc_summaries: list[DocumentSummary] = []
    doc_vecs: list[list[float]] = []
    offset: Any = None

    while True:
        points, next_offset = await asyncio.wait_for(
            qdrant_client.scroll(
                collection_name=COLLECTION,
                scroll_filter=scroll_filter,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=["vector_chunk"],
            ),
            timeout=60.0,
        )
        if not points:
            break

        for point in points:
            payload = point.payload or {}
            artifact_id = payload.get("artifact_id") or str(point.id)
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)

            vec = None
            if hasattr(point, "vector") and point.vector:
                if isinstance(point.vector, dict):
                    vec = point.vector.get("vector_chunk")
                elif isinstance(point.vector, list):
                    vec = point.vector
            if vec is None:
                continue

            title = payload.get("title") or payload.get("path") or artifact_id
            preview = payload.get("text", "")[:300]
            doc_summaries.append(DocumentSummary(title=title, content_preview=preview))
            doc_vecs.append(vec)

        if next_offset is None:
            break
        offset = next_offset

    if not doc_vecs:
        return doc_summaries, np.empty((0, 0), dtype=np.float32)

    embeddings = np.array(doc_vecs, dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    embeddings = embeddings / norms
    return doc_summaries, embeddings


async def _build_cache_live(org_id: str, kb_slug: str) -> CacheBundle:
    """Run the full bootstrap pipeline up to naming+describing. No DB writes."""
    logger.info("Scrolling Qdrant for org=%s kb=%s …", org_id, kb_slug)
    doc_summaries, embeddings = await _scroll_kb_embeddings(org_id, kb_slug)
    if len(doc_summaries) == 0:
        logger.warning("No documents found in Qdrant for this KB.")
        return CacheBundle(
            kb_slug=kb_slug,
            org_id=org_id,
            kb_description="",
            documents_scanned=0,
            outlier_count=0,
            clusters=[],
        )

    logger.info("Got %d documents. Fetching KB metadata + existing nodes …", len(doc_summaries))
    kb_meta = await fetch_kb_metadata(kb_slug, org_id)
    kb_description = (kb_meta or {}).get("description") or ""
    existing_nodes = await fetch_taxonomy_nodes(kb_slug, org_id)

    logger.info("Running HDBSCAN (UMAP-pre-reduced) …")
    min_cluster_size = compute_min_cluster_size(
        len(doc_summaries),
        floor=settings.taxonomy_bootstrap_min_cluster_size_floor,
    )
    labels, metrics = cluster_documents_hdbscan(
        embeddings,
        min_cluster_size=min_cluster_size,
        pre_reduce=True,
    )
    clusters_found: int = metrics["clusters_found"]
    outlier_count: int = metrics["outlier_count"]
    logger.info("Found %d clusters (%d outliers).", clusters_found, outlier_count)

    if clusters_found == 0:
        return CacheBundle(
            kb_slug=kb_slug,
            org_id=org_id,
            kb_description=kb_description,
            documents_scanned=len(doc_summaries),
            outlier_count=outlier_count,
            clusters=[],
        )

    cluster_map: dict[int, list[int]] = {}
    for idx, lbl in enumerate(labels):
        if int(lbl) >= 0:
            cluster_map.setdefault(int(lbl), []).append(idx)

    max_clusters = settings.taxonomy_bootstrap_max_clusters
    if len(cluster_map) > max_clusters:
        sorted_clusters = sorted(cluster_map.items(), key=lambda x: len(x[1]), reverse=True)
        cluster_map = dict(sorted_clusters[:max_clusters])
        logger.info("Capped to %d largest clusters.", max_clusters)

    top_n = settings.taxonomy_bootstrap_top_n_per_cluster

    cluster_doc_lists: dict[int, list[DocumentSummary]] = {}
    for cid, indices in cluster_map.items():
        top_indices = closest_to_centroid(indices, embeddings, n=top_n)
        cluster_docs = [
            doc_summaries[i]
            for i in top_indices
            if len(doc_summaries[i].content_preview.strip()) >= 50
        ]
        if not cluster_docs:
            cluster_docs = [doc_summaries[i] for i in top_indices]
        cluster_doc_lists[cid] = cluster_docs

    logger.info("Running batched LLM-naming over %d clusters …", len(cluster_doc_lists))
    batched_names = await _suggest_cluster_names_batched(cluster_doc_lists, kb_description)
    existing_names_lower = {node.name.lower() for node in existing_nodes}

    kept_clusters: list[tuple[int, str, list[int]]] = []
    for cid in sorted(cluster_doc_lists):
        name = batched_names.get(cid)
        if not name:
            logger.info("Skipping cluster %d: batched-naming returned no name.", cid)
            continue
        if name.lower() in existing_names_lower:
            logger.info("Skipping cluster %d (%r): duplicate of existing node.", cid, name)
            continue
        kept_clusters.append((cid, name, cluster_map[cid]))

    if not kept_clusters:
        return CacheBundle(
            kb_slug=kb_slug,
            org_id=org_id,
            kb_description=kb_description,
            documents_scanned=len(doc_summaries),
            outlier_count=outlier_count,
            clusters=[],
        )

    logger.info("Generating descriptions for %d clusters in parallel …", len(kept_clusters))
    desc_tasks = [
        generate_node_description(
            name,
            None,
            [doc.title for doc in cluster_doc_lists[cid][:5]],
        )
        for cid, name, _ in kept_clusters
    ]
    descriptions = await asyncio.gather(*desc_tasks, return_exceptions=True)

    clusters: list[ClusterInfo] = []
    for (cid, name, indices), desc in zip(kept_clusters, descriptions, strict=True):
        if isinstance(desc, str):
            description = desc
        else:
            description = ""
            logger.warning("Description failed for cluster %d: %s", cid, desc)
        centroid = embeddings[indices].mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        if norm == 0:
            logger.warning("Cluster %d has zero-norm centroid — skipping.", cid)
            continue
        centroid_unit = (centroid / norm).tolist()
        clusters.append(
            ClusterInfo(
                cluster_id=cid,
                name=name,
                description=description,
                doc_count=len(indices),
                sample_titles=[doc.title for doc in cluster_doc_lists[cid][:8]],
                centroid=centroid_unit,
            )
        )

    return CacheBundle(
        kb_slug=kb_slug,
        org_id=org_id,
        kb_description=kb_description,
        documents_scanned=len(doc_summaries),
        outlier_count=outlier_count,
        clusters=clusters,
    )


# ---------------------------------------------------------------------------
# Group-and-assign LLM call.
# ---------------------------------------------------------------------------


async def _group_and_assign(
    bundle: CacheBundle,
    target_min: int,
    target_max: int,
    print_prompt: bool = False,
) -> list[ParentCategory]:
    """One LLM call: propose parents + assign each base cluster.

    Returns a list of ParentCategory. May raise on judge failure; caller
    handles by reporting and exiting.
    """
    if not bundle.clusters:
        return []

    kb_description_block = ""
    if bundle.kb_description and bundle.kb_description.strip():
        kb_description_block = (
            f"\n\nThe knowledge base is described as:\n{bundle.kb_description.strip()}"
        )

    total_docs = sum(c.doc_count for c in bundle.clusters)
    n_clusters = len(bundle.clusters)
    # Soft caps: 25% of docs, 33% of clusters; hard cap: target_max + 2
    doc_cap = max(1, total_docs // 4)
    cluster_cap = max(1, n_clusters // 3)
    hard_cap = target_max + 2
    system_prompt = _GROUP_AND_ASSIGN_SYSTEM_PROMPT_TEMPLATE.format(
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
    for c in bundle.clusters:
        title_lines = "\n".join(f"      - {t[:140]}" for t in c.sample_titles[:5])
        descr = c.description or "(no description)"
        cluster_lines.append(
            f"Cluster {c.cluster_id} \"{c.name}\" ({c.doc_count} docs):\n"
            f"  Description: {descr}\n"
            f"  Sample titles:\n{title_lines}"
        )
    user_message = "\n\n".join(cluster_lines)

    if print_prompt:
        print("--- SYSTEM PROMPT ---", file=sys.stderr)
        print(system_prompt, file=sys.stderr)
        print("--- USER MESSAGE ---", file=sys.stderr)
        print(user_message, file=sys.stderr)
        print("---------------------", file=sys.stderr)

    max_tokens = 600 + 80 * len(bundle.clusters)
    logger.info(
        "Calling LLM group-and-assign: %d clusters → %d-%d parents, max_tokens=%d",
        len(bundle.clusters),
        target_min,
        target_max,
        max_tokens,
    )
    t0 = time.monotonic()
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
    latency = time.monotonic() - t0
    logger.info("Group-and-assign call returned in %.2fs", latency)

    content = (data["choices"][0]["message"]["content"] or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict) or "parents" not in parsed:
        raise ValueError(f"Group response missing 'parents' key. Got: {content[:200]}")

    parents_list = parsed["parents"]
    if not isinstance(parents_list, list):
        raise ValueError("'parents' is not a list")

    valid_cids = {c.cluster_id for c in bundle.clusters}
    parents: list[ParentCategory] = []
    seen_cids: set[int] = set()
    for item in parents_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            logger.warning("Dropping parent without name: %r", item)
            continue
        rationale = item.get("rationale") or ""
        if not isinstance(rationale, str):
            rationale = str(rationale)
        children_raw = item.get("child_cluster_ids", [])
        if not isinstance(children_raw, list):
            logger.warning("Parent %r has invalid child_cluster_ids: %r", name, children_raw)
            children_raw = []
        children: list[int] = []
        for cid in children_raw:
            if not isinstance(cid, int):
                continue
            if cid not in valid_cids:
                logger.warning("Parent %r references unknown cluster %d", name, cid)
                continue
            if cid in seen_cids:
                logger.warning("Cluster %d already assigned to another parent — skipping", cid)
                continue
            seen_cids.add(cid)
            children.append(cid)
        parents.append(
            ParentCategory(name=name.strip(), rationale=rationale.strip(), child_cluster_ids=children)
        )

    unassigned = [cid for cid in valid_cids if cid not in seen_cids]
    if unassigned:
        logger.warning("LLM forgot to assign %d clusters — collecting as 'Overig'", len(unassigned))
        parents.append(
            ParentCategory(
                name="Overig (niet door LLM toegewezen)",
                rationale="Deze clusters werden door de LLM niet aan een parent toegewezen.",
                child_cluster_ids=unassigned,
            )
        )

    return parents


async def _generate_parent_descriptions(
    bundle: CacheBundle, parents: list[ParentCategory]
) -> None:
    """Populate parent.description in-place via generate_node_description.

    Uses the SAME path as production new_node proposals so the integration
    will be consistent with existing node descriptions.

    For each parent: 2 titles per child (round-robin), capped at 10 total.
    generate_node_description internally caps at 10 anyway.
    """
    logger.info("Generating descriptions for %d parents in parallel …", len(parents))

    def _titles_for_parent(p: ParentCategory) -> list[str]:
        per_child = max(2, 10 // max(1, len(p.child_cluster_ids)))
        titles: list[str] = []
        for cid in p.child_cluster_ids:
            cluster = next((c for c in bundle.clusters if c.cluster_id == cid), None)
            if cluster is None:
                continue
            titles.extend(cluster.sample_titles[:per_child])
            if len(titles) >= 10:
                break
        return titles[:10]

    desc_tasks = [
        generate_node_description(p.name, None, _titles_for_parent(p)) for p in parents
    ]
    descriptions = await asyncio.gather(*desc_tasks, return_exceptions=True)
    for p, desc in zip(parents, descriptions, strict=True):
        if isinstance(desc, str):
            p.description = desc
        else:
            logger.warning("Parent description failed for %r: %s", p.name, desc)
            p.description = ""


# ---------------------------------------------------------------------------
# Pretty-print rapport.
# ---------------------------------------------------------------------------


def _print_rapport(
    bundle: CacheBundle,
    parents: list[ParentCategory],
    target_min: int,
    target_max: int,
    cache_path: Path | None,
) -> None:
    print()
    print("=== Bootstrap output ===")
    print(f"KB:           {bundle.kb_slug} (org={bundle.org_id})")
    print(f"Documents:    {bundle.documents_scanned}")
    print(f"Outliers:     {bundle.outlier_count}")
    print(f"Base clusters: {len(bundle.clusters)} (post-dedup)")
    if bundle.kb_description:
        print(f"KB description: {bundle.kb_description[:140]}{'…' if len(bundle.kb_description) > 140 else ''}")
    print()
    for c in bundle.clusters:
        descr = c.description or "(no description)"
        descr_short = descr[:120] + "…" if len(descr) > 120 else descr
        print(f"  [#{c.cluster_id:>2}] {c.name:<55s} ({c.doc_count} docs)")
        print(f"          {descr_short}")

    print()
    if not parents:
        print("=== Group-and-assign ===")
        print("  (no clusters to consolidate)")
        return

    print("=== Voorgestelde parent-categorieën ===")
    print(f"Doel: {target_min}-{target_max} parents   Resultaat: {len(parents)} parents")
    print()
    for i, p in enumerate(parents, start=1):
        children_str = ", ".join(f"#{cid}" for cid in p.child_cluster_ids)
        total_docs = sum(_doccount_of(bundle, cid) for cid in p.child_cluster_ids)
        print(f"  {i}. {p.name:<55s} ({len(p.child_cluster_ids)} clusters, {total_docs} docs)")
        if p.description:
            print(f"        Description: {p.description}")
        if p.rationale:
            print(f"        Rationale (debug): {p.rationale}")
        print(f"        Children: {children_str}")
        for cid in p.child_cluster_ids:
            child_name = _name_of(bundle, cid)
            print(f"          [#{cid:>2}] {child_name}")
        print()

    print("=== Samenvatting ===")
    print(f"  {len(bundle.clusters)} → {len(parents)} categorieën")
    if len(parents) < target_min:
        print(f"  ! Onder doel-min ({target_min}). Mogelijk te grof gegroepeerd.")
    elif len(parents) > target_max:
        print(f"  ! Boven doel-max ({target_max}). Mogelijk te fijn gegroepeerd.")
    else:
        print(f"  OK: binnen doel-range {target_min}-{target_max}")

    if cache_path is not None:
        print()
        print(f"Cache written: {cache_path}")
        print(f"Replay: python -m knowledge_ingest.scripts.dry_run_merge_consolidate \\")
        print(f"          --mode replay --from {cache_path}")


def _name_of(bundle: CacheBundle, cluster_id: int) -> str:
    for c in bundle.clusters:
        if c.cluster_id == cluster_id:
            return c.name
    return f"cluster#{cluster_id}"


def _doccount_of(bundle: CacheBundle, cluster_id: int) -> int:
    for c in bundle.clusters:
        if c.cluster_id == cluster_id:
            return c.doc_count
    return 0


# ---------------------------------------------------------------------------
# Cache I/O.
# ---------------------------------------------------------------------------


def _serialise_bundle(bundle: CacheBundle) -> dict:
    return {
        "kb_slug": bundle.kb_slug,
        "org_id": bundle.org_id,
        "kb_description": bundle.kb_description,
        "documents_scanned": bundle.documents_scanned,
        "outlier_count": bundle.outlier_count,
        "clusters": [asdict(c) for c in bundle.clusters],
    }


def _deserialise_bundle(data: dict) -> CacheBundle:
    return CacheBundle(
        kb_slug=data["kb_slug"],
        org_id=data["org_id"],
        kb_description=data.get("kb_description", ""),
        documents_scanned=data.get("documents_scanned", 0),
        outlier_count=data.get("outlier_count", 0),
        clusters=[
            ClusterInfo(
                cluster_id=c["cluster_id"],
                name=c["name"],
                description=c.get("description", ""),
                doc_count=c["doc_count"],
                sample_titles=c["sample_titles"],
                centroid=c["centroid"],
            )
            for c in data["clusters"]
        ],
    )


def _default_cache_path(kb_slug: str) -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    return Path("/tmp") / f"merge-consolidate-{kb_slug}-{ts}.json"


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


async def amain(args: argparse.Namespace) -> int:
    if args.target_min < 1 or args.target_max < args.target_min:
        print("Invalid --target-min / --target-max range", file=sys.stderr)
        return 1

    if args.mode == "live":
        if not args.org_id or not args.kb_slug:
            print("--mode live requires --org-id and --kb-slug", file=sys.stderr)
            return 1
        if not settings.litellm_api_key:
            print("LITELLM_API_KEY is not set — calls will fail. Aborting.", file=sys.stderr)
            return 1
        bundle = await _build_cache_live(args.org_id, args.kb_slug)
        cache_path = Path(args.cache) if args.cache else _default_cache_path(args.kb_slug)
        cache_path.write_text(json.dumps(_serialise_bundle(bundle), indent=2))
    else:
        if not args.from_path:
            print("--mode replay requires --from <cache.json>", file=sys.stderr)
            return 1
        cache_path = Path(args.from_path)
        if not cache_path.exists():
            print(f"Cache file not found: {cache_path}", file=sys.stderr)
            return 1
        bundle = _deserialise_bundle(json.loads(cache_path.read_text()))
        cache_path = None

    if not bundle.clusters:
        _print_rapport(bundle, [], args.target_min, args.target_max, cache_path)
        return 0

    try:
        parents = await _group_and_assign(
            bundle,
            target_min=args.target_min,
            target_max=args.target_max,
            print_prompt=args.print_prompt,
        )
    except Exception as exc:
        logger.exception("Group-and-assign call failed: %s", exc)
        return 2

    if parents:
        await _generate_parent_descriptions(bundle, parents)

    _print_rapport(bundle, parents, args.target_min, args.target_max, cache_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run taxonomy consolidation (Clio-style top-down hierarchy).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--mode", choices=["live", "replay"], default="live")
    parser.add_argument("--org-id", help="Zitadel org ID (live mode only)")
    parser.add_argument("--kb-slug", help="KB slug (live mode only)")
    parser.add_argument(
        "--from",
        dest="from_path",
        help="Cache file to replay (replay mode only)",
    )
    parser.add_argument(
        "--cache",
        help="Path to write cache file (default: /tmp/merge-consolidate-<kb>-<ts>.json)",
    )
    parser.add_argument(
        "--target-min",
        type=int,
        default=5,
        help="Minimum desired number of parent categories (default: 5)",
    )
    parser.add_argument(
        "--target-max",
        type=int,
        default=9,
        help="Maximum desired number of parent categories (default: 9)",
    )
    parser.add_argument(
        "--print-prompt",
        action="store_true",
        help="Print the full LLM prompt to stderr (debug)",
    )
    args = parser.parse_args()
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
