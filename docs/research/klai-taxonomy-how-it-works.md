# Klai Taxonomy & Tagging: How It Actually Works

> Written: 2026-04-07
> Updated: 2026-04-07 — SPEC-KB-027 gaps added
> Based on: SPEC-KB-021 through SPEC-KB-027 + code trace
> Scope: The complete categorisation and tagging pipeline as implemented + known gaps

---

## Overview

Klai's taxonomy system classifies knowledge base chunks into editorial categories and tags them with free-form keywords. It is a **per-KB, user-governed system** with two parallel classification signals and a self-improving proposal loop.

The system spans three services:
- **portal-api** — taxonomy node/proposal CRUD, gap classification, coverage dashboard
- **knowledge-ingest** — LLM classification at ingest, backfill, HDBSCAN clustering, auto-categorise
- **Qdrant** — stores `taxonomy_node_ids`, `tags`, `content_label` as chunk payload fields

---

## Data model

### PostgreSQL (portal-api)

**`portal_taxonomy_nodes`** — the taxonomy itself, per KB.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `kb_id` | FK → knowledge_bases | per-KB isolation |
| `parent_id` | FK → self (nullable) | max 2 levels in practice (no enforcement) |
| `name` | varchar(128) | unique among siblings |
| `slug` | varchar(128) | URL-safe, auto-generated from name |
| `description` | text (nullable) | used by classifier as context |
| `doc_count` | int | maintained manually (not live count) |
| `sort_order` | int | |
| `created_by` | varchar(64) | user_id |

Uniqueness is enforced by two partial indexes: one for root nodes (parent_id IS NULL), one for siblings (parent_id IS NOT NULL).

**`portal_taxonomy_proposals`** — AI-generated or user-submitted change proposals.

| Column | Type | Notes |
|---|---|---|
| `proposal_type` | enum | `new_node | merge | split | rename | tag` |
| `status` | enum | `pending | approved | rejected` |
| `payload` | JSONB | type-specific data |
| `confidence_score` | float (nullable) | LLM confidence |

The `payload` for a `new_node` proposal from clustering includes: `suggested_name`, `document_count`, `sample_titles`, `description`, `cluster_centroid` (the HDBSCAN centroid vector).

### Qdrant (per chunk payload)

Three fields are written to every chunk:

| Field | Type | Set by |
|---|---|---|
| `taxonomy_node_ids` | `list[int]` | LLM classifier or centroid lookup at ingest / backfill |
| `tags` | `list[str]` | Frontmatter + LLM suggestions, merged at ingest |
| `content_label` | `list[str]` | Blind keyword extractor (3-5 words, no taxonomy context) |

`taxonomy_node_ids` is multi-label (a chunk can belong to multiple nodes). `tags` are free-form lowercase keywords. `content_label` is the raw content fingerprint, used as HDBSCAN clustering input.

---

## Pipeline 1: Classification at ingest time

This runs synchronously inside every `POST /ingest/v1/ingest` call, before the enrichment Procrastinate job is enqueued.

### Step 1 — Blind label generation (`content_labeler.py`)

Called **before** any taxonomy context is fetched. Prevents confirmation bias.

```
title + content[:500]
  → klai-fast (LLM, temp=0, max_tokens=100, json_object)
  → {"keywords": ["keyword1", "keyword2", ...]}
  → 3-5 lowercase keywords stored as content_label
```

The `content_label` is the raw, taxonomy-agnostic description of the document. It is used later by HDBSCAN clustering to identify semantic groups.

Non-fatal: returns `[]` on timeout (15s) or failure. Ingest continues.

### Step 2 — Taxonomy node fetch (`portal_client.py`)

```
fetch_taxonomy_nodes(kb_slug, org_id)
  → GET /api/app/knowledge-bases/{kb_slug}/taxonomy/nodes/internal?zitadel_org_id=...
  → portal-api (RLS-scoped query on portal_taxonomy_nodes)
  → list[TaxonomyNode(id, name, description)]
```

Result is **cached in-memory for 5 minutes** per (org_id, kb_slug). If no nodes exist, classification is skipped entirely (has_taxonomy = False).

### Step 3 — Centroid lookup (fast path, `clustering.py`)

If a centroid store exists for this KB (JSON sidecar at `~/.klai/taxonomy_centroids/{org_id}_{kb_slug}.json`) and is not stale (configurable max age, default 24h):

```
doc content[:512] → embed (dense vector)
  → cosine similarity against all cluster centroids
  → if best_sim >= taxonomy_centroid_match_threshold AND centroid has taxonomy_node_id
  → return [taxonomy_node_id] (skip LLM call)
```

The centroid path is the **fast path**: no LLM call, O(k) similarity check where k = number of clusters.

### Step 4 — LLM classification (slow path, `taxonomy_classifier.py`)

If centroid lookup misses (no store, stale, or no match above threshold):

```
title + content[:500] + taxonomy_node_ids formatted list (name + description)
  → klai-fast (temp=0, max_tokens=300, json_object, rate-limited to 1 req/s)
  → {"nodes": [{"node_id": int, "confidence": float}], "tags": [str], "reasoning": str}
  → matched_nodes: list[(node_id, confidence)] where confidence >= 0.5, max 5
  → llm_tags: list[str], max 5
```

Model is `settings.taxonomy_classification_model` (defaults to `klai-fast`).

### Step 5 — Tag merge

Frontmatter tags (from YAML frontmatter in the document) take priority. LLM tags are appended, deduped:

```python
merged_tags = [*frontmatter_tags, *llm_tags]  # deduped, lowercase
```

### Step 6 — Write to Qdrant

All three fields go into `extra_payload` which is passed through to every chunk:

```python
extra_payload["taxonomy_node_ids"] = taxonomy_node_ids   # list[int]
extra_payload["tags"]              = merged_tags          # list[str]
extra_payload["content_label"]     = content_label        # list[str]
```

**Critical:** these fields must be in `extra_payload` (not just local variables) or they are silently dropped by the enrichment pipeline passthrough.

### Step 7 — Self-bootstrapping proposal (dead code — fixed in SPEC-KB-027)

If the KB has taxonomy nodes but the document was **not classified** (taxonomy_node_ids is empty), the ingest route calls `maybe_generate_proposal` with a single-document list. However, the function has a `_MIN_UNMATCHED_FOR_PROPOSAL = 3` threshold — a list of 1 document always fails this check and the function returns immediately. **This code path never submits a proposal.**

The fix (SPEC-KB-027 R2): remove the per-document call from the ingest route. Instead, the backfill job (`_run_backfill` Phase 2) accumulates all unmatched documents across the batch and calls `maybe_generate_proposal` once at the end with the full list. Proposals are generated only when a batch of real documents genuinely fails classification.

---

## Pipeline 2: Backfill (4-phase background job)

Triggered by a user action in the portal ("Re-tag" button → `POST /api/app/.../taxonomy/backfill-trigger`). Enqueues a Procrastinate job in the `taxonomy-backfill` queue.

### Phase 0 — Blind label generation

Scrolls all Qdrant chunks for this KB with `IsEmptyCondition(content_label)`. Groups by `artifact_id` (one LLM call per document, not per chunk). Writes `content_label` via `client.set_payload()`.

### Phase 1 — Schema migration

Migrates old `taxonomy_node_id` (singular, SPEC-KB-021) to `taxonomy_node_ids` (plural list, SPEC-KB-022). Scrolls chunks with `taxonomy_node_id` set but `taxonomy_node_ids` absent. Writes `[old_id]` → `taxonomy_node_ids`.

### Phase 2 — Re-classify unclassified chunks

Scrolls chunks with neither `taxonomy_node_id` nor `taxonomy_node_ids`. Same grouping by `artifact_id`, same LLM classification call as at ingest time.

### Phase 3 — Generate tags for classified-but-untagged chunks

Scrolls chunks with `taxonomy_node_ids` set but `tags` absent. Runs LLM classification again (only tag output used).

**Deduplication:** the Procrastinate `queueing_lock` pattern ensures at most one pending/running backfill per (org_id, kb_slug). A second trigger returns the existing job_id.

---

## Taxonomy governance: proposals

### Proposal lifecycle

```
new_node proposal
  → status=pending (visible in portal review queue)
  → contributor approves
    → PortalTaxonomyNode created in PostgreSQL
    → auto-categorise triggered (if cluster_centroid in payload)
  → contributor rejects
    → status=rejected, rejection_reason stored
```

### Proposal sources

1. **Self-bootstrapping** — ingest creates `new_node` proposals when unmatched documents accumulate
2. **Bootstrap endpoint** — `POST /taxonomy/bootstrap` scans up to 50 existing chunks, asks klai-fast for 3-8 category names, submits one proposal per category. Use this to cold-start a taxonomy from scratch.
3. **Manual** — users create nodes directly via `POST /taxonomy/nodes` (no proposal needed, instant)

### Auto-categorise (on proposal approval)

When a `new_node` proposal with a `cluster_centroid` is approved:

```
approve_proposal()
  → PortalTaxonomyNode created
  → enqueue_auto_categorise(org_id, kb_slug, node_id, cluster_centroid)
    → POST /ingest/v1/taxonomy/auto-categorise-job (Procrastinate)
      → _auto_categorise_impl():
          Pass 1: scroll all chunks, compute cosine_similarity(vec, centroid)
                  collect matched artifact_ids (sim >= threshold)
          Pass 2: scroll all chunks of matched artifact_ids
                  set_payload(taxonomy_node_ids = [*current, node_id])
```

This is **pure cosine similarity** — no LLM calls. All chunks belonging to documents that match the centroid get tagged with the new node_id, including all chunks of a document (not just the representative chunk).

---

## HDBSCAN clustering (taxonomy discovery)

Runs separately from ingest. Used to discover emergent clusters in the embedding space.

```
run_clustering_for_kb(org_id, kb_slug, qdrant_client, taxonomy_nodes)
  → scroll all chunks, deduplicate to one embedding per artifact_id
  → requires >= 10 documents
  → HDBSCAN(min_cluster_size=settings.taxonomy_cluster_min_size, metric="cosine")
  → for each cluster:
      centroid = mean(cluster_embeddings)
      content_label_summary = top-5 unique keywords from cluster docs
      taxonomy_node_id = carried from previous store if centroid sim > 0.95
  → save to ~/.klai/taxonomy_centroids/{org_id}_{kb_slug}.json
```

The centroid store is versioned and has a configurable max age (default 24h, rejected as stale after that). Previous `taxonomy_node_id` assignments carry over when a new cluster's centroid matches a previous centroid with > 0.95 cosine similarity (stable cluster).

---

## Gap classification

Every failed retrieval (hard gap: 0 chunks, soft gap: top score < 0.4) creates a `PortalRetrievalGap` record.

After the gap is stored, an async task calls the knowledge-ingest classify endpoint:

```
_classify_gap(gap_id, org_zitadel_id, query_text, kb_slug)
  → POST /ingest/v1/taxonomy/classify {org_id, kb_slug, text=query_text}
    → fetch_taxonomy_nodes() → classify_document()
    → returns taxonomy_node_ids
  → UPDATE portal_retrieval_gaps SET taxonomy_node_ids = [...] WHERE id = gap_id
```

This is best-effort (fire-and-forget task, non-fatal on failure).

---

## Coverage dashboard

The coverage dashboard (`GET /taxonomy/{kb_slug}/coverage`) combines:

1. **Chunk counts per taxonomy node** — from Qdrant via knowledge-ingest `/ingest/v1/taxonomy/coverage-stats`
2. **Gap counts per taxonomy node** — from PostgreSQL: `COUNT(gaps) WHERE taxonomy_node_ids @> [node_id] AND last 30 days AND unresolved`

Together they show which nodes have good coverage (many chunks, few gaps) vs. which are thin (few chunks, many gaps). Cached for 5 minutes.

---

## Tag visibility

Tags are queryable via `/ingest/v1/taxonomy/top-tags`, which scrolls up to 2,000 Qdrant chunks and counts tag frequency. Can be scoped to a specific `taxonomy_node_id`. Used to populate the tag cloud in the portal.

---

## Full flow diagram

```
Document ingest
│
├─ generate_content_label()          klai-fast, blind (no taxonomy context)
│   → content_label: ["keyword1", ...]
│
├─ fetch_taxonomy_nodes()            portal-api internal endpoint (5-min cache)
│   → taxonomy nodes with id, name, description
│   └─ if no nodes: skip classification
│
├─ [fast path] classify_by_centroid()
│   → cosine_similarity(doc_embedding, centroids)
│   → if match: taxonomy_node_ids = [node_id]  (no LLM)
│
├─ [slow path] classify_document()   klai-fast, multi-label
│   → taxonomy_node_ids: [id, id]
│   → llm_tags: ["tag1", "tag2"]
│
├─ merge_tags(frontmatter_tags, llm_tags)
│   → merged_tags: ["tag1", ...]  (frontmatter priority)
│
├─ upsert_chunks(Qdrant)
│   payload: {taxonomy_node_ids, tags, content_label, ...}
│
└─ [if unmatched] ⚠️ dead code — never fires (threshold=3, always 1 doc passed)
    See SPEC-KB-027 R2 for fix (move to backfill batch accumulation)

User approves proposal
│
├─ PortalTaxonomyNode created (PostgreSQL)
└─ auto_categorise_job (Procrastinate)
    → cosine_similarity(all_chunks, centroid) > threshold
    → set_payload(taxonomy_node_ids = [*existing, new_id])

Failed retrieval (gap event)
│
└─ classify_gap (async task)
    → /ingest/v1/taxonomy/classify
    → UPDATE portal_retrieval_gaps SET taxonomy_node_ids
```

---

## Known gaps and roadmap

### Addressed in SPEC-KB-027

| Gap | Fix |
|---|---|
| **Retrieval filter nooit actief** — `taxonomy_node_ids` zit in Qdrant en retrieval-api heeft de filter al (`search.py:182`), maar research-api stuurt nooit node IDs mee | R1: research-api classificeert query voor retrieval, stuurt node IDs mee als coverage ≥ 30% |
| **`maybe_generate_proposal` is dead code** — altijd 1 doc meegestuurd, drempel is 3, functie returnt altijd direct | R2: call verwijderd uit ingest route, backfill accumuleert unmatched docs en triggert batch-gewijs |
| **`doc_count` denormalisatie** — handmatig bijgehouden bij node-delete, maar nooit bij re-ingest/backfill/connector cleanup; structureel onjuist | R3: kolom verwijderd, coverage dashboard haalt counts altijd live uit Qdrant |

### Taxonomy-aware retrieval flow (na SPEC-KB-027 R1)

```
Chat query
│
├─ [parallel, max 3s]
│   ├─ GET /ingest/v1/taxonomy/coverage-stats
│   │   → coverage = (total - untagged) / total
│   └─ POST /ingest/v1/taxonomy/classify {text=query}
│       → taxonomy_node_ids: [5, 7]
│
├─ if coverage >= 0.30 AND node_ids not empty:
│   → retrieval request met taxonomy_node_ids=[5, 7]  ← filter actief
└─ else:
    → retrieval request zonder filter  ← huidig gedrag
```

### Nog niet opgepakt

| Feature | Status |
|---|---|
| Browse interface (navigate-by-topic) | Niet geïmplementeerd |
| Cross-KB taxonomy coherentie | Niet geïmplementeerd — elk KB heeft eigen geïsoleerde node set |
| Synonym mapping / alias handling | Niet geïmplementeerd |
| Tag-gebaseerde retrieval filter | Niet geïmplementeerd — tags opgeslagen maar niet als filter gebruikt |
| Centroid store in PostgreSQL/Redis | Huidig: JSON sidecar op disk van ingest container |
| Automatic taxonomy evolution (split/merge signalen) | Gedeeltelijk — proposals kunnen worden ingediend, geen automatische signaalberekening |

The coverage dashboard and gap classification per taxonomy node are implemented. The editorial "prioritised writing agenda" (gaps grouped by node) exists but requires the coverage dashboard UI to surface it.

---

## Configuration reference

Relevant settings in `knowledge-ingest/knowledge_ingest/config.py`:

| Setting | Default | Purpose |
|---|---|---|
| `taxonomy_classification_model` | `klai-fast` | Model for LLM classification |
| `taxonomy_classification_timeout` | `30s` | Timeout for classification call |
| `content_label_timeout` | `15s` | Timeout for blind label generation |
| `taxonomy_centroid_match_threshold` | configurable | Min cosine sim for centroid fast path |
| `taxonomy_centroid_max_age_hours` | `24` | Max age of centroid store before stale |
| `taxonomy_centroids_dir` | `~/.klai/taxonomy_centroids` | JSON sidecar location |
| `taxonomy_cluster_min_size` | configurable | HDBSCAN min_cluster_size |
| `taxonomy_auto_categorise_threshold` | configurable | Min sim for auto-categorise pass 1 |
| `graphiti_llm_rps` | `1` | Rate limit shared by labeler + classifier |
