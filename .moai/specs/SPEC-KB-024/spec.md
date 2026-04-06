---
id: SPEC-KB-024
version: "1.0"
status: draft
created: "2026-04-06"
updated: "2026-04-06"
author: Mark Vletter
priority: medium
tags: [taxonomy, knowledge-ingest, clustering, qdrant, discovery, auto-categorisation]
related: [SPEC-KB-022, SPEC-KB-023]
---

# SPEC-KB-024: Taxonomy Discovery — Embedding Clustering & Auto-Categorisation

## Context

SPEC-KB-023 stores a `content_label` per document: a blind, taxonomy-independent description. This SPEC uses those labels and the existing dense embeddings (already in Qdrant) to:

1. **Discover new categories** — cluster documents by embedding similarity; clusters without a matching taxonomy node are candidate new categories
2. **Categorise new documents without LLM calls** — once cluster centroids are computed, new documents are assigned to their nearest centroid via cosine similarity (O(k) where k = number of clusters)
3. **Auto-categorise historical documents** — when a new taxonomy node is approved, all documents in its source cluster get the node automatically via centroid-matching, not via a backfill LLM run

The key insight: **LLM is used once per new category (naming), not once per document per category change.**

---

## Scope

**In scope:**
- `klai-knowledge-ingest`: clustering job (Procrastinate periodic task)
- `klai-knowledge-ingest`: centroid-based classification at ingest (replaces or supplements LLM classification for matched clusters)
- `klai-knowledge-ingest`: centroid store (JSON sidecar, refreshed by clustering job)
- `klai-knowledge-ingest`: proposal generation for unmatched clusters (1 LLM call per cluster)
- `klai-knowledge-ingest`: bulk auto-categorise endpoint (for when a new node is approved)
- `klai-portal/backend`: webhook/callback when a taxonomy node is approved → trigger auto-categorise

**Out of scope:**
- Portal UI for cluster inspection (separate SPEC)
- Manual cluster merging or splitting
- Cross-KB clustering (each KB clustered independently)

---

## Requirements

### R1 — Periodic Clustering Job

WHEN the clustering job runs (trigger: every 24h OR after N ≥ 20 new documents ingested into a KB),
THEN for each KB with ≥ 10 documents:
- Fetch dense embeddings for all chunks via Qdrant scroll (with_vectors=True)
- Deduplicate to one embedding per document (use chunk_index=0 or first chunk per artifact_id)
- Run HDBSCAN (min_cluster_size=5) on the embeddings
- Compute centroid (mean embedding) per cluster
- Persist centroids to `~/.klai/taxonomy_centroids/{org_id}_{kb_slug}.json`

HDBSCAN is preferred over k-means because it does not require k to be specified and naturally produces an "unclustered" noise label (-1) for outlier documents.

The clustering job SHALL run as a Procrastinate periodic task on the `taxonomy-backfill` queue.

### R2 — Centroid-Based Classification at Ingest

WHEN a new document is ingested and centroids exist for the KB,
THEN BEFORE the LLM classification (KB-022):
- Compute cosine similarity between the document embedding and each centroid
- If max similarity ≥ 0.85 AND that centroid maps to a known taxonomy node: set `taxonomy_node_ids` directly, skip LLM classification call
- If max similarity ≥ 0.85 AND that centroid does NOT map to a known node: continue to LLM classification (centroid is an unconfirmed cluster)
- If max similarity < 0.85: continue to LLM classification (document may be genuinely novel)

This reduces LLM calls for well-established categories to zero after the first clustering run.

### R3 — New Cluster Proposal Generation

WHEN the clustering job finds a cluster with ≥ 5 documents that does NOT map to any existing taxonomy node,
THEN:
1. Collect the `content_label` keywords for the top 5 documents in the cluster (by proximity to centroid)
2. Make ONE LLM call (`klai-fast`): "Given these document descriptions: {labels}, suggest a taxonomy category name (max 40 chars, Dutch)"
3. Submit a `new_node` proposal to the portal review queue via `submit_taxonomy_proposal()`

A cluster SHALL NOT generate a duplicate proposal if one is already pending review for the same KB.

### R4 — Auto-Categorise on Node Approval

WHEN a taxonomy node is approved in the portal (proposal type `new_node`),
THEN the portal SHALL call `POST /ingest/v1/taxonomy/auto-categorise` with:
```json
{"org_id": "...", "kb_slug": "...", "node_id": <int>, "cluster_centroid": [<float>, ...]}
```

THEN the ingest service SHALL:
- Scroll all chunks for the KB that have `taxonomy_node_ids` not containing `node_id`
- For each document (deduplicated by artifact_id): compute cosine similarity to `cluster_centroid`
- If similarity ≥ 0.82: add `node_id` to `taxonomy_node_ids` via Qdrant set_payload
- No LLM calls during this operation

The centroid is stored in the proposal payload so it is available at approval time.

### R5 — Centroid Store Format

Centroids SHALL be stored as JSON at `~/.klai/taxonomy_centroids/{org_id}_{kb_slug}.json`:

```json
{
  "version": 1,
  "computed_at": "<iso8601>",
  "kb_slug": "voys",
  "org_id": "362757920133283846",
  "clusters": [
    {
      "cluster_id": 0,
      "centroid": [0.123, -0.456, ...],
      "size": 42,
      "taxonomy_node_id": 6,
      "content_label_summary": ["voip", "sip", "telefonie"]
    }
  ]
}
```

`taxonomy_node_id` is null for unconfirmed clusters (no matching node yet).

### R6 — Backfill of Existing Documents (content_label)

WHEN SPEC-KB-023 is deployed, existing chunks lack `content_label`.
THEN the existing `POST /ingest/v1/taxonomy/backfill` endpoint SHALL be extended with a phase 0:
- Scroll chunks where `content_label` is absent (IsEmptyCondition)
- For each document: generate `content_label` (blind, no taxonomy context)
- Store on all chunks of that document

This is idempotent and can be triggered manually after SPEC-KB-023 goes live.

### R7 — Thresholds are Configurable

All similarity thresholds SHALL be configurable via environment variables with defaults:
- `TAXONOMY_CENTROID_MATCH_THRESHOLD=0.85` (skip LLM if centroid matches known node)
- `TAXONOMY_AUTO_CATEGORISE_THRESHOLD=0.82` (threshold for bulk auto-categorise on approval)
- `TAXONOMY_CLUSTER_MIN_SIZE=5` (HDBSCAN min_cluster_size)
- `TAXONOMY_CLUSTER_TRIGGER_COUNT=20` (re-cluster after N new docs)

---

## Technical Design

### Dependencies

Add to `klai-knowledge-ingest` pyproject.toml:
- `hdbscan>=0.8` (or `scikit-learn` for AgglomerativeClustering as fallback — lighter dependency)
- `numpy>=1.26` (already likely present via qdrant-client)

### New files

| File | Purpose |
|------|---------|
| `knowledge_ingest/clustering.py` | HDBSCAN clustering, centroid computation, centroid store read/write |
| `knowledge_ingest/clustering_tasks.py` | Procrastinate periodic task wrapping `clustering.py` |

### Modified files

| File | Change |
|------|--------|
| `knowledge_ingest/routes/ingest.py` | Centroid lookup before LLM classification (R2) |
| `knowledge_ingest/routes/taxonomy.py` | `POST /ingest/v1/taxonomy/auto-categorise` (R4), phase 0 in backfill (R6) |
| `knowledge_ingest/taxonomy_tasks.py` | Phase 0 in backfill Procrastinate task |
| `knowledge_ingest/app.py` | Register clustering periodic task |
| `knowledge_ingest/config.py` | Threshold env vars (R7) |
| `klai-portal/backend/app/api/taxonomy.py` | Trigger auto-categorise on node approval (R4) |

---

## Acceptance Criteria

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | Clustering job runs and produces centroid file | Trigger job manually, check `~/.klai/taxonomy_centroids/` |
| AC2 | New doc matching known centroid (≥0.85) skips LLM call | Ingest doc similar to existing cluster, confirm no LLM call in logs |
| AC3 | Unmatched cluster (≥5 docs) generates 1 proposal in portal | Check portal proposals after clustering run |
| AC4 | Approving a node triggers auto-categorise for matching docs | Approve proposal, verify Qdrant payloads updated, no LLM logs |
| AC5 | Thresholds configurable via env vars | Set `TAXONOMY_CENTROID_MATCH_THRESHOLD=0.99`, verify LLM always called |
| AC6 | Backfill phase 0 populates `content_label` on existing chunks | Run backfill on Voys KB, scroll 5 chunks, verify `content_label` present |
| AC7 | No LLM calls during auto-categorise bulk operation | Check logs during `auto-categorise` call |
| AC8 | Duplicate proposals not generated | Run clustering twice, verify only 1 pending proposal per cluster |

---

## Sequencing

1. Deploy SPEC-KB-023 first (blind labeling at ingest)
2. Trigger backfill phase 0 for existing KBs (populates `content_label`)
3. Deploy SPEC-KB-024
4. Trigger first clustering run manually
5. Review generated proposals in portal, approve relevant ones
6. Verify auto-categorise fires correctly on approval
