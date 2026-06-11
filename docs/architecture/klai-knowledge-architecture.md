# Klai Knowledge — Platform Architecture

> Status: Active reference. Research phases complete for §§ 1–12. §13.5 (cross-org federation) remains an open decision. Implementation tracking: `klai-claude/docs/specs/klai-knowledge-implementation.md`.
>
> Source synthesis: `helpdesk-extractie-pipeline.md` (2026-03-18) + `Sovereign Knowledge, Augmented` (2026-01-13). The helpdesk pipeline is now treated as one ingestion adapter, not the product goal. The product goal is Klai Knowledge.
>
> *Last updated: 2026-05-06 (post retrieval-coupling audit). For platform-wide infrastructure and stack decisions, see [architecture/platform.md](platform.md). For the research backing evidence-weighted scoring (§7.4) and assertion mode weights (§3.2, §13.4), see [Evidence-Weighted Knowledge Retrieval: Research Synthesis](../research/README.md).*
>
> *2026-05-06: §2 rewritten to remove the "Two products, one infrastructure" framing — Klai Focus / research-api was decommissioned by `SPEC-PORTAL-UNIFY-KB-001` (April 2026) and `SPEC-DECOMM-FOCUS-001` (May 2026). Klai Knowledge is now the single knowledge product. §7.4 evidence-tier activation track aligned with `SPEC-EVIDENCE-001-FOLLOWUP-001`.*
>
> *2026-06-08 (doc-vs-code drift audit): §0 infra table corrected (gpu-01 models, Graphiti live, PG schema populated, gap inbox live, Mistral stack). Several sections that described a richer design than the code now carry an **Intended vs. current** callout pointing to [`product-gaps-backlog.md`](product-gaps-backlog.md) — notably §8 gap detection (GAP-LOOP-*), §6 taxonomy (GAP-TAX-*), §3.3/§5 provenance (GAP-PROV-*), §7.4/§3.2 evidence weighting (GAP-EVID-*), §5.1 multitenancy (GAP-TENANCY-01), §9.2 MCP read tools (GAP-MCP-01), §10 personal-knowledge enrichment (GAP-PRIV-01). The temporal-validity filter has a field-name bug — see [the retro](../retros/2026-06-08-temporal-filter-field-mismatch.md).*
>
> *2026-06-11 (per-claim re-verification): the temporal field-name bug is **fixed** on the serving path (dual-contract filter + delete-then-upsert; see §5.2 callout and the retro addendum); `is_tenant=True` now ships for new collections (existing collection needs the one-time upgrade script — §5.1 callout); evidence-tier assertion weights are no longer flat (conservative profile, shadow-gated — §7.4 callout); GAP-INGEST-02 (`chunk_type`) was closed by removal. Refreshed gap states: [`product-gaps-backlog.md`](product-gaps-backlog.md); improvement plan: [`knowledge-rag-improvement-plan.md`](knowledge-rag-improvement-plan.md).*

---

## Contents

| § | Section | Status |
|---|---|---|
| 0 | [Current State vs. Target Architecture](#0-current-state-vs-target-architecture) | Reference |
| 1 | [What Klai Knowledge Is](#1-what-klai-knowledge-is) | Stable |
| 2 | [Platform Service Architecture](#2-platform-service-architecture) | Stable |
| 3 | [Knowledge Model](#3-knowledge-model) | Stable |
| 4 | [Ingestion Architecture](#4-ingestion-architecture) | Stable |
| 5 | [Storage Architecture](#5-storage-architecture) | Stable |
| 6 | [Taxonomy](#6-taxonomy) | Researched |
| 7 | [Retrieval Architecture](#7-retrieval-architecture) | Updated — evidence-weighted scoring added (§7.4) |
| 8 | [Gap Detection](#8-gap-detection) | Stable |
| 9 | [AI Interface](#9-ai-interface) | Stable |
| 10 | [Multi-tenancy, Personal Knowledge, Federated Knowledge](#10-multi-tenancy-personal-knowledge-and-federated-knowledge) | Stable |
| 11 | [Publication Layer](#11-publication-layer) | Stable |
| 12 | [The Self-Improving Loop](#12-the-self-improving-loop) | Stable |
| 13 | [Open Questions](#13-open-questions-requiring-resolution) | Mixed — see table in §13 |
| 14 | [Technology Stack](#14-technology-stack) | Stable |
| — | [Appendix: Relation to Existing Klai Components](#appendix-relation-to-existing-klai-components) | Reference |

---

## 1. What Klai Knowledge Is

Klai Knowledge is the organizational memory layer of the Klai platform. It is not a document storage system, not a helpdesk tool, and not a search engine. It is a **living knowledge graph** — continuously updated by human contributions and AI-assisted extraction — that makes organizational knowledge queryable, traceable, and self-improving.

Every organization that uses Klai has a Klai Knowledge instance. That instance accumulates knowledge from every source the organization feeds it: help articles, internal procedures, meeting notes, support transcripts, product documentation. The knowledge is structured, not flat. It knows where claims come from, how confident they are, and whether they are still current.

The AI interface on top is a lens, not an owner. AI helps navigate and surface knowledge. The organization owns the knowledge, in open formats, with full provenance.

### Why it is the platform heart

Without Klai Knowledge:
- The AI chat widget has no grounded answers
- The helpdesk gap detection has no KB to compare against
- The publication layer has no intelligent structure
- Onboarding a new employee means pointing them at 47 documents

With Klai Knowledge:
- Every interface (chat widget, internal tools, external KB) draws from one structured source
- Gap detection identifies what is missing relative to what the organization actually knows
- New employees can ask the knowledge base natural language questions and get grounded answers
- Knowledge that exists in one source flows to others automatically

---

## 0. Current State vs. Target Architecture

This section captures what is running in production as of March 2026 (infrastructure rows re-verified 2026-06-08). The core architecture is built and live. Remaining open items are tracked in §13.

> **2026-06-08 correction.** The embedding / reranker / sparse / transcription
> models are **not** core-01 docker-compose services — they run on **gpu-01** and
> are reached over the `172.18.0.1` autossh tunnel (`TEI_URL=:7997`,
> `TEI_RERANKER_URL=:7998`, `SPARSE_SIDECAR_URL=:8001`). Transcription is the Vexa
> `transcription-service` on gpu-01, not a `whisper-server` container. The LiteLLM
> fallback is `klai-medium` (Mistral Medium), not Ollama. See
> [`platform.md`](platform.md) for the live model stack.

### What exists today

**Deployed infrastructure:**

| Service | Where | What it is | Notes |
|---|---|---|---|
| `docs-app` (klai-docs) | core-01 | Next.js app — reader + REST API | KB publication and CRUD; editor UI lives in klai-portal |
| `docling-serve` | core-01 | Document chunker | HybridChunker; standalone file uploads (SPEC-KB-FILE-UPLOAD-001) |
| TEI (dense BGE-M3) | **gpu-01** `:7997` | Dense embeddings | Reached via `172.18.0.1` tunnel; not a core-01 service |
| BGE-M3 sparse sidecar | **gpu-01** `:8001` | FlagEmbedding sparse | `SPARSE_SIDECAR_URL`; not a core-01 service |
| Infinity reranker | **gpu-01** `:7998` | `bge-reranker-v2-m3` | `TEI_RERANKER_URL`; not a core-01 service |
| Vexa `transcription-service` | **gpu-01** | Audio transcription | Replaces the old `whisper-server` (SPEC-VEXA-003) |
| `knowledge-ingest` | core-01 | Unified ingest API | `/ingest/v1/document`, `/ingest/v1/webhook/gitea`, `/ingest/v1/crawl`, taxonomy + gap-event endpoints |
| `retrieval-api` | core-01 | Retrieval service | `POST /retrieve`; RRF over dense + question/HyDE + sparse **+ graph** (Graphiti live) |
| `klai-knowledge-mcp` | core-01 | MCP write server | `save_personal_knowledge`, `save_org_knowledge`, `save_to_docs`; one read tool `search_knowledge` |
| Qdrant | core-01 | Vector store | `klai_knowledge` collection (org + personal KB) |
| `gitea` | core-01 | Self-hosted Git | One repo per org KB; content store for klai-docs |
| `searxng` | core-01 | Self-hosted web search | Startpage + DuckDuckGo |
| PostgreSQL | core-01 | Relational store | `knowledge` schema (now populated — see below), `docs`, `portal` schemas. pgvector no longer used. |
| LiteLLM | core-01 | LLM routing | Mistral stack (`klai-fast/primary/medium/large` = Mistral Small/Medium/Large); fallback `klai-medium`; routing via `custom_router.py` |
| FalkorDB + Graphiti | core-01 | Knowledge graph | `GRAPHITI_ENABLED=true`; live graph search leg in RRF |
| Zitadel | core-01 | Auth/OIDC | Tenant isolation; all services use same instance |

### What was recently built (March 2026)

| Component | Status |
|---|---|
| Qdrant | ✅ Deployed — `klai_knowledge` collection; `org_id`, `kb_slug`, `user_id` payload indexes |
| `knowledge` schema (PostgreSQL) | ✅ **Populated** — `knowledge.artifacts` (+ provenance/crawl tables) written on every ingest via `pg_store.py`. Qdrant + PostgreSQL are now dual stores keyed by artifact id. (See GAP-PROV-01: the `derivations`/`entities`/`embedding_queue` tables exist but are not yet written — schema-only.) |
| Unified Ingest API | ✅ Built as `knowledge-ingest` — `/ingest/v1/document`, `/ingest/v1/webhook/gitea`, `/ingest/v1/crawl`, taxonomy + gap-event endpoints |
| Retrieval API | ✅ Built — `POST /retrieve` on `retrieval-api`; RRF over dense + question/HyDE + sparse + graph (Graphiti) |
| Personal knowledge saves | ✅ Live — `save_personal_knowledge` via MCP; indexed in Qdrant immediately by knowledge-ingest |
| Org knowledge saves | ✅ Live — `save_org_knowledge` via MCP; indexed in Qdrant immediately |
| Gap detection | ✅ Live — `classify_gap` + `fire_gap_event` in `deploy/litellm/klai_retrieval_telemetry.py` (a 2-class hard/soft classifier; see GAP-LOOP-* for the richer documented design that is **not** built) |
| Gap editorial inbox | ✅ Live — frontend `/app/gaps`, backend `app_gaps.py` (`/gaps`, `/gaps/summary`, `/gaps/by-taxonomy`); hook posts gap events to portal-api `/v1/gap-events` |
| LiteLLM pre-call hook | ✅ Deployed — `KlaiKnowledgeHook` (decomposed into `klai_kb_*` modules); feature gate, user_id scoping, history, kb_slugs filter, strict/open answer policy, gap detection |
| Knowledge model fields in frontmatter | ✅ `KnowledgeFrontmatter` in klai-docs; Zod validation deferred |

### What does NOT exist yet (remaining open items)

> Several capabilities that earlier editions of this doc listed as "live" are
> implemented more simply than described. Those are tracked as Type-B gaps in
> [`product-gaps-backlog.md`](product-gaps-backlog.md) and flagged inline at each
> section, rather than repeated here.

| Component | Where described | Status |
|---|---|---|
| Cross-org knowledge federation | §10.7 | Deferred to V2 (open question §13) |
| Self-maintaining taxonomy (re-cluster / monitor) | §6.4-6.5 | Bootstrap is manual; no periodic clustering task is registered — GAP-TAX-01 |
| Gap-detection LLM judge / semantic registry / lifecycle | §8 | Editorial inbox is live, but the LLM judge, semantic dedup, and lifecycle are not — GAP-LOOP-01..04 |
| Provenance DAG / entity registry / dual-store outbox | §3.3, §5 | Tables exist, write path does not — GAP-PROV-01, GAP-SYNC-01 |

### Completed migrations

**Klai Focus / research-api decommission:** Done. The Focus service was
collapsed into Knowledge in SPEC-PORTAL-UNIFY-KB-001 (April 2026); the
final residual cleanup (`klai_focus` collection, allowlists, dead code
paths) landed in SPEC-DECOMM-FOCUS-001 (May 2026).

**TEI → FlagEmbedding sparse sidecar:** Done. `bge-m3-sparse` sidecar handles sparse embeddings via FlagEmbedding. TEI continues serving dense-only embeddings.

**SearXNG:** Reconfigured (March 2026) — Google/Bing removed, Startpage + DuckDuckGo active. Mojeek configured but disabled (API key needed). See §13.8.

### Component ownership today

The BlockNote editor **currently lives in `klai-portal`** (frontend SPA). It was previously in klai-docs but was migrated to the portal for a unified session flow. klai-docs now contains only the reader and the REST API. A standalone editor in klai-docs can be restored from git (commit `a50797a`) if needed.

---

## 2. Platform Service Architecture

Klai Knowledge is the platform's single knowledge product. The earlier "Klai Focus + Klai Knowledge" two-product framing was unwound by `SPEC-PORTAL-UNIFY-KB-001` (April 2026) and `SPEC-DECOMM-FOCUS-001` (May 2026). The historical Focus narrative (notebook-based research, `notebook_*` scopes, hybrid `broad` mode) is preserved only in those two SPECs and the git history; this document describes the current state.

### 2.1 Knowledge as a single product on shared infrastructure

Klai Knowledge is the organizational memory layer: persistent, curated, broad. It accumulates what the organization knows over time. There is no separate ephemeral notebook product — `/app/focus/*` redirects to `/app/knowledge`, and personal-notes-style use cases live as personal-scope knowledge artifacts.

```
┌─────────────────────────────────────────────────────────┐
│                  Shared Platform Services                │
│                                                         │
│  TEI (embeddings)      BGE-M3 dense + bge-m3-sparse     │
│  Infinity (reranker)   bge-reranker-v2-m3               │
│  docling-serve         HybridChunker                    │
│  Qdrant                klai_knowledge collection only   │
│  FalkorDB              Graphiti knowledge graph         │
└─────────────────────────────────────────────────────────┘
          ▲
          │
┌──────────────────────┐
│  Knowledge Service   │
│  retrieval-api +     │
│  knowledge-ingest    │
│                      │
│  POST /retrieve      │
│  POST /ingest        │
└──────────────────────┘
```

### 2.2 LibreChat + Partner API integration via LiteLLM hook

LibreChat tenants and Partner-API consumers reach knowledge automatically through a pre-call hook in the LiteLLM proxy. This is the primary consumer interface for §9.

```
LibreChat-{slug} container          Partner-API consumer / Widget
  │  LITELLM_API_KEY (org_id)         │  pk_live_... or widget JWT
  ▼                                   ▼
                LiteLLM proxy (klai-core)
                  │  KlaiKnowledgeHook.async_pre_call_hook
                  │    ├── trivial? → skip
                  │    ├── resolve identity:
                  │    │    LibreChat   → real Zitadel user_id
                  │    │    Partner/Widget → synthetic `partner:<key_id>`
                  │    └── POST /retrieve  (3s timeout, graceful degrade)
                  ▼
                klai-primary / klai-fast / klai-large
```

Tenant isolation: one LiteLLM team key per LibreChat container, carrying `org_id` in metadata. The hook uses this to scope every retrieval query. Partner-API and widget traffic carries an explicit `org_id` from the partner key's owning tenant (validated via portal `/internal/identity/verify` against `partner_api_keys` — see [knowledge-retrieval-flow.md § Identity verification](knowledge-retrieval-flow.md#identity-verification-on-every-retrieve-call)).

### 2.3 Qdrant scope conventions

All knowledge lives in the `klai_knowledge` Qdrant collection with `org_id` payload indexing. Scope identifiers use Zitadel IDs — the stable cross-system identifiers available on `PortalOrg.zitadel_org_id` and `PortalUser.zitadel_user_id`. Not PostgreSQL integer PKs, not UUIDs.

| Payload field | Scope semantic | Set by |
|---|---|---|
| `org_id` | Organization (always required) | knowledge-ingest at upsert |
| `user_id` | Personal scope (optional, only on personal artifacts) | knowledge-ingest |
| `visibility` | `public` / `internal` / `private` (per-chunk) | knowledge-ingest from KB config |
| `kb_slug` | Per-KB partition within an org | knowledge-ingest |

**Interface-to-scope mapping:**

| Interface | Org scope | Personal scope | Notes |
|---|---|---|---|
| LiteLLM pre-call hook (LibreChat) | Yes (`org` or `both`) | Yes when `kb_personal_enabled=true` | KBScopeBar controls scope |
| LiteLLM pre-call hook (Partner/Widget) | Yes (`org` only) | No | Scope-locked at credential creation |
| MCP server tools | Yes | Yes | Explicit user action; Zitadel session provides both IDs |

`scope=notebook` and `scope=broad` were the integration points with the now-decommissioned Focus service. Both literals were removed from `RetrieveRequest` in `SPEC-DECOMM-FOCUS-001`; the only valid scopes today are `personal`, `org`, `both`.

---

## 3. Knowledge Model

### 3.1 Two structural types

Klai Knowledge has exactly two structural artifact types. The distinction is not about who created something but about its **causal relationship to reality**.

| Type | Definition | Key property |
|---|---|---|
| **`source_document`** | An artifact that was captured or ingested — it records something that happened or existed independently of this system | Immutable after ingest; never directly serves as an answer |
| **`knowledge_artifact`** | An artifact that was constructed — intentionally assembled to represent a knowledge claim | Evolves via `superseded_by`; the thing the system actually serves in responses |

A raw helpdesk transcript is a `source_document`. An extracted problem/solution pair is a `knowledge_artifact`. A procedure written by a domain expert is a `knowledge_artifact`. A conclusion from a human-AI sparring session that was deliberately saved is a `knowledge_artifact` — the AI involvement is irrelevant to its type.

**The operational consequence:** source documents are evidence. Knowledge artifacts are claims. When the system generates an answer, it serves knowledge artifacts and cites source documents. It never serves a raw source document as if it were a conclusion.

---

### 3.2 Three metadata axes (not a type hierarchy)

The research literature (PROV-O, Wikidata, GraphRAG, Zettelkasten) shows that knowledge distinctions people intuitively call "types" are actually three orthogonal axes that get collapsed into one. Collapsing them is the most common design failure in knowledge systems.

**Axis 1 — Provenance origin** (where did this come from?)

Maps to W3C PROV-O properties. Stored as `provenance_type` on every artifact.

| Value | Meaning | PROV-O equivalent |
|---|---|---|
| `observed` | Directly captured from an event (transcript, sensor, raw import) | `hadPrimarySource` |
| `extracted` | Derived from source text by deterministic or LLM extraction | `wasDerivedFrom` |
| `synthesized` | Constructed from multiple sources by reasoning or AI generation | `wasDerivedFrom` (multi-input) |
| `revised` | Updated version of an existing knowledge artifact | `wasRevisionOf` |

**Axis 2 — Assertion mode** (what kind of claim is this?)

Stored as `assertion_mode` on every knowledge artifact.

| Value | Meaning |
|---|---|
| `factual` | Claimed to be true ("the return period is 30 days") |
| `procedural` | A sequence of steps claimed to achieve a goal |
| `quoted` | Attributed to a specific source, not endorsed independently |
| `belief` | Held as likely true but not verified ("we think this affects macOS 14 only") |
| `hypothesis` | Explicitly speculative; requires validation |
| `unknown` | Epistemic status not specified; system default for untagged content (SPEC-TAXONOMY-001 DD-2) |

**Research finding — 3 vs. 5 assertion categories:**

The DB schema stores 6 values: 5 epistemic categories above plus `unknown` for content with no explicit classification. However, [assertion modes research](../research/assertion-modes/assertion-modes-research.md) found that human inter-annotator agreement drops from ~89% at 3 categories to ~67% at 5 categories (Prieto et al. 2020, Rubin 2007). For **retrieval scoring and automated classification**, the 5 epistemic values should be grouped into 3: `assertion` (factual + quoted), `speculation` (hypothesis + belief), `procedure` (procedural). The 6-value schema remains the source of truth for storage and manual labeling; grouping happens only at the scoring/classification layer. `unknown` carries flat/neutral weight in retrieval scoring — see [SPEC-TAXONOMY-001 Realignment Note](../../.moai/specs/SPEC-TAXONOMY-001/spec.md) for why it is not a sixth epistemic category but an explicit "no-classification" sentinel.

See also: [Assertion Mode Weights](../research/assertion-modes/assertion-mode-weights.md) for starting weight recommendations and maximum safe spread derivation.

**Axis 3 — Synthesis depth** (how much epistemic work has been done?)

Maps to the Zettelkasten transformation chain and GraphRAG's Document → TextUnit → Entity → Community Report levels. Stored as `synthesis_depth` (integer 0–4).

| Depth | Description | Example |
|---|---|---|
| 0 | Raw capture, unprocessed | Transcript chunk, imported PDF page |
| 1 | Extracted claim, directly attributable to a source span | Problem/solution pair extracted from one transcript |
| 2 | Synthesized from multiple sources, single topic | Procedure consolidated from 5 similar transcripts |
| 3 | Cross-topic synthesis, organizational position | Product area knowledge summary |
| 4 | Published artifact, fully curated | Public help article, approved policy document |

**Why three axes instead of one:** A Wikidata editor manually entering a fact from a primary source is active construction + depth 1 + `observed` provenance simultaneously. An AI-generated summary is synthesis depth 3 + `synthesized` provenance + `factual` assertion. These behave differently in retrieval weighting (§7.4), citation style, and invalidation logic. A single type label cannot capture this.

---

### 3.3 Provenance chain

Every knowledge artifact maintains a reference chain to its inputs, following GraphRAG's TextUnit → Entity → Community Report model and Wikidata's Statement/Reference pattern.

```yaml
id: ka-8821
type: knowledge_artifact
provenance_type: synthesized
assertion_mode: procedural
synthesis_depth: 2
title: "VoIP adapter setup on macOS 14 — disable IPv6 workaround"
derived_from:
  - source: src-2847          # Helpdesk transcript, 2026-02-14
    span: "agent said: 'you need to disable IPv6 first'"
  - source: src-3102          # Helpdesk transcript, 2026-02-28
    span: "customer confirmed IPv6 was the issue"
  - source: src-3309          # Helpdesk transcript, 2026-03-01
    span: "same problem, same resolution"
confidence: 0.87              # 3 independent sources, consistent
belief_time_start: 2026-02-14
belief_time_end: null         # still active
superseded_by: null
```

This chain enables:
- **Invalidation cascade** — if `src-2847` is retracted or its product version is deprecated, `ka-8821` is flagged for review automatically
- **Confidence calibration** — the system knows this claim rests on 3 independent transcripts; a claim on 1 transcript gets lower weight
- **Temporal reasoning** — "what did we believe about this procedure in Q3 2024?" is answerable from `belief_time_start/end`

> **Intended vs. current (GAP-PROV-02).** This is target design. In code the
> direct Docs chain is partially populated: `search_knowledge` exposes source
> `artifact_id`, `save_to_docs` accepts `derived_from`, and ingest writes valid
> parent→child rows to `knowledge.derivations`. Automatic source capture, editor
> display, delete warnings, and confidence-from-source-count are still open.
> `confidence` is a manual `high/medium/low` frontmatter label — never computed
> from independent-source count and never read in retrieval scoring. So the full
> invalidation cascade and confidence calibration still lack all required inputs
> (their precondition — complete populated provenance
> edges + computed confidence — is missing; cf. §5.2 GAP-PROV-01). Temporal reasoning has
> a separate field-name bug — see [the retro](../retros/2026-06-08-temporal-filter-field-mismatch.md).

---

### 3.4 Knowledge evolution

Knowledge artifacts evolve without deletion. When a claim is superseded, the old artifact is preserved and linked:

```yaml
# Old artifact
id: ka-8821
superseded_by: ka-9104
belief_time_end: 2026-03-15

# New artifact
id: ka-9104
provenance_type: revised
assertion_mode: procedural
synthesis_depth: 4
derived_from:
  - source: ka-8821           # supersedes the earlier synthesis
  - source: src-4201          # new source that prompted the revision
belief_time_start: 2026-03-15
```

At query time: the system retrieves the most recent non-superseded artifact (`superseded_by: null`, `belief_time_end: null`). Historical queries use `belief_time_start ≤ query_date ≤ belief_time_end`.

The same pattern applies to gap detection: gaps are never deleted, only linked to their resolving artifact (`resolving_artifact_id`).

---

### 3.5 Time dimensions

| Field | Question it answers | V1 status |
|---|---|---|
| `belief_time_start` | When did this organization start holding this position? | Implemented |
| `belief_time_end` | When did this organization stop holding this position? (`null` = still active) | Implemented |
| `system_time` | When was this recorded in Klai Knowledge? | Auto-set on write |
| `world_time` | When was this objectively true in the world? | Dropped in V1 — insufficient signal quality at ingest |

Implementation uses Unix epoch integers in Qdrant payloads (not ISO strings — range filters require numeric types). Sentinel `9999-12-31` → `253402300800` for active items instead of NULL, enabling `belief_time_end <= query_timestamp` range filters without null-handling edge cases.

Concrete query patterns:

| Query | Filter |
|---|---|
| Current knowledge | `belief_time_end = 253402300800 AND synthesis_depth >= 2` |
| What did we know on 2024-09-01? | `belief_time_start <= 1725148800 AND belief_time_end > 1725148800` |
| All superseded procedures | `assertion_mode = procedural AND superseded_by != null` |

---

## 4. Ingestion Architecture

> **Engineering reference:** [knowledge-ingest-flow.md](knowledge-ingest-flow.md) — verified against the live codebase on core-01.

Klai Knowledge accepts knowledge from multiple source types. Each source type has a dedicated adapter that normalizes input into a standard format before passing it to the unified ingest pipeline.

### 4.1 Ingestion adapters

| Source type | Adapter | How it reaches knowledge-ingest |
|---|---|---|
| Human-authored content | KB editor (BlockNote) → markdown | Direct API call on every save (debounced 3s) |
| Help articles (web/HTML) | Crawl4AI → markdown | klai-connector webcrawler via Gitea webhook |
| GitHub repositories | klai-connector → raw markdown | klai-connector Gitea webhook |
| Documents (PDF, DOCX, XLSX) | docling-serve HybridChunker | klai-connector file upload endpoint |
| Meeting transcripts | scribe-api → transcript JSON | scribe-api posts to `/ingest/v1/document` |
| MCP writes (personal/org/docs) | klai-knowledge-mcp | All three save tools post to `/ingest/v1/document` (scope carried in the body, not the path) |

All adapters deliver to `knowledge-ingest` via the single `/ingest/v1/document` endpoint (plus `/ingest/v1/webhook/gitea` and `/ingest/v1/crawl`). There are **no** `/ingest/v1/meetings`, `/ingest/v1/personal`, or `/ingest/v1/org` endpoints — scope (org vs personal) is a body field. Adapters do not write directly to Qdrant — this is enforced at the architecture level.

### 4.2 Enrichment pipeline

Ingestion runs in two phases to keep the document searchable immediately while enrichment happens in the background.

**Phase 1 — Immediate (synchronous, < 1s)**

1. Parse and chunk (docling HybridChunker or markdown splitter)
2. Embed chunks with BGE-M3 dense vectors (gpu-01 via SSH tunnel)
3. Store in Qdrant with basic metadata → document is immediately searchable

**Phase 2 — Async enrichment (Procrastinate job queue, background)**

A. **Contextual prefix** — LLM generates a 1–2 sentence prefix situating each chunk within its parent document. Addresses chunk boundary loss. Prefix is prepended to the chunk text before re-embedding.

B. **Dense re-embed** — BGE-M3 dense vectors recomputed on the context-enriched text (replaces Phase 1 vectors).

C. **Sparse embed** — BGE-M3 sparse vectors (SPLADE-style) via the `bge-m3-sparse` FlagEmbedding sidecar. BGE-M3 sparse is not supported in TEI (open bug since June 2024) — FlagEmbedding is the only production-compatible path.

D. **HyPE questions** — LLM generates 3–5 hypothetical questions the chunk answers. These are embedded and stored as a separate `vector_questions` index in Qdrant. Bridges the vocabulary gap between user query language and document language. Result: +42 pp precision, +45 pp recall vs. direct embedding (Vake et al., 2025 — single benchmark, real-world gains will be lower; validate on your corpus).

E. **Knowledge graph** — Graphiti entity/relationship extraction → FalkorDB. Controlled by `settings.graphiti_enabled`; **on** in production on both `knowledge-ingest` and `retrieval-api` now that LLM + reranker dependencies run on gpu-01. See §5.3.

> Model selection for Phase 2 LLM calls: see §13.6.

### 4.3 Helpdesk transcript extraction (one adapter in detail)

The helpdesk adapter extracts structured contributions from support transcripts. Key signals:

- `problem_summary` — core problem in 1–2 sentences; basis for semantic matching
- `information_sought` — what the customer was looking for, normalized as a search query
- `unanswered_questions` — questions the agent could NOT satisfactorily answer; primary gap signal
- `agent_uncertainty_indicators` — exact phrases ("I don't know", "let me check"); proxy for missing agent knowledge
- `knowledge_gap_signal` — LLM heuristic (none/weak/strong)
- `resolution.resolved` — unresolved conversations are the heaviest gap candidates

A two-pass extraction strategy improves recall on the critical `unanswered_questions` field: the first pass extracts all fields; a second "gleaning" pass runs only when `knowledge_gap_signal != none` OR `resolution.resolved == false`. The second condition is essential — without it, the gleaning pass misses exactly the cases where the model underreported the gap in pass 1.

---

## 5. Storage Architecture

### 5.1 Vector store: Qdrant, single collection with tenant isolation

**One Qdrant collection for all tenants (`klai_knowledge`).** Tenant isolation via `org_id` payload field with `is_tenant: true` index flag (Qdrant 1.12+ native multitenancy). Every query includes a mandatory `must: org_id = X` filter, enforced at the API layer — not optional, not configurable.

**Why not collection-per-tenant:** Qdrant's own documentation explicitly discourages more than ~10 collections. Each collection carries independent memory overhead, file descriptors, HNSW index state, and background processes. At 50+ tenants this causes OOM and cluster instability. This was the previous design — it is wrong at scale.

**Why payload filtering is sufficient isolation here:** With `is_tenant: true` indexing, Qdrant builds a dedicated HNSW subgraph per tenant. Filtered search on tenant-indexed collections does not degrade recall the way unindexed payload filters do. This is the specific feature that makes single-collection multitenancy viable without recall loss.

**Large tenants:** Qdrant 1.16 tiered multitenancy allows promoting tenants above a vector count threshold (e.g., >20,000 vectors) to dedicated shards automatically. This provides the isolation benefits of a dedicated collection without the operational overhead.

> **Intended vs. current (GAP-TENANCY-01, updated 2026-06-11).** Isolation is correct;
> the optimization mechanisms are now partially in code:
> - `ensure_collection()` creates the `org_id` index with
>   `KeywordIndexParams(is_tenant=True)` for **new** collections
>   (`qdrant_store.py:85-102`). Collections created before this landed keep their plain
>   keyword index until the one-time online upgrade
>   (`scripts/upgrade_org_id_tenant_index.py`) is run — deliberately not auto-rebuilt at
>   startup. Whether the production `klai_knowledge` collection has been upgraded must be
>   verified against the live payload schema.
> - **Tiered sharding** for large tenants is not implemented (no `shard_key` / sharding
>   code anywhere).
>
> Also: the payload key is `org_id` (the raw Zitadel org id) with a separate `user_id`
> field — not a single `tenant_id`. There is **no `gap_{org_uuid}` Qdrant scope**; gap
> detection lives in the Postgres `portal_retrieval_gaps` table (see §8 callout,
> GAP-LOOP-02). The scope table below is the documented scheme, not literal payload values.

**GDPR right-to-erasure:** delete-by-filter on `org_id` removes all vectors for that tenant (`qdrant_store.delete_kb` / `delete_document`). For personal scopes the filter also matches `user_id`.

**Scopes tracked in the collection payload:**

| `org_id` value | Scope | Accessible by |
|---|---|---|
| `org_{org_uuid}` | Org knowledge | All org members |
| `user_{org_uuid}_{user_uuid}` | User personal knowledge | That user only |
| `gap_{org_uuid}` | Gap detection registry | Org admins |

---

### 5.2 Structured storage: PostgreSQL `knowledge` schema

**SQLite is removed from this design.** It has one writer at a time (even in WAL mode), cannot serve multiple services concurrently, and provides none of the analytical query capability needed here. PostgreSQL is already running for klai-docs. This adds a `knowledge` schema to the same cluster — zero new infrastructure.

> **Intended vs. current — parts of this schema are typed-but-inert.** `knowledge.artifacts`
> (+ crawl/provenance-adjacent tables) **is** populated on every ingest. But several
> structures below exist only as DDL with no write path:
> - `derivations` (provenance DAG), `entities` + `artifact_entities` (entity registry):
>   **0 INSERTs** in production code — GAP-PROV-01. The `WITH RECURSIVE` invalidation
>   cascade and entity analytics the schema is designed for cannot return data. Entity
>   data that *is* produced (Graphiti) lives in FalkorDB + the Qdrant payload, not here.
> - `superseded_by`: same-path replacement ingest now links the just-closed
>   artifact row to the replacement artifact. This gives PG a supersession chain
>   for ordinary re-ingest, but broader entity/provenance consumers remain partial.
> - `embedding_queue` (transactional outbox): **0 INSERTs** — the write path is a direct
>   PG-then-Qdrant write with no outbox or retry. A nightly read-only
>   PG↔Qdrant reconciliation job now detects divergence and alerts, but does not
>   repair it — GAP-SYNC-01/H2.
> - **Temporal-validity bug — FIXED 2026-06-11 on the serving path:** retrieval now uses
>   a dual-contract filter (`search.py::_temporal_validity_filter`) covering both the
>   legacy `valid_at`/`invalid_at` and the ingest-written `valid_from`/`valid_until`
>   fields, and both Qdrant upsert paths delete all points for `(org, kb, path)` before
>   re-upserting, so same-path re-ingest leaves no stale points. What remains:
>   `soft_delete_artifact()` updates PG only (Qdrant hygiene relies on delete-then-upsert;
>   a failed Qdrant call after PG commit is silent divergence → GAP-SYNC-01). The
>   `valid_from`/`valid_until` payload fields now get integer indexes during
>   `ensure_collection()`. History:
>   [the retro](../retros/2026-06-08-temporal-filter-field-mismatch.md) (with 2026-06-11
>   status addendum).

**Schema overview:**

```sql
-- knowledge.artifacts: canonical metadata for every knowledge artifact
CREATE TABLE knowledge.artifacts (
  id            UUID PRIMARY KEY,
  org_id        UUID NOT NULL,              -- tenant scope
  user_id       UUID,                       -- non-null = personal scope
  provenance_type TEXT NOT NULL,            -- observed | extracted | synthesized | revised
  assertion_mode  TEXT NOT NULL,            -- factual | procedural | quoted | belief | hypothesis
  synthesis_depth SMALLINT NOT NULL,        -- 0–4
  confidence    REAL,
  belief_time_start BIGINT NOT NULL,        -- Unix epoch
  belief_time_end   BIGINT NOT NULL DEFAULT 253402300800,  -- sentinel = active
  superseded_by UUID REFERENCES knowledge.artifacts(id),
  created_at    BIGINT NOT NULL
);

-- knowledge.derivations: provenance DAG (adjacency list)
CREATE TABLE knowledge.derivations (
  child_id   UUID NOT NULL REFERENCES knowledge.artifacts(id),
  parent_id  UUID NOT NULL REFERENCES knowledge.artifacts(id),
  span_json  JSONB,                         -- {start, end} char offset in source
  PRIMARY KEY (child_id, parent_id)
);

-- knowledge.entities: entity registry
CREATE TABLE knowledge.entities (
  id          UUID PRIMARY KEY,
  org_id      UUID NOT NULL,
  name        TEXT NOT NULL,
  type        TEXT NOT NULL,                -- product_area | feature | concept | person
  created_at  BIGINT NOT NULL
);

-- knowledge.artifact_entities: many-to-many
CREATE TABLE knowledge.artifact_entities (
  artifact_id UUID NOT NULL REFERENCES knowledge.artifacts(id),
  entity_id   UUID NOT NULL REFERENCES knowledge.entities(id),
  resolved    BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (artifact_id, entity_id)
);

-- knowledge.embedding_queue: outbox for Qdrant sync
CREATE TABLE knowledge.embedding_queue (
  id          UUID PRIMARY KEY,
  artifact_id UUID NOT NULL,
  operation   TEXT NOT NULL,               -- upsert | delete
  created_at  BIGINT NOT NULL,
  processed_at BIGINT
);
```

**Analytical queries run directly against PostgreSQL:**

```sql
-- Which product areas have the most unresolved conversations?
SELECT e.name, COUNT(*) total, SUM(CASE WHEN ae.resolved = false THEN 1 ELSE 0 END) unresolved
FROM knowledge.artifact_entities ae
JOIN knowledge.entities e ON ae.entity_id = e.id
WHERE e.type = 'product_area'
GROUP BY e.name ORDER BY unresolved DESC;

-- Invalidation cascade: all artifacts derived from a given source
WITH RECURSIVE lineage AS (
  SELECT child_id FROM knowledge.derivations WHERE parent_id = $source_id
  UNION ALL
  SELECT d.child_id FROM knowledge.derivations d JOIN lineage l ON d.parent_id = l.child_id
)
SELECT * FROM lineage;
```

**Write path:** PostgreSQL write (within transaction, including `embedding_queue` row) → background worker reads queue → Qdrant upsert → queue record marked processed. If the Qdrant write fails: retry using the PostgreSQL record as source of truth. Nightly reconciliation job validates bidirectional consistency.

**Read path:** Qdrant filtered vector search returns point IDs + scores → PostgreSQL `WHERE id = ANY($ids)` fetches full metadata and provenance. Analytical dashboards query PostgreSQL directly, bypassing Qdrant entirely.

The `id` is the shared key across both stores. Qdrant point ID = PostgreSQL `artifacts.id`.

### 5.3 Knowledge graph: ACTIVE (ingest + retrieval)

FalkorDB runs on core-01. Graphiti integration is built into `knowledge-ingest` Phase 2 enrichment (Step E) and a parallel graph search branch is called from `retrieval-api` alongside the Qdrant 3-leg RRF fusion. `GRAPHITI_ENABLED=true` on both containers in `deploy/docker-compose.yml`.

Graph retrieval was temporarily disabled on the retrieval side in March 2026
(commit `3c6d1002`) because graph search + reranker took 35-60 s per query
when their LLM/embedding/reranker dependencies ran on CPU. Both are back online
now that the inference dependencies are served by GPU-backed runtime services.

**Original decision rationale (V1 gate):** do not activate the graph layer until query analysis justifies it for B2B knowledge base query patterns.

Research finding (GraphRAG-Bench, ICLR 2026; RAG vs. GraphRAG systematic evaluation, 2025; SAP enterprise study, 2025):

Graph RAG provides measurable benefit **only for multi-hop relational queries** — queries that require traversing entity relationships across multiple documents (e.g., "which features are affected by the vendor who supplies component X?"). For single-hop factual queries ("what is the return policy?") and procedural queries ("how do I reset my password?") — which constitute the majority of B2B knowledge base traffic — graph RAG either matches or underperforms hybrid vector retrieval. MS-GraphRAG in global mode regressed from 60.92% to 36.92% on fact retrieval in one benchmark: a catastrophic result.

**The LightRAG "90% fewer tokens" claim is inverted.** GraphRAG-Bench measured LightRAG at ~100,000 tokens per query prompt vs. ~954 for vanilla RAG — a 100× overhead, not a 90% reduction. The claim was a misreading of a retrieval-phase comparison on a specific large corpus. At GPT-4o pricing, LightRAG at 100K tokens/query costs $0.30–$1.00 per query. This is untenable at volume.

**LightRAG is also pre-production for self-hosted B2B:** 2,000+ open GitHub issues, upgrade breakage in production causing "extremely long downtime" (issue #2255), entity extraction failures with models below 32B parameters, and query latency degrading from 2s to 15s+ at 10,000 documents without tuning.

**Gate condition for adding a graph layer:**
Before building any graph infrastructure, sample 200 real user queries from the platform and classify them as single-hop factual / procedural / multi-hop relational. If fewer than 20% are multi-hop relational, do not add a graph layer. If multi-hop queries are significant, evaluate **HippoRAG2 + SpaCy-based construction**:
- SpaCy for entity/relationship extraction at ingest: zero LLM cost, achieves 94% of LLM-based construction quality (SAP study)
- HippoRAG2 Personalized PageRank traversal at query time: ~1,000 tokens/query (vs. LightRAG's 100,000), documented multi-hop recall improvement of +5–13pp on multi-hop benchmarks, no regression on simple queries

**FastGraphRAG** (circlemind-ai, MIT) is an alternative: ~4,200 tokens/query, incrementally updatable, but no published accuracy benchmarks.

Do not use full Microsoft GraphRAG (community reports too expensive, no benefit for topically narrow corpora) or LightRAG as primary graph system.

### 5.4 YAML frontmatter as metadata store

**klai-docs uses YAML frontmatter as the sole per-page metadata store.** PostgreSQL holds only org/KB structure and access control. Everything specific to a page — including all knowledge model metadata — lives in the file's frontmatter. This is the existing architecture; the knowledge model fields extend it naturally.

**What the current system already parses** (`lib/markdown.ts`): `id` (UUID, stable, set once), `title`, `description`, `icon`, `edit_access`, `redirects`. The serialize/deserialize pipeline uses `gray-matter` + `js-yaml` and preserves unknown fields on read-modify-write, so adding new fields does not break existing content.

**Extended frontmatter schema for knowledge artifacts:**

```yaml
---
id: "3f4a1c2d-8b9e-4f1a-b2c3-d4e5f6a7b8c9"   # stable UUID, set at creation
title: "VoIP Adapter — macOS IPv6 Workaround"
description: "Step-by-step fix for IPv6 connectivity failure on macOS 14+."
icon: "🔧"
# --- knowledge model fields ---
provenance_type: "synthesized"   # observed | extracted | synthesized | revised
assertion_mode: "procedural"     # factual | procedural | quoted | belief | hypothesis
synthesis_depth: 2               # 0 = raw source, 4 = published curated artifact
confidence: "high"               # high | medium | low
belief_time_start: "2026-02-14"  # quoted string — prevents js-yaml Date coercion
belief_time_end: null            # null = still believed
superseded_by: null              # UUID of replacement artifact, or null
derived_from:                    # UUIDs of source artifacts — never slugs (slugs break on rename)
  - "9a4f1c2d-..."
  - "b2e30f1a-..."
# --- existing fields ---
redirects: []
edit_access: "org"
---
```

**What belongs in frontmatter vs. PostgreSQL only:**

| Field | Frontmatter | PostgreSQL | Reason |
|---|---|---|---|
| `id`, `title`, `provenance_type`, `assertion_mode`, `synthesis_depth`, `confidence`, `belief_time_*`, `superseded_by` | Yes | Yes (cached) | Human-readable in editor; PostgreSQL cache enables analytical queries without scanning every file |
| `derived_from` (UUIDs) | Yes (immediate parents only) | Yes — full adjacency table | Frontmatter holds direct parents for display; PostgreSQL holds full DAG for recursive traversal queries |
| Entity relationships | No | Yes only | Too verbose for frontmatter; only useful for SQL aggregations |
| `embedding_queue` state | No | Yes only | Machine-only, no editorial relevance |

**Three implementation constraints from the codebase:**

1. **Always use quoted strings for dates.** `js-yaml` silently coerces bare YAML dates (`2026-02-14`) to JavaScript `Date` objects, which then serialize back as full ISO timestamps. Use `"2026-02-14"` (quoted) — preserved as string throughout.

2. **Use UUIDs in `derived_from`, not slugs.** The rename route updates slugs in `_sidebar.yaml` and body wikilinks but does not scan frontmatter fields. A slug reference in frontmatter will silently become stale after any rename. The stable `id` UUID is the correct reference key — already used by the wikilink system (`data-wikilink="uuid"`).

3. **The PUT handler in `app/api/orgs/[org]/kbs/[kb]/pages/[...path]/route.ts` must be extended** to accept and pass through knowledge model fields. Currently it destructures only `{ title, content, icon, sha, edit_access }`. New fields must be added to the accepted body or the handler changed to accept an arbitrary `frontmatter` object with Zod validation. Without this, the fields can be preserved in existing files but cannot be written by the API.

**Wikilinks and provenance coexist without conflict.** `[[Page Title]]` in body text produces `<a data-wikilink="uuid">` embedded HTML — reader navigation. `derived_from` UUIDs in frontmatter are provenance graph references — machine-interpreted metadata. Both use the same UUID namespace (`buildPageIndex` already maintains the id→slug mapping). No new machinery required.

---

## 6. Taxonomy

### 6.1 What taxonomy does in this system

The taxonomy is the navigational and analytical structure over the knowledge base. It determines:
- How knowledge items are categorized for browsing and filtering
- How gap-detection results are prioritized and grouped
- How the gap-detection output tells editors *where* a new article belongs
- How analytical queries (SQLite) are structured

> **Current implementation status (GAP-TAX-01/02/03).** The taxonomy apparatus is fully
> built but largely **inert** in production:
> - No periodic HDBSCAN re-clustering task is registered — the "monthly
>   monitoring / re-cluster / merge suggestion" loop in §6.4-6.5 does not run.
>   A KB's taxonomy is frozen at the manual admin bootstrap (GAP-TAX-01).
> - Query-time `coverage` is **binary** (≥1 node ⇒ 1.0, else 0.0;
>   `taxonomy_lookup.py`), not the "fraction of corpus classified" of §6.7 (GAP-TAX-02).
> - The automatic chat path never sends `taxonomy_node_ids` as a retrieval filter, so
>   per-document classification is effectively **write-only** for normal retrieval, and on
>   any KB with 0 curated nodes the whole classifier no-ops (GAP-TAX-03).
> Document *classification* against an existing taxonomy works; taxonomy *curation* is
> manual-only — consistent with §6.2's verdict, but more inert than the prose implies.

### 6.2 The "self-managing taxonomy" claim: verdict

The working assumption from the prior research document was: "The taxonomy manages itself. There is no maintenance process, no governance meeting, no manual migration required."

**This claim is significantly overstated. No production B2B knowledge management system operates this way.**

Research findings (Taxonomy Boot Camp 2024; Enterprise Knowledge 2024; arXiv 2502.18469, 2510.15125):
- Every production KM system studied — Intercom, Zendesk, Tettra, enterprise KM vendors — maintains a human approval step for taxonomy changes.
- Enterprise Knowledge's 2024 report is explicit: "Decision making for taxonomy management still requires human judgement regarding organizational alignment and business objectives. No AI system can autonomously manage long-term taxonomy updates."
- The conflation is between *document classification* (largely automatable once a taxonomy is defined) and *taxonomy curation* (not automatable without unacceptable error rates).

### 6.3 What BERTopic + HDBSCAN actually produces

BERTopic with HDBSCAN can discover an initial taxonomy from unlabeled documents. What it genuinely delivers vs. where it fails:

**Genuine strengths:**
- Outperforms LDA and Top2Vec on short-to-medium text in multiple comparisons (specific benchmark numbers vary by source and dataset — the "34% improvement" figure cited in prior research came from a blog post, not a peer-reviewed benchmark; treat as directionally correct, not precise)
- No predefined number of topics required — suitable for exploratory discovery
- LLM-generated cluster names rate 2.7–2.8/5 by human evaluators, vs. 1.2/5 for keyword-only labels
- Modular pipeline — each stage can be swapped independently

**Documented failure modes for production use:**
- **20–40% outlier rate**: HDBSCAN assigns documents that don't fit any cluster to class -1 (noise). In practice, 20–40% of a real-world corpus ends up unclassified with default parameters. These are not garbage documents — they are legitimate content the model failed to cluster.
- **Minimum viable corpus ~1,000 documents**: For corpora below this size, HDBSCAN produces unstable, unreliable clusters. A B2B tenant onboarding with 200 articles will get poor results.
- **Hyperparameter sensitivity**: `min_cluster_size` and `min_samples` have a "dramatic effect" on output. Too low → hundreds of micro-topics, many noise artifacts. Too high → real distinct topics get merged into coarse buckets. There is no universal default; manual tuning per corpus is required.
- **Stochastic non-reproducibility**: UMAP (used for dimensionality reduction before clustering) is stochastic by default. Two runs on the same corpus can produce different taxonomy structures. This is incompatible with production systems that need stable taxonomy IDs for downstream tagging. Fix `random_state` explicitly.
- **LLM labels are plausible, not reliable**: Labels often default to generic ("Customer Service Issues", "Technical Problems"). Human inter-rater agreement on label quality was only moderate (Cohen's Kappa = 0.66). Labels require validation before use in a customer-facing system.

**Online learning (incremental updates) has a fundamental flaw:**
BERTopic's `.partial_fit()` uses a decay mechanism that progressively reduces the weight of older documents. This causes semantic drift by design — the taxonomy you deployed at month 1 is not the same taxonomy at month 6, even if concepts have not changed. For a system that needs stable category IDs for retrieval and gap tracking, this is a production-blocking issue. Online learning should not be used as the primary taxonomy evolution mechanism without a human gate that validates changes before they are applied.

### 6.4 Recommended architecture: tiered by tenant size

**Small tenants (<100 documents):**
BERTopic cannot produce stable clusters on this corpus size. Do not attempt automatic taxonomy discovery. Use a manually curated shared base taxonomy (maintained by Klai) with tenant-level tag customization. This covers the majority of new tenants at onboarding.

**Medium tenants (100–5,000 documents):**
Use BERTopic (or FASTopic — see §6.6) for initial taxonomy discovery at onboarding, after the corpus crosses ~1,000 documents. Treat output as a *proposal*, not a committed taxonomy. Apply mandatory human review before activation. Set `min_cluster_size` to approximately 1–3% of total document count. Expect initial outlier rate of 20–40%; budget for a review pass of the unclassified pool.

**Large tenants (5,000+ documents):**
BERTopic with fixed `random_state` for initial discovery. Human review gate before activation. Monthly automated monitoring (outlier rate, coverage per node, document velocity). Quarterly human review of flagged anomalies. Estimate: 30–60 minutes per active tenant per quarter for an experienced reviewer.

### 6.5 What to automate vs. what requires human approval

**Automate:**
- Tagging new documents to the *existing, approved* taxonomy using a trained classifier
- Detecting when outlier rate exceeds a threshold (e.g., >30% of a new batch unclassified) — surface as alert
- Generating candidate labels for newly detected clusters — feed to human review queue
- Surfacing merge suggestions when two nodes have high cosine similarity and low document count

**Require human approval before execution:**
- Activating new taxonomy nodes (false positive rate 20–40% makes autonomous activation unacceptable)
- Merging existing nodes (requires naming decision and understanding of organizational context)
- Renaming nodes (downstream tagging breaks if IDs are string-based and not migrated correctly)
- Splitting nodes (requires determining where the semantic boundary is — inherently ambiguous without domain knowledge)

**The lightweight governance process (not a "governance meeting"):**
The minimum viable review process based on practitioner research is a pull-request-style workflow:
1. System generates a suggested change (new node, merge, split)
2. Change goes to a review queue visible to a designated taxonomy reviewer (tenant admin or Klai support)
3. Reviewer approves or rejects with a brief rationale (single click for clear cases, text field for complex ones)
4. Approved changes are applied; rejected changes are logged

Estimate per Argilla/Prodigy active learning research: a single reviewer spending 2–4 hours per quarter can maintain quality for a corpus of thousands of documents using active learning to surface only uncertain edge cases.

### 6.6 Technology selection

**BERTopic** remains the best-supported open-source option. Configure explicitly:
- Fix `random_state` in UMAP for reproducibility
- Tune `min_cluster_size` per corpus (start at 1–2% of document count)
- Use LLM representation model for labels, not raw c-TF-IDF keywords
- Do not rely on `.partial_fit()` online learning for production taxonomy evolution

**FASTopic** (2024): benchmarks show better coherence and stability scores than BERTopic on multiple datasets with significantly faster inference. Worth evaluating as the primary discovery model for production — less battle-tested but the performance case is credible.

**Argilla** (open source): the most practical tool for the human review queue. Supports active learning (surfaces uncertain classifications first, not random samples), integrates with Python pipelines, and has a UI accessible to non-technical reviewers. This is the interface through which taxonomy proposals and classification reviews flow.

### 6.7 Quality metrics

| Metric | What it measures | Reliability |
|---|---|---|
| Coverage (1 − outlier rate) | % of corpus assigned to non-noise clusters | High — directly measures taxonomy fitness |
| NPMI / C_V coherence | Semantic consistency of top words within topics | Moderate — correlates with human judgment but can be gamed |
| Topic diversity | Proportion of unique words across topics | Useful for detecting micro-topic overfitting |
| Contextualized Topic Coherence (CTC, 2024) | LLM-simulated human coherence judgment | Better correlation with human eval than NPMI; computationally expensive |

**The evaluation gap that remains:** No single automatic metric reliably predicts whether a taxonomy will produce *better retrieval* in production. Amazon's QuaIIT system (2024) found only 35% of auto-generated topics were rated consistent by human evaluators after quality filtering. Human sign-off on production taxonomy is still required — these metrics support the decision, they do not replace it.

---

## 7. Retrieval Architecture

> **Engineering reference:** [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) — verified against the live codebase on core-01.

### 7.1 Six-step retrieval pipeline

> **Correction (2026-06-08).** The numbered retrieval pipeline runs **inside
> `retrieval-api`** (`retrieval_api/api/retrieve.py` + `api/ranking.py` +
> `api/page_context.py`), not inside the LiteLLM hook. The hook performs an
> independent Step-0 query rewrite (`klai_kb_query_rewrite`) and then calls
> `POST /retrieve`; retrieval-api owns coreference (skipped when the caller
> pre-resolved), embedding, gate, hybrid+graph search, rerank, and evidence scoring.
> The engineering reference for the live pipeline is
> [`knowledge-retrieval-flow.md`](knowledge-retrieval-flow.md).

The retrieval pipeline (conceptually) runs before every chat message reaches the model. It has six steps:

```
User message
  │
  ▼
Step 1: Coreference resolution
  klai-fast rewrites the query to be self-contained
  ("what about the second one?" → "what about plan B pricing?")
  │
  ▼
Step 2: Parallel embeddings
  BGE-M3 dense (gpu-01) + BGE-M3 sparse (bge-m3-sparse sidecar)
  computed in parallel
  │
  ▼
Step 3: Retrieval gate
  Trivial check: skip retrieval if < 8 chars or matches greeting/ack regex
  │
  ▼
Step 4: Hybrid search in Qdrant (3-leg RRF)
  Leg 1: dense query on vector_chunk   ← semantic meaning of raw text
  Leg 2: dense query on vector_questions (HyPE) ← query-to-question match
  Leg 3: sparse query on vector_sparse  ← exact keywords, error codes
  RRF fusion (k=60), top-20 candidates
  │
  ▼
Step 5: Cross-encoder reranking
  bge-reranker-v2-m3 scores query+chunk pairs together
  Top 5 chunks selected for context injection
  │
  ▼
Step 6: Evidence tier scoring (shadow mode)
  Adjusts scores by content_type weight + temporal decay
  Currently logs only — does not reorder results (EVIDENCE_SHADOW_MODE=true)
  │
  ▼
System message injection → model
```

Hybrid search is not optional for B2B knowledge: error codes, product names, and exact procedure steps are keyword matches that pure semantic search misses.

### 7.2 Multi-language support

BGE-M3 covers 100+ languages with a shared embedding space. A Dutch query finds English documents and vice versa. No separate indexes per language are required.

For extraction from Dutch transcripts: do not translate to English before inference. Research on Dutch clinical reports (Builtjes et al., JAMIA Open 2025) confirms that machine translation before inference consistently degrades performance — native Dutch inference is better.

### 7.3 Cross-encoder reranking

For gap detection (batch processing, no latency requirement), add a cross-encoder reranking step after bi-encoder retrieval:

```
Bi-encoder retrieval (top-20) → Cross-encoder reranking (top-5) → LLM classification
```

The cross-encoder reads query + document together and assesses relevance at claim level, not topic level. This is what distinguishes "Windows vs. Mac" when the topic is the same but the coverage is not.

### 7.4 Evidence-weighted scoring [LIVE — shadow mode]

> Research backing: [Evidence-Weighted Knowledge Retrieval: Research Synthesis](../research/README.md). Activation track: [`SPEC-EVIDENCE-001-FOLLOWUP-001`](../../.moai/specs/SPEC-EVIDENCE-001-FOLLOWUP-001/spec.md). User-facing flow: [knowledge-retrieval-flow.md § Evidence tier scoring](knowledge-retrieval-flow.md#step-6-evidence-tier-scoring-shadow-mode).

Evidence-weighted scoring runs in Step 6 of the retrieval pipeline. Currently in shadow mode (`EVIDENCE_SHADOW_MODE=true`): scores are computed and logged but do not reorder results. This is a **post-retrieval scoring adjustment**, not a replacement for semantic search.

**Four scoring dimensions (multiplicative, each per-dimension feature-flagged):**

| Dimension | Signal | V1 approach | Feature flag |
|---|---|---|---|
| Content type | `content_type` (kb_article, pdf_document, web_crawl, ...) | Per-type weight, see table below | `EVIDENCE_CONTENT_TYPE_ENABLED` (default `true`) |
| Temporal decay | `ingested_at` age | Step function across <30d / 30-180d / 180-365d / >365d brackets | `EVIDENCE_TEMPORAL_DECAY_ENABLED` (default `true`) |
| PageRank | `entity_pagerank_max` from FalkorDB | `1 + 0.20 * log1p(pagerank * 100)` — capped ~+25% | `EVIDENCE_PAGERANK_ENABLED` (default `true`) |
| Assertion mode | `assertion_mode` (factual, procedural, ...) | **Conservative profile** (factual/procedural 1.00, quoted 0.98, unknown 0.97, belief 0.95, hypothesis 0.90) — go-live via `SPEC-EVIDENCE-002` | `EVIDENCE_ASSERTION_MODE_ENABLED` (default `true`; shadow mode keeps it measured-not-served) |

> **Intended vs. current (GAP-EVID-01/02, updated 2026-06-11).** The whole evidence-tier
> remains shadow-mode (`EVIDENCE_SHADOW_MODE=true`: weighted order is computed and
> logged as `shadow_eval`, the flat reranker order is served). Two corrections to the
> earlier callout:
> - `_assertion_weight()` no longer returns a constant: it reads the conservative
>   profile above from `DEFAULT_EVIDENCE_PROFILE` (`evidence_tier.py:43-67,125-148`;
>   spread 0.10 per the weights research, unmapped modes fall back to `unknown`).
>   Still measured-not-served while shadow mode is on.
> - The deciding RAGAS A/B (SPEC-EVIDENCE-001-FOLLOWUP-001 variants
>   `evidence_tier_full` / `evidence_tier_temporal_only`) was **never built** — the eval
>   harness only knows `baseline` — and the SPEC's 30-day decision deadline has lapsed
>   while the shadow `deepcopy + apply()` CPU cost is paid per request.
>
> The 3-into-5 epistemic grouping from §3.2 (assertion / speculation / procedure) is
> still **not implemented anywhere**. content_type / temporal / pagerank are real
> multipliers (also shadow-served).

After scoring, chunks are reordered into a **U-shape** (strongest at position 0, second-strongest at the last position, mid-strength clustered in the middle) per Liu et al. 2023 "Lost in the Middle" ([arXiv:2307.03172](https://arxiv.org/abs/2307.03172)).

**Formula:**

```
final_score = reranker_score × content_type_weight × assertion_mode_weight × temporal_decay × pagerank_weight
```

**Content type weights (production defaults):**

| Content type | Weight |
|---|---|
| `kb_article` | 1.00 |
| `pdf_document` | 0.90 |
| `meeting_transcript`, `1on1_transcript` | 0.80 |
| `graph_edge` (Graphiti facts) | 0.70 |
| `web_crawl` | 0.65 |
| `unknown` (fallback) | 0.55 |

**Activation track (post-audit, SPEC-EVIDENCE-001-FOLLOWUP-001):** the shadow mode has been the default since 2026-03-30. The follow-up SPEC sets a 30-day deadline to choose one of four terminal states using a 7-day RAGAS A/B (`baseline` vs. `evidence_tier_full` vs. `evidence_tier_temporal_only`):

1. **Activate** — staged 5%/50%/100% rollout with auto-revert on quality regression
2. **Activate temporal-only** — narrow rollout of the dimension with strongest theoretical justification
3. **Decommission** — remove `evidence_tier.apply()` + payload fields, free up CPU
4. **Retain-flags-off** — `EVIDENCE_SHADOW_MODE=disabled` becomes the new default, code preserved

Decision criteria: ≥+0.02 mean uplift on RAGAS Context Precision **and** Faithfulness with Wilcoxon paired `p<0.05` against baseline on the 50-curated query subset.

**Assertion mode weight activation** is governed by a separate SPEC (`SPEC-EVIDENCE-002`) — it remains flat-1.00 in V1 even after the FOLLOWUP-001 decision because the multiplicative compounding risk (4 dimensions at ~85% classifier accuracy → 48% chance of misclassification per chunk) demands its own calibration cycle. See [Assertion Mode Weights research](../research/assertion-modes/assertion-mode-weights.md).

**Corroboration scoring: deferred.** Cross-source corroboration requires three prerequisites not yet met: near-duplicate detection (SemHash), source-level grouping, and entity resolution validation (>90% precision, >85% recall). See [Corroboration Scoring research](../research/corroboration/corroboration-scoring.md).

**What is deliberately not built:**
- Conflict detection between chunks (ACL 2025: no reliable method exists)
- Confidence calibration (ICLR 2020: systematically miscalibrated without calibration step)
- User-facing confidence labels (CHI 2024, ACL 2024: don't improve decisions, damage trust)

---

## 8. Gap Detection

Gap detection is the mechanism by which Klai Knowledge identifies what an organization's knowledge base does not cover, based on what users actually ask.

> **Intended design vs. current implementation (GAP-LOOP-01..05).** §8.2-8.4 below
> describe the **target** design. The shipped system is considerably simpler — this is
> the single largest ambition-vs-reality gap in the platform and the core of the
> "self-improving knowledge base" roadmap. What is live today:
> - Detection is a **2-class threshold classifier** (`classify_gap` in
>   `deploy/litellm/klai_retrieval_telemetry.py`): `hard` (no chunks) / `soft` (all
>   reranker scores below `KLAI_GAP_SOFT_THRESHOLD`) / `None`. **No LLM judge**, no
>   `covered|partial|new` verdict, no `missing_aspects`.
> - Only **signal #3** (low chat-retrieval confidence) is wired. The transcript /
>   unresolved-ticket signals (#1, #2) do not feed the registry.
> - The "registry" is the Postgres table `portal_retrieval_gaps` (one row per
>   occurrence); "frequency" is a read-time `COUNT() GROUP BY query_text` (exact-string).
>   **No Qdrant `org_{uuid}_gap_registry`** and no embedding-based semantic dedup.
> - Lifecycle is a single `resolved_at` timestamp — **no** `in_progress`, **no**
>   `resolving_article_id`, **no** re-open-after-≥3.
> - Editorial priority is `frequency_per_day` buckets — **no** `urgency_weight` /
>   `recency_factor`.
> - The editorial inbox itself **is** live (`/app/gaps`, `app_gaps.py`).
>
> See [`product-gaps-backlog.md`](product-gaps-backlog.md) §Cluster 1 for the full delta.

### 8.1 What constitutes a gap

A knowledge gap is a pattern of questions or problems that cannot be satisfactorily answered from the current knowledge base. Three signals trigger gap candidates:

1. **Unanswered questions** — extracted from transcripts: questions the agent could not answer
2. **Unresolved conversations** — conversations marked `resolved: false`
3. **Low retrieval confidence** — AI chat responses where top retrieved chunk similarity falls below a threshold

### 8.2 Gap detection pipeline (target design — not yet built)

```
Knowledge item (from transcript extraction or chat log)
  ↓
BGE-M3 embedding
  ↓
Qdrant hybrid ANN search → top-5 existing knowledge items
  ↓
Phase 1 (fast): cosine similarity threshold
  > 0.90 → COVERED (skip)
  0.60–0.90 → Phase 2
  < 0.60 → NEW gap (direct to registry)
  ↓
Phase 2 (accurate): LLM-as-judge (Claude Haiku)
  Input: knowledge item + top-3 chunks
  Output: { verdict: covered|partial|new, missing_aspects: [...], article_id }
  ↓
Gap registry (Qdrant collection: org_{uuid}_gap_registry)
  New gap → insert
  Existing gap → increment frequency, update last_seen
```

**Known limitations of cosine thresholds:** ICLR 2025 and recent RAG surveys confirm cosine similarity produces unreliable results for specific domain terms, error codes, and product names. The thresholds above (0.60/0.90) are starting points. Every tenant deployment requires calibration on real data.

### 8.3 Gap lifecycle (target design — current model is binary open/resolved)

Gaps follow the same superseded_by philosophy as knowledge in general:

```
status: open → in_progress → resolved
```

Resolved gaps are not deleted. They carry a `resolving_article_id` pointing to the synthesis that addressed them. If the same problem reappears in ≥3 new conversations after a gap is resolved, it is re-opened with full history.

This creates an audit trail: "Gap G47 was first detected 2026-01-14, resolved 2026-02-28 by article X, re-opened 2026-03-12 because the article was too generic."

### 8.4 Gap output for editors (target design — current priority is frequency-only)

The gap registry feeds a prioritized editorial inbox:

```
priority_score = frequency × urgency_weight × recency_factor

urgency_weight:
  error code present → 2.0
  escalation → 1.5
  normal → 1.0
```

The editorial inbox shows each gap with: gap type, target article, frequency, what is missing, and example agent responses from real conversations.

---

## 9. AI Interface

### 9.1 Principles

The AI is a lens on organizational knowledge, not an owner of it.

- **AI retrieves and presents; humans judge** — AI never asserts organizational truth autonomously
- **AI suggests metadata; humans accept or reject** — generated_metadata and human_metadata are kept separate in the knowledge record
- **AI drafts; humans authorize** — AI may draft a new article from a gap cluster, but it cannot publish without human approval
- **The knowledge stays in open formats** — markdown + YAML frontmatter, Git-backed, readable without any AI tool

### 9.2 MCP integration

The AI interface (Claude or a local model) connects via MCP (Model Context Protocol). The MCP server exposes semantic operations, not raw vault access.

**The MCP server resolves org and user identity from the authenticated Zitadel session** — tools do not accept `org_id` or `user_id` as caller parameters. This prevents scope confusion and cross-user writes. Every tool call is implicitly scoped to the caller's org and (where relevant) their personal scope.

**Read tools (target surface):**
```
search(query, scope: "org" | "personal" | "both", type?, time_range?)
related_concepts(concept_id, depth)
belief_evolution(topic, from_date, to_date)
provenance_chain(claim_id)
recent(days, type, scope: "org" | "personal" | "both")
```

> **Intended vs. current (GAP-MCP-01).** Only **one** read tool is implemented today:
> `search_knowledge(query, top_k)` in `klai-knowledge-mcp/main.py`, and it hardcodes
> `scope: "both"` — the `scope` / `type` / `time_range` parameters above are not exposed.
> `related_concepts`, `belief_evolution`, `provenance_chain`, and `recent` are **not
> built** (they exist only in this doc). These are precisely the tools that would expose
> the graph / temporal / provenance depth of the §3 model to an external agent.

**Write tools — `klai-knowledge-mcp` (deployed 2026-03-21, org scope added March 2026):**

Saves from LibreChat are handled by a dedicated MCP server with streamable-http transport. Three tools are exposed:

```
save_personal_knowledge(title, content, assertion_mode, tags, source_note?)
save_org_knowledge(title, content, assertion_mode, tags, source_note?)
save_to_docs(title, content, kb_slug, assertion_mode, tags, source_note?)
```

Identity: `X-User-ID` + `X-Org-Slug` headers sent by LibreChat.
Auth: internal service token (`KNOWLEDGE_INGEST_SECRET`).
Storage: POSTs directly to `{KNOWLEDGE_INGEST_URL}/ingest/v1/document` — **not** via klai-docs API.
Qdrant indexing: **live** — saves are ingested and indexed immediately by the knowledge-ingest pipeline.

`search_knowledge` returns source `artifact_id`; `save_to_docs` can store those
UUIDs in `derived_from`; ingest validates them for the same org and writes
parent→child rows to `knowledge.derivations`. Human-authored source attribution
can still use `source_note`.

**LibreChat config** (per tenant in `librechat.yaml`):
```yaml
mcpServers:
  klai-knowledge:
    type: streamable-http
    url: http://klai-knowledge-mcp:8080/mcp
    headers:
      X-User-ID: "{{LIBRECHAT_USER_ID}}"
      X-Org-Slug: "${KLAI_ORG_SLUG}"   # set per tenant in LibreChat .env
    mcpSettings:
      allowedDomains:
        - klai-knowledge-mcp
```

Implementation: `klai-knowledge-mcp/main.py`
Agent system prompt: `klai-knowledge-mcp/agent-system-prompt.md`

This design is model-agnostic — the write layer is independent of which model drives the agent.

**Known risk:**

*Service token scope* — `KNOWLEDGE_INGEST_SECRET` is a shared symmetric secret. If the MCP
container is compromised, an attacker can write to any user's personal KB by supplying any
`X-User-ID` value. Mitigation path: replace with a Zitadel machine user token (scoped, rotatable,
auditable). Acceptable for V1 on an internal Docker network; must be addressed before the MCP
server is exposed to an untrusted network.

### 9.3 Routing: RAG vs. structured queries

Two complementary mechanisms determine whether a question goes to Qdrant (semantic) or PostgreSQL (analytical):

**Hardcoded router (for known patterns):**
Analytic signals ("how many", "most common", "trend", "rate") → PostgreSQL (`knowledge` schema)
Content signals ("what does", "how does", "explain", "steps for") → Qdrant
Ambiguous → both

**Tool use (for conversational interfaces):**
Provide the LLM with two tools with clear descriptions. The model selects based on question intent. Both results can be combined in a single response.

### 9.4 Grounded response format

All AI responses must cite sources. The response format includes:
- Answer text
- Source list: `{ title, url, excerpt }` per cited chunk
- Handoff option: `true` if retrieval confidence is below threshold or if user requests human escalation

If an answer is not found in retrieved sources, the system explicitly says so. It does not hallucinate.

### 9.5 LibreChat automatic context injection via LiteLLM pre-call hook

**Status: deployed March 2026. Hook is live and verified for the `getklai` tenant.**

Every LibreChat chat message is automatically enriched with relevant organizational knowledge before it reaches the model. This is transparent to the user and to LibreChat.

#### Architecture

```
LibreChat (per-tenant container)
  → POST /chat/completions  (with team-scoped API key)
  → LiteLLM proxy
      → async_pre_call_hook (KlaiKnowledgeHook)
          1. Extract user_id from data["user"] — skip if absent
          2. Skip if message is trivial (short, ack, greeting)
          3. _get_kb_feature(user_id, org_id) — fail-closed: skip if not enabled
          4. scope = "both" if kb_personal_enabled else "org"
          5. kb_slugs = kb_slugs_filter (None = all org KBs)
          6. POST {KNOWLEDGE_RETRIEVE_URL}/retrieve  (timeout: 3s, graceful degrade)
             — sends conversation_history for coreference resolution
          7. Inject chunks as system message prefix
          8. _classify_gap(chunks) + _fire_gap_event(...)  (fire-and-forget)
      → model (Mistral / Ollama fallback)
  → response back to LibreChat
```

The hook is a `CustomLogger` subclass mounted as a Python file into the LiteLLM container. No custom image build required.

#### Retrieval API interface

The hook calls `POST {KNOWLEDGE_RETRIEVE_URL}/retrieve` on the `retrieval-api` service (env var). Scope and user identity are derived from the feature gate result:

```
POST /retrieve
Authorization: Bearer <internal-service-token>

{
  "query": string,                  // last user message text
  "org_id": string,                 // Zitadel org ID
  "scope": "org" | "both",          // from feature gate (kb_personal_enabled)
  "user_id": string | null,         // for personal scope filtering
  "kb_slugs": [...] | null,         // null = all org KBs; from kb_slugs_filter
  "conversation_history": [...],    // for coreference resolution
  "top_k": 5
}

→ 200 OK (always — empty chunks = no relevant context)
{
  "chunks": [{"text": string, "source": string, "score": float, "metadata": {...}}],
  "total_tokens": int
}
```

The retrieval-api applies 3-leg RRF fusion (vector_chunk + vector_questions + vector_sparse) and returns ranked chunks. The hook injects the top results as a system message prefix.

#### Token budget

Injected knowledge context is capped at **2,000 tokens** per request. Rationale:

| Budget item | Allocation |
|---|---|
| LibreChat system prompt | ~500 tokens |
| Conversation history | ~8,000 tokens |
| User message | ~200 tokens |
| Model response headroom | ~2,000 tokens |
| **Injected knowledge context** | **2,000 tokens** |

Truncation: server-side in the retrieval API. Chunks ranked by score. Greedily fill from top score down. If cumulative tokens exceed 2,000: stop, optionally trim last chunk at word boundary. Target chunk size at ingestion: 400–500 tokens (docling-serve `HybridChunker` default).

#### Query classifier

Heuristic only — no LLM call:
1. `len(query) < 8` → skip retrieval
2. Regex match against trivial patterns (acks, greetings, continuations in NL/EN) → skip
3. Otherwise → retrieve

False positive cost: one 2s API call. False negative cost: worse answer. Classifier calibrated toward retrieval.

#### Tenant isolation via LiteLLM team keys

Each LibreChat tenant has a **LiteLLM team-scoped key** instead of the master key. The hook reads `org_id` from the key's `metadata` field. Master key usage → no `org_id` in metadata → hook skips retrieval.

Migration required for existing tenants: see §9.5 provisioning changes.

#### Provisioning changes

At tenant creation, provisioning adds a step:
1. `POST /team/new` — creates a LiteLLM team (alias = org slug)
2. `POST /key/generate` — creates a team key with `metadata: {org_id: "<zitadel_org_id>"}` and `models: ["klai-primary", "klai-fast", "klai-large"]` (the user-facing LiteLLM tier aliases — internal-only aliases like `klai-medium` and `klai-bge-m3` are excluded from tenant keys)
3. The returned key replaces `settings.litellm_master_key` as `LITELLM_API_KEY` in the LibreChat `.env`

`zitadel_org_id` is used (not the integer PK) — it is the stable cross-system identifier available on `PortalOrg.zitadel_org_id`, and the correct key for Qdrant scope `org_{zitadel_org_id}`.

`klai-infra/core-01/litellm/config.yaml` change:
```yaml
litellm_settings:
  callbacks:
    - klai_knowledge.KlaiKnowledgeHook
```

Hook file: `klai-infra/core-01/litellm/klai_knowledge.py`, mounted read-only at `/app/custom/` with `PYTHONPATH=/app/custom` in the container env.

#### Graceful degradation

Any failure in `async_pre_call_hook` (timeout, connection refused, 5xx, any exception) logs a WARNING and returns `data` unchanged. LibreChat receives a normal response. This is the expected behavior when the Knowledge Service is not yet deployed.

---

## 10. Multi-tenancy, Personal Knowledge, and Federated Knowledge

### 10.1 Structural isolation between organizations

Each organization occupies an isolated tenant scope in Qdrant (via `tenant_id` payload, see §5.1) and an isolated schema/rows in PostgreSQL keyed by `org_id`. Data from organization A is structurally unreachable by organization B. This is enforced at the API routing layer: every query includes a mandatory org filter; there is no API surface that crosses org boundaries.

**Gitea:** one Git repository per organization for human-authored knowledge. Organization A's repo is not accessible to organization B.

### 10.2 Personal knowledge: hard isolation within an org

Every user in an organization has access to two distinct knowledge scopes:

| Scope | `tenant_id` in Qdrant | `org_id` / `user_id` in PostgreSQL | Accessible by |
|---|---|---|---|
| **Org knowledge** | `org_{org_uuid}` | `org_id = X`, `user_id = NULL` | All org members |
| **Personal knowledge** | `user_{org_uuid}_{user_uuid}` | `org_id = X`, `user_id = Y` | That user only |

**The isolation is hard, not soft.** There is no API route that an org admin, org owner, or any other org member can use to query another user's personal knowledge scope. This is enforced at the retrieval API layer, not by access control configuration. The org has no window into personal knowledge, period.

**Personal knowledge in V1 is explicitly scoped:**
- Notes the user creates themselves via the editor or LibreChat (`klai-knowledge-mcp`)
- No helpdesk transcript extraction, no document ingestion from org sources flowing into personal scope
- Not processed by the contextual retrieval or HyPE enrichment pipeline (BGE-M3 direct embedding only — avoids LLM processing of personal content until the legal basis is formally established)
- Not processed by any third-party cloud LLM — self-hosted pipeline only, consistent with the platform-wide no-external-LLM rule

> **Intended vs. current (GAP-PRIV-01) — compliance-relevant.** The "personal content
> skips LLM enrichment" carve-out above is **not implemented**. The ingest enqueue path
> gates enrichment only on chunk count + org config (`enrichment_policy.py` has no
> `user_id`/personal branch), so personal artifacts go through the same contextual-prefix
> + HyPE + Graphiti pipeline as org knowledge. A one-line scope check in the enqueue path
> would honour the documented posture. **Validate the intended policy with legal/privacy
> before changing code** — the doc's carve-out may or may not still be the desired rule.

**What personal knowledge is not:**
- A staging area for org knowledge (though it can be used that way voluntarily — see §10.4)
- Visible to org administrators under any normal circumstance
- Subject to org-level gap detection, taxonomy, or analytics

**Gitea storage layout for personal knowledge (decided 2026-03-21):**

One shared `personal` KB per org in Gitea. User content is stored at:
```
personal/
  users/{user_uuid}/{slug}-{uuid_prefix}.md
```

Rationale: zero per-user repos (avoids Gitea repo proliferation at scale), clean namespace
separation, compatible with the existing klai-docs path API. The `user_{org_uuid}_{user_uuid}`
Qdrant scope mirrors the same user isolation without requiring separate Gitea repos.

The `personal` KB is created at tenant provisioning time (one per org). No per-user provisioning
is required — the MCP server writes to `users/{user_uuid}/` paths which are created on first save.
Access control: each page is written with `edit_access: owner` (only the owning user can edit via
the portal UI). The klai-docs API enforces this via `db.getPageEditRestriction`.

### 10.3 Visibility model within org knowledge

Individual org knowledge artifacts carry a `visibility` field:

| visibility | Accessible to |
|---|---|
| `internal` | Org members only |
| `external` | Logged-in external users (e.g., customers with accounts) |
| `public` | Everyone (anonymous access) |

Visibility filtering is a second access-control layer on top of org isolation. Org routing handles org-to-org isolation; visibility handles interface-level access within an org. Personal knowledge has no visibility field — it is always and only accessible to the owning user.

### 10.4 Retrieval scope and attribution

When a user queries Klai Knowledge, the retrieval layer runs two parallel lookups:

1. `user_{org_uuid}_{user_uuid}` scope — personal knowledge
2. `org_{org_uuid}` scope — org knowledge

Results are merged (Reciprocal Rank Fusion) and passed to the LLM with explicit source attribution per chunk:

```
source_scope: personal   → "From your notes: ..."
source_scope: org        → "From [Org] knowledge base: ..."
```

**Guard:** Org-facing interfaces (public KB site, customer-facing chat widget, any interface outside the user's own session) only query `org_{org_uuid}`. Personal knowledge never surfaces to external users, never surfaces in org-admin views, never surfaces in usage analytics or gap detection.

### 10.5 Promoting personal knowledge to org knowledge

A user can voluntarily contribute a personal note to the org knowledge base. This is an immutable promotion — a clean copy, not a sync or a reference:

1. User triggers "Contribute to org KB" on a personal note
2. A new `knowledge_artifact` is created in `org_{org_uuid}` scope with:
   - `provenance_type: synthesized`
   - `synthesis_depth: 1` (direct user contribution)
   - `derived_from: [{parent_id: personal_note_uuid}]` — audit trail
   - `author: user_uuid` — attribution preserved
3. The personal note is marked `contributed_to_org: true` (informational only — it is not linked live)
4. The org artifact is independent from creation. No sync. No bidirectional reference. The org owns it.

The user's personal note is not deleted or modified. The org artifact evolves independently.

### 10.6 User offboarding

When a user leaves an organization:
- Their personal knowledge scope (`user_{org_uuid}_{user_uuid}`) is offered for export (markdown archive)
- After export window: `DELETE` from Qdrant by `tenant_id = user_{org_uuid}_{user_uuid}`, `DELETE` from PostgreSQL `WHERE user_id = Y AND org_id = X`
- Org knowledge artifacts they contributed remain in org scope (attributed to them by `author` field, not by personal scope)
- GDPR right-to-erasure for personal data: single operation, fully satisfiable without touching org knowledge

### 10.7 Cross-organizational knowledge sharing

**[DECISION NEEDED]** Two scenarios require a design decision:

1. **Klai-maintained shared knowledge** — a `klai_shared_baseline` tenant scope accessible to all orgs (e.g., generic best practices). Requires Klai governance for this scope.

2. **Opt-in tenant federation** — organizations share specific knowledge items with other organizations. Architecturally feasible, operationally complex. Defer to V2.

---

## 11. Publication Layer

Human-authored knowledge is stored in Git (Gitea, self-hosted), edited via BlockNote in the browser, and published as a Next.js knowledge base site.

```
BlockNote editor (browser)
  → serialize to markdown + YAML frontmatter
  → commit via Gitea API
  → Gitea webhook → Unified Ingest API (re-index changed pages)
  → Next.js KB site (reads from Gitea via API, renders via SSR)
```

### 11.1 Content structure

```
help-center/
  _meta.yaml                       ← navigation order + labels
  getting-started/
    _meta.yaml
    quick-start.md
    installation.md
  integrations/
    zapier.md
    api.md
```

Navigation = folder structure. `_meta.yaml` per folder defines display name and child order. No separate navigation config required.

### 11.2 Access control

| KB setting | Who can read |
|---|---|
| `public` | Anonymous internet |
| `private` | Organization members (Zitadel OIDC) |

Per-article write access is set in YAML frontmatter (`edit_access: org` or `edit_access: [user-id, ...]`).

### 11.3 MDX vs. remark-directive

For rich content (tabbed code blocks, callouts), the recommendation is `remark-directive` over MDX:
- BlockNote does not serialize to MDX (no native serializer, not on roadmap)
- MDX JSX syntax breaks the docling-serve HybridChunker (RAG pipeline fails on `<Tab>` components without custom preprocessing)
- `next-mdx-remote` compiles MDX to executable JavaScript server-side — a code execution surface for tenant-authored content

`remark-directive` provides 80% of MDX's value for B2B knowledge bases (tabbed content, callouts, callout types) with none of the RAG pipeline breakage. Reserve MDX for cases with a concrete need for interactive, data-driven components.

---

## 12. The Self-Improving Loop

The core value proposition of Klai Knowledge is that it improves continuously without requiring constant manual curation.

```
Users ask questions (chat widget, internal tools)
  ↓
Low-confidence answers → gap candidates
Helpdesk transcripts → gap candidates
  ↓
Gap registry (aggregated, deduplicated, prioritized)
  ↓
Editorial inbox (human reviews, selects gaps to address)
  ↓
Human writes or AI drafts new/updated article
  ↓
Article published → Gitea commit → Unified Ingest API
  ↓
Knowledge base re-indexed → better retrieval
  ↓
Fewer low-confidence answers → fewer gaps → smaller inbox
```

The human is in the loop at the editorial step only. The detection, aggregation, prioritization, and re-indexing are automated. Humans decide what to write, not whether to write.

This loop also closes the gap between what an organization knows (formal knowledge base) and what its people know (tacit knowledge in transcripts, meeting notes, emails). Over time, tacit knowledge crystallizes into explicit knowledge.

> **Intended vs. current (GAP-LOOP-05/06).** Two arms of this loop are not yet built:
> the **"helpdesk transcripts → gap candidates"** input does not exist (only low-confidence
> chat retrieval feeds the registry — scribe transcripts are ingested as documents but emit
> no gap events), and there is **no AI-draft** affordance (`grep 'draft'` over the portal
> backend = 0) — every article is authored by hand in BlockNote. The detection/aggregation
> side is also simpler than "aggregated, deduplicated, prioritized" implies (see §8 callout:
> exact-string grouping, frequency-only priority). The editorial inbox and re-index arms
> are live.

---

## 13. Open Questions Requiring Resolution

| § | Question | Status |
|---|---|---|
| 13.1 | Taxonomy evolution | Researched — findings in §6 |
| 13.2 | Bi-temporal query infrastructure | Researched — V1 achievable with Qdrant + PostgreSQL simplification |
| 13.3 | Graph layer decision | Researched — deferred; gate condition defined |
| 13.4 | Epistemic labeling automation | Researched — 3-way V1 model + weights guidance |
| 13.5 | Cross-organizational knowledge federation | **Decision needed** |
| 13.6 | Enrichment and extraction LLM | Decided — Mistral Small 3.2 (128K) + Qwen3-8B (fast extraction) |
| 13.7 | Editor gap | Known limitation — no short-term resolution |
| 13.8 | Web search backend | **Deployed** — SearXNG (March 2026) |
| 13.9 | Whisper/transcription → Knowledge pipeline | **Open** |

### 13.1 Taxonomy evolution [RESEARCHED — see §6]

Completed. Full findings integrated in §6. Summary: "self-managing taxonomy" claim is significantly overstated for B2B. BERTopic requires human approval gate; minimum viable corpus ~1,000 documents; online learning causes semantic drift by design. Tiered approach by tenant size is the recommended architecture.

### 13.2 Bi-temporal query infrastructure [RESEARCHED — achievable in V1 with simplification]

**The basic use cases are achievable with the existing Qdrant + PostgreSQL stack using a simplified model.** No additional infrastructure required for V1.

**V1 simplified model (drop world_time entirely):**

```yaml
belief_time_start: "2024-01-15"     # ISO date
belief_time_end: "9999-12-31"       # sentinel for "currently active"; never null
system_created_at: "2024-01-16T09:32:00Z"
supersedes: null
```

Using a sentinel (`"9999-12-31"`) instead of NULL for active items makes every Qdrant range query a simple `lte`/`gte` without the `IsNullCondition` complexity and eliminates a known Qdrant `DatetimeRange` bug (issue #5641, December 2024). Store timestamps as Unix epoch integers in Qdrant payloads — fully reliable across all versions.

**Queries that work in V1:**

| Query | Execution |
|---|---|
| "What did we know about X on date D?" | Qdrant semantic search + `belief_start_ts <= D AND belief_end_ts >= D` |
| "Which items became outdated in last 6 months?" | PostgreSQL: `WHERE belief_time_end < NOW() AND belief_time_end != '9999-12-31' AND belief_time_end >= NOW() - 6 months` |
| "Evolution of position on topic Y over 2 years" | PostgreSQL: `WHERE topic_id = ? ORDER BY belief_time_start` |
| "All currently active items" | Qdrant: `belief_end_ts = 9999-12-31` as exact match, or PostgreSQL `WHERE belief_time_end = '9999-12-31'` |

**What requires V2 custom work:**
- Automatic temporal contradiction detection (Graphiti-style: LLM invalidates contradicted edges at ingest)
- `FOR PORTION OF` interval splits (splitting a valid-time interval mid-range — PostgreSQL doesn't support this natively yet)
- Temporal joins across knowledge items ("items simultaneously active with item X")
- Full transaction-time audit trail (system-time history table via `temporal_tables` extension or XTDB)

**Reference implementations:**
- [Graphiti/Zep](https://github.com/getzep/graphiti) — open source, four timestamps per graph edge, LLM-assisted contradiction detection. Best reference for the full bi-temporal knowledge graph vision.
- [XTDB](https://xtdb.com/) — native bi-temporal SQL (MPL-2.0, self-hostable). Correct long-term answer if regulatory audit trail is required. JVM service, adds operational complexity.
- [temporal_tables extension](https://github.com/arkhipov/temporal_tables) — adds system-period versioning to PostgreSQL. Defer to V2.

### 13.3 Graph layer decision [ACTIVE — measuring effect in production]

**The graph layer is live in production on both ingest and retrieval** (`GRAPHITI_ENABLED=true` on both `knowledge-ingest` and `retrieval-api`). See §5.3. The research below is the background for whether it demonstrably improves recall on Klai's query mix — we now measure that directly in production instead of gating activation on it.

Summary of findings:
- LightRAG uses ~100,000 tokens per query (measured, not estimated) — the "90% fewer tokens" claim is inverted
- Graph RAG improves only multi-hop relational queries; B2B knowledge base traffic is predominantly single-hop factual and procedural
- All tested graph RAG implementations regress on simple fact retrieval to varying degrees
- No self-hosted graph RAG system is production-hardened for B2B at this time

**Gate condition:** Sample 200 real queries; if >20% are multi-hop relational, evaluate HippoRAG2 + SpaCy construction before adding any graph infrastructure. Do not build in anticipation of a use case that may not materialize.

### 13.4 Epistemic labeling automation [RESEARCHED — simplified model + weights guidance]

> Full research: [Assertion Modes Research](../research/assertion-modes/assertion-modes-research.md) | [Assertion Mode Weights](../research/assertion-modes/assertion-mode-weights.md)

**The full 4-way classification (quote/paraphrase/interpretation/synthesis) is not solvable at acceptable accuracy with current open-source tools.** The paraphrase/interpretation boundary is genuinely ambiguous — even human annotators disagree in 25–30% of cases without access to the original source text. Do not attempt to automate this distinction in V1.

**Recommended V1 model: 3-way classification**

A simpler 3-way scheme covers most of the epistemic value without the hard boundary problem:
- **Verbatim** — text is identical or near-identical to a source (marked quotes, blockquotes)
- **Attributed** — text reflects human processing of a named source (paraphrase, interpretation of a specific source)
- **Own conclusion** — the author's own synthesis or judgment, not traceable to a single source

**Implementation: tiered approach**

**Tier 1 — Structural parser (zero ML, zero cost):** Parse markdown blockquotes, quotation marks, and citation syntax. Auto-classify as "verbatim" or "attributed." Expected coverage: 20–40% of items at ~90%+ accuracy.

**Tier 2 — Self-hosted LLM classification (remaining items):** Use Mistral Small 3.1 with 5–10 few-shot examples per category and chain-of-thought rationale. Route low-confidence results (below ~0.75) to human review. Expected accuracy: 65–75% (open-weight models perform somewhat below GPT-4o class on this task; gap is smaller with good few-shot examples).

**Tier 3 — Human review via Label Studio:** Label Studio (open source, Apache 2.0) with uncertainty-based active learning — surfaces only low-confidence items, not the full corpus. Research shows uncertainty sampling saves ~66% of annotation effort vs. random sampling. Show LLM chain-of-thought reasoning to reduce reviewer cognitive load.

**Evolution path:** Once 500–1,000 labeled examples are collected, fine-tune `deberta-v3-base` (Hugging Face, open source). This reduces inference cost by ~100× compared to Mistral and improves consistency.

**Retrieval weight guidance (from weights research):**

The [assertion mode weights research](../research/assertion-modes/assertion-mode-weights.md) established:

- **Start with flat weights (all 1.00).** The equal-weighting literature (Einhorn & Hogarth 1975, Graefe 2013) strongly shows flat weights are safest when empirical validation data doesn't exist.
- **Maximum safe spread: 0.20** for an ~85% accurate classifier (minimum weight 0.80 if max is 1.00). Wider spreads cause more harm from misclassification than benefit from correct classification.
- **Multiplicative compounding risk:** With 4 scoring dimensions at ~85% accuracy each, 48% of chunks will have at least one misclassified dimension. This is why flat weights are the safe default.
- **Evaluation gate:** Only widen the spread after 200+ chunk classification evaluation on Klai's own content + A/B retrieval test with Wilcoxon signed-rank paired testing. See [RAG Evaluation Framework](../research/evaluation/rag-evaluation-framework.md).
- **Never show assertion labels to users:** CHI 2024: confidence indicators don't improve task accuracy. ACL 2024: overconfident epistemic markers cause lasting trust damage. ACL 2025: LLMs can't produce calibrated epistemic markers. Use as internal ranking signal only.

**What to defer permanently:**
- Paraphrase/interpretation boundary without source text — unsolvable at useful accuracy
- Cross-document synthesis detection — unsolved research problem at scale
- Fine-grained paraphrase type detection — research-grade, not production-ready

**Open-source constraint note:** Research surfaced Prodigy (Explosion AI) as a leading active learning annotation tool. Prodigy is commercial ($390). Label Studio is the open-source replacement and covers the same active learning workflow.

### 13.5 Cross-organizational knowledge federation [DECISION NEEDED]

Does Klai want to enable knowledge sharing between organizations? If yes, this requires a permission model, governance structure, and conflict-resolution mechanism that does not currently exist. This is V2 at the earliest.

### 13.6 Enrichment and extraction LLM [DECIDED + RESEARCHED]

**Pipeline LLM constraint:** No Anthropic/Claude API anywhere in the pipeline. Mistral API is allowed for non-sensitive enrichment tasks (contextual retrieval prefix, HyPE question generation). Transcript extraction (GDPR-sensitive source data) remains self-hosted only — no cloud API regardless of provider.

**Two tasks that need an LLM:**

| Task | Data sensitivity | Cloud API allowed? | Recommended model |
|---|---|---|---|
| Contextual Retrieval prefix generation | Chunks of org KB content | Yes (Mistral API) | Mistral Small 3.1 via API |
| HyPE question generation | Same as above | Yes (Mistral API) | Mistral Small 3.1 via API |
| Helpdesk transcript extraction | Raw customer call data (GDPR) | No — self-hosted only | Qwen2.5-14B or Mistral Small 3.1 self-hosted |
| Epistemic label classification | Internal KB content | Yes (Mistral API) | Mistral Small 3.1 via API |

**Model selection (researched, IFEval benchmark + RTX 4090 throughput):**

**Primary recommendation: Qwen2.5-14B-Instruct (self-hosted, Q8)**
- IFEval: 81.0 (strong instruction following — the critical requirement for prefix/question generation)
- Throughput: ~64 tok/s on RTX 4090 at Q4; Q8 fits in ~15 GB leaving headroom for batching
- License: Apache 2.0
- Multilingual: strong pretraining including European languages; Dutch is not in the published eval set (a benchmark gap, not a known quality gap — validate on a sample before committing)
- Cost: effectively zero marginal cost once the server is running; breaks even vs. Mistral Nemo API at ~200K chunks/month

**Fallback / ramp-up: Mistral Small 3.1 24B via API**
- IFEval: ~83-85 (slightly higher than Qwen2.5-14B)
- Explicit Dutch language support in model documentation
- API pricing: $0.030 input / $0.110 output per MTok (~$760 per 1M chunks at 500 input + 100 output tokens each)
- Use during early phase when volume does not justify managing inference infrastructure; migrate to self-hosted Qwen2.5-14B once chunk volume exceeds ~200K/month
- Self-hosted Q4 fits in ~14 GB on RTX 4090; throughput ~45-55 tok/s (slower than Qwen2.5-14B)

**What was evaluated and discarded:**

| Model | IFEval | Reason discarded |
|---|---|---|
| Mistral Small 3.2 | No public score | $0.075/$0.200 — 2.5× more expensive than 3.1 with no evidence of proportional quality gain for these tasks |
| Gemma 3 27B | 0.904 | High IFEval but 28-38 tok/s measured throughput and poor overall quality-per-compute; takes 22 GB Q4 leaving minimal KV cache |
| Phi-4 14B base | 0.630 | IFEval 63 is a disqualifier for instruction-following tasks |
| Llama 3.1 8B | 0.804 | Weak Dutch; not suitable when Dutch is a first-class requirement |
| Mistral Nemo 12B | No public IFEval | Viable cost-minimizing fallback ($0.020/$0.040 via API) if quality testing on Dutch content confirms adequacy |
| Mistral 7B v0.3 | Low | More expensive via API than Nemo; weaker quality |

**Validation gate before production commit:** Run 100 representative Dutch chunks through both Qwen2.5-14B and Mistral Small 3.1. Compare prefix coherence and question diversity. This must happen before scale ingestion begins — the Dutch quality gap is a known unknown.

### 13.7 The editor gap [KNOWN LIMITATION]

No existing tool combines: Notion-quality web editor + Git as storage + wikilinks with bidirectional backlinks + markdown-native. BlockNote + Gitea covers most of this but lacks native wikilink support with cross-document backlinks. This is a known limitation accepted for V1. Evaluate whether to build wikilink support into the BlockNote integration or defer.

**Current state:** BlockNote is integrated in `klai-portal` (not klai-docs). The editor was previously in klai-docs but migrated to the portal SPA for a unified Zitadel session flow. Commit `a50797a` in klai-docs contains the standalone version if needed.

### 13.8 Web search backend [DEPLOYED — March 2026]

**Current production state (core-01):**
- SearXNG reconfigured: Google and Bing removed; Startpage + DuckDuckGo active
- Mojeek engine configured but disabled (API key needed to activate; see settings.yml)
- LibreChat webSearch enabled: SearXNG + Firecrawl scraper + Infinity reranker (bge-reranker-v2-m3, CPU)
- Infinity reranker is shared infrastructure — will also serve the Knowledge retrieval pipeline (§7.1) when deployed

The platform currently uses SearXNG (self-hosted, `http://searxng:8888`) for web search in LibreChat. Web search is a user-initiated, opt-in action — not part of the Knowledge ingestion pipeline.

**Platform constraint:** all components must be open-source and privacy-friendly. Cloud APIs that send queries to US companies (Tavily, Brave Search API) are excluded regardless of GDPR compliance claims.

#### The SearXNG problem (quality, not privacy)

SearXNG's privacy posture is fine — self-hosted, queries routed via server IP, not the user's. The problem is reliability. SearXNG has no index of its own; it aggregates upstream engines. Two of its three main sources are now effectively dead on datacenter IPs:

- **Google**: actively blocks datacenter IP ranges via TLS/HTTP2 fingerprinting. Not fixable by rotating IPs. Only 25 of 91 public instances had a working Google engine in 2025/2026.
- **Bing**: shut down its public search API in August 2025. Gone entirely.
- **DuckDuckGo**: still works but applies aggressive rate limits.

**Fix: strip Google and Bing entirely, use engines with independent indexes.** Viable non-blocking engines for SearXNG: Startpage (Google partnership, works from datacenter IPs), DuckDuckGo (rate-limited but functional), and Mojeek (independent crawler, API-based).

#### Landscape of EU privacy-friendly alternatives (researched March 2026)

| Option | Index | Self-hostable | EU/privacy fit | Quality | Verdict |
|---|---|---|---|---|---|
| **SearXNG (reconfigured)** | Metasearch (Startpage + DDG) | Yes (AGPL) | Fine — self-hosted | Medium | Short-term fix |
| **Mojeek API** | Own crawler (9B+ pages) | No (API) | UK company, EU adequacy until 2031, no query retention | Below Google, acceptable for B2B | Best strategic fit |
| **MetaGer (self-hosted)** | Metasearch | Technically yes (AGPL) | German non-profit | Same blocking problem as SearXNG | Not worth the added complexity |
| **Stract** | Own crawler | Yes (Rust) | Danish founder | Not production-ready — no deployment guide, poor quality | Promising future, not today |
| **YaCy** | Federated P2P | Yes (GPL) | Decentralized | Poor for B2B general queries | Wrong tool |
| **Whoogle** | Google proxy | Yes (MIT) | N/A | **Dead since Jan 2025** — Google required JS | Do not use |
| **Common Crawl** | Own crawl data | Theoretical | Neutral | No turnkey product exists | Engineering project, not a solution |

#### Recommended path

**Short term:** Reconfigure SearXNG — disable Google and Bing, enable Startpage + DuckDuckGo + Mojeek (via API engine). This is the lowest-effort fix and keeps everything self-hosted.

**Medium term:** Replace SearXNG with the **Mojeek API** directly. Mojeek is the only option satisfying all platform criteria: independent index (own crawler since 2004, 9B+ pages), UK company with EU adequacy (renewed December 2025, valid until 2031), no query data retained. Quality is below Google for technical queries — validate on a representative sample of Focus queries before committing. Pricing: £1–3 per 1,000 requests.

**Not pursued:** Stract (not production-ready), Whoogle (dead), YaCy (wrong use case), MetaGer self-hosted (same blocking root cause, higher complexity).

### 13.9 Whisper/transcription → Knowledge pipeline [OPEN QUESTION]

`whisper-server` is deployed on core-01 and used by the klai-portal Scribe/Transcribe features (audio → transcript). These transcripts are a natural feed into the helpdesk extraction adapter (§4.3). The connection between the transcription pipeline and the Knowledge ingestion pipeline is not yet designed.

**Open question:** Does the transcription service write transcripts to a store that the Knowledge ingestion pipeline can poll, or does it POST directly to the Unified Ingest API? The answer depends on whether the transcript pipeline is batch (end-of-call) or streaming. Design this interface before building the helpdesk adapter.

### Recording Lifecycle & Cleanup

Vexa meeting recordings are treated as ephemeral data:

1. Recording captured in vexa-bot container at `/data/recordings/` (upstream main v0.10 path post SPEC-VEXA-003; previously `/var/lib/vexa/recordings/` on `bot-manager`).
   In the current deploy `RECORDING_ENABLED=false` so this path is inert — audio is streamed directly to the transcription-service without local persistence.
2. Transcription completed by `transcription-service` (Vexa faster-whisper workers on gpu-01 via gpu-tunnel `172.18.0.1:8000`).
3. Any recording that IS stored would be cleaned up post-transcription (GDPR Art. 5(1)(c) data minimization). Not exercised while `RECORDING_ENABLED=false`.

Implementation:
- Meeting orchestrator: `meeting-api` (replaces the former `bot-manager`/`vexa-meeting-api:klai` internal build) — spawns and tears down `vexa-bot` containers via `runtime-api` and the Docker socket.
- Background cleanup task in portal-api: recordings >30 min old with `status="done"` and `recording_deleted=False`
- Tracked via `VexaMeeting.recording_deleted` and `recording_deleted_at` columns
- Graceful failure: if cleanup fails, transcription pipeline continues

No persistent volume mount for recordings — storage is ephemeral by design.

---

## 14. Technology Stack

| Layer | Component | Notes |
|---|---|---|
| **Enrichment LLM** | Mistral Small 3.2 via API (ramp-up) → Qwen3-8B self-hosted (scale) | No Anthropic API anywhere. Mistral API allowed for non-sensitive enrichment. Transcript extraction self-hosted only (GDPR). See §13.6. |
| **Extraction** | Instructor + Qwen3-8B or Mistral Small 3.2 (both self-hosted) | Self-hosted only for transcript data; no cloud API |
| **Document parsing** | docling-serve (self-hosted) | HybridChunker for token-aware, structure-preserving chunking |
| **V2 external connectors** | Unstructured.io (Apache 2.0) | 30+ native source connectors (Zendesk, Google Drive, Confluence, Slack, SharePoint, Jira). Integrates as a Python library inside `knowledge-ingest` — no extra infrastructure. Each connector becomes an adapter: call Unstructured, forward output to `/ingest/v1/document`. Chosen over Airbyte (operationally heavy, ELv2 license) and LlamaIndex (code-only, no admin UI). Alternative for orgs with complex sync needs: Airbyte (600+ connectors, native web UI, native Qdrant destination, but requires Temporal + multiple containers). |
| **Web crawling** | Crawl4AI | Open source, async, sitemap-aware |
| **Embeddings** | BGE-M3 dense via TEI + sparse via `bge-m3-sparse` sidecar (FlagEmbedding) | Both deployed on core-01. TEI cannot produce BGE-M3 sparse; sidecar handles sparse in a separate HTTP service. |
| **Vector store** | Qdrant (self-hosted, core-01) | `klai_knowledge` collection (org + personal KB). `org_id`, `kb_slug`, `user_id` payload indexes. |
| **Web search** | SearXNG (self-hosted, reconfigured) → Mojeek API if quality insufficient | Google/Bing removed; Startpage + DuckDuckGo active. Mojeek configured but disabled (API key needed). LibreChat webSearch deployed. See §13.8. |
| **Graph layer** | FalkorDB + Graphiti (ingest + retrieval, live) | `GRAPHITI_ENABLED=true` on both `knowledge-ingest` and `retrieval-api`. Graph entities/relationships are written at ingest and a parallel graph search branch is RRF-merged with the Qdrant 3-leg fusion. Empirical value for Klai's B2B query mix is under evaluation — see §5.3 and §13.3. |
| **Structured storage** | PostgreSQL `knowledge` schema | Replaces SQLite. Artifacts, provenance DAG, entity registry, embedding outbox. Same cluster as klai-docs. |
| **Taxonomy discovery** | BERTopic + HDBSCAN | Starting point; human approval gate required |
| **Retrieval orchestration** | None (V1) — eigen `knowledge-ingest` service | Haystack was gepland maar verwijderd uit V1. Reden: Qdrant + de ingest pipeline dekten al de meeste orchestration-taken (chunking, embedding, vector search, scoping) — Haystack zou een extra abstractielaag toevoegen over functionaliteit die al aanwezig was. Eigen `knowledge-ingest` service gebouwd in plaats daarvan. Haystack heroverwegen als de retrieval pipeline complex genoeg wordt om pipeline-compositie (meerdere retrievers, rerankers, readers) via één definitie te rechtvaardigen. |
| **Reranking** | bge-reranker-v2-m3 via Infinity server (self-hosted, CPU) | **Deployed** (March 2026) as `infinity-reranker` on core-01. Jina-compatible `/v1/rerank` endpoint. Currently serves LibreChat webSearch; will also serve Knowledge retrieval pipeline. |
| **LLM interface** | Claude via LiteLLM | Grounded responses with source citations |
| **PII detection** | Presidio + GLiNER (gliner_multi-v2.1) | For Dutch transcripts; pseudonymization, not anonymization |
| **Knowledge storage** | Gitea (self-hosted) | Git-backed, org-per-tenant, webhook → ingest pipeline |
| **Editor** | BlockNote | Block-based, serializes to markdown + YAML frontmatter |
| **Publication site** | Next.js (klai-docs) | Reads from Gitea via API, SSR |
| **Auth** | Zitadel (existing) | OIDC for editor and private KB access |
| **Routing/proxy** | Caddy (existing) | Custom domain SSL via Let's Encrypt |
| **AI interface** | MCP server → Claude / local model | Model-agnostic retrieval layer |

---

## Appendix: Relation to Existing Klai Components

| Existing component | Role in Klai Knowledge |
|---|---|
| `klai-docs` | Publication layer — renders KB sites + REST API; editor UI lives in klai-portal (see §13.7) |
| `klai-portal` | Editorial interface — editorial inbox, article management, gap review |
| `knowledge-ingest` | Unified ingest pipeline — accepts documents from all sources, enriches (Contextual Retrieval + HyPE), embeds (dense + sparse), stores in Qdrant |
| `retrieval-api` | Retrieval service — 3-leg RRF fusion (dense + question + sparse); used by portal-api and the LiteLLM hook |
| `klai-knowledge-mcp` | MCP write interface — personal + org knowledge saves; posts directly to knowledge-ingest |
| `docling-serve` | Shared document parser — self-hosted, produces HybridChunker output; called by knowledge-ingest |
| Zitadel | Auth — already in production, OIDC for all editor access |
| Caddy (core-01) | Reverse proxy — handles `*.getklai.com` routing |

The core infrastructure is built. See §0 for remaining open items.
