# Klai Knowledge: Platform Architecture

> Describes what runs. Verified on 12 November 2026 against commit `3987ee2a9` (origin/main), with the two later changes of 12 November (#2340, #2341) folded in and the anchors rechecked at `631b5a82f`. Whatever the design wants and the code does not do yet lives in [product-gaps-backlog.md](product-gaps-backlog.md); this document names the matching `GAP-*` id in one sentence where a reader would expect the feature. How the system got here is told in [evolution.md](evolution.md).

## 1. What Klai Knowledge is, and where the other documents are

Klai Knowledge is the organisation memory of the platform. Every organisation has one instance, fed from the sources it connects: pages written in the portal editor, crawled help sites, connector syncs, uploaded files, meeting transcripts and notes saved by an AI client. Each document is split into chunks, embedded, and stored with metadata about its origin, its kind of claim and the period in which it was held to be true.

Two stores share one artifact id: PostgreSQL (schema `knowledge`) is the ledger of documents and versions, Qdrant (collection `klai_knowledge`) holds the chunk vectors and a payload used for filtering. A graph store (FalkorDB, through Graphiti) holds entities and facts per organisation. Chat, the widget, partner keys and external AI clients all read through one retrieval service. The knowledge belongs to the organisation in open formats: pages are markdown with YAML frontmatter in Git.

| Question | Document |
|---|---|
| How does a document get in, step by step? | [knowledge-ingest-flow.md](knowledge-ingest-flow.md) |
| How is an answer found and ranked? | [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) |
| How does chat use retrieval? | [chat-system.md](chat-system.md) |
| Platform services, hosts, models | [platform.md](platform.md) |
| Transcription and meeting notes | [scribe-transcription.md](scribe-transcription.md) |
| From an unanswered question to a proposed article | [knowledge-gap-to-article.md](knowledge-gap-to-article.md) |
| Analysis of support cases against a knowledge base | [support-gap-detection.md](support-gap-detection.md) |
| Everything the design wants and the code does not do | [product-gaps-backlog.md](product-gaps-backlog.md) |
| History and the plan for ingest and retrieval | [evolution.md](evolution.md), [knowledge-base-ingestion-and-retrieval-plan.md](knowledge-base-ingestion-and-retrieval-plan.md) |
| Research behind the choices | [docs/research](../research/README.md) |

### 1.1 A document in, in brief

Everything reaches knowledge-ingest through `POST /ingest/v1/document` (`klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:1083`, `ingest_document` at :366), except the Gitea push webhook (`/ingest/v1/webhook/gitea`) and the crawl routes. Callers: the docs app through Gitea, klai-connector (GitHub, Notion, Airtable, Confluence, Google Drive, Microsoft documents, JSON feeds), the portal (file upload, partner API, meeting transcripts), `klai-knowledge-mcp`, and scribe. Scope (organisation or personal) is a field of the request body; there are no separate routes per scope. The request is handled in two phases: the synchronous part chunks the text, embeds it, writes the PostgreSQL row and the Qdrant points, so the document is searchable at once; the Procrastinate worker then adds the contextual prefix, the question vectors, the sparse vectors and the graph episode, and sets `index_status`. Support cases from the HubSpot connector are not ingested as documents; they go to the portal ([support-gap-detection.md](support-gap-detection.md)). The full flow is [knowledge-ingest-flow.md](knowledge-ingest-flow.md).

### 1.2 An answer out, in brief

`POST /retrieve` on `retrieval-api` (`klai-retrieval-api/retrieval_api/api/retrieve.py:1412`) verifies the caller's identity, rewrites a follow-up question into a self-contained one when the caller did not (`coreference_model` `klai-fast`, 3 second timeout, :703), embeds the query as dense and sparse vectors (:750), searches Qdrant with the legs `vector_chunk`, `vector_questions` and `vector_sparse` fused by reciprocal rank fusion over 60 candidates (:833, `config.py:21`), merges the graph leg (section 6), reranks with `bge-reranker-v2-m3` (:904, `services/reranker.py:50`) and returns `top_k` chunks (default 8) with at most two per source label first and the remaining slots filled from the other candidates (`config.py:59`, `services/diversity.py:24,98-105`). The full pipeline is [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md).

## 2. The knowledge model as stored

### 2.1 Tenant key and scopes

The tenant key in `knowledge.*` and in the Qdrant payload is `org_id`, the Zitadel organisation id as text. There is no `tenant_id` payload key. Portal tables (`portal_*`) use a different `org_id`, the integer `portal_orgs.id`; the two are joined through `portal_orgs.zitadel_org_id`. Scope inside an organisation is carried by `kb_slug` plus `user_id`: a personal knowledge base has the slug `personal-<zitadel_user_id>` (`klai-libs/kb-slugs`, `personal_kb_slug`), and its points carry `user_id`. Retrieval accepts `scope` = `personal`, `org` or `both` (`klai-retrieval-api/retrieval_api/models.py:39`, default `org`).

### 2.2 Fields, side by side

One row of `knowledge.artifacts` is one version of one document (`klai-knowledge-ingest/alembic/versions/0001_baseline.py:105`). The Qdrant points of the current version carry the same facts in their payload, written by `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py` (plain path `upsert_chunks` at :281, enriched path `upsert_enriched_chunks` at :364) from a payload assembled in `routes/ingest.py` (`extra_payload`, around :826 to :900).

| Field | PostgreSQL `knowledge.artifacts` | Qdrant payload | Where it comes from |
|---|---|---|---|
| `id` / `artifact_id` | `id` uuid | `artifact_id` | generated per version |
| `org_id` | text, NOT NULL | `org_id` (keyword index, `is_tenant` on collections created by current code) | request, verified against the caller |
| `kb_slug`, `path` | text, NOT NULL, default empty | same keys | document identity inside the organisation |
| `user_id` | text | `user_id` when set | request, or derived from the slug `personal-<uid>` (`routes/ingest.py:645`) |
| `provenance_type` | CHECK observed, extracted, synthesized, revised | `provenance_type` when the frontmatter carries it (`routes/ingest.py:192`) | frontmatter, else the source profile default (`routes/ingest.py:230` to `:290`) |
| `assertion_mode` | CHECK factual, belief, hypothesis, procedural, quoted, unknown | `assertion_mode` | frontmatter; default `unknown` |
| `synthesis_depth` | smallint 0 to 4 | not in the payload | frontmatter, else 4 for `source_type=docs`, else the profile default |
| `confidence` | CHECK high, medium, low, nullable | `confidence` when the frontmatter carries it | frontmatter label |
| `belief_time_start` | bigint epoch seconds | `belief_time_start` | frontmatter, else ingest time |
| `belief_time_end` | bigint, default 253402300800 | `belief_time_end` | sentinel for "still active" (`pg_store.py:25`), set to the replacement or deletion time when a row closes |
| `superseded_by` | uuid | not in the payload | set on closed rows |
| `valid_from`, `valid_until` | not stored | integer fields copied from `belief_time_start` and `belief_time_end` | enriched path only (`qdrant_store.py:427`) |
| `content_type` | text, default `unknown` | `content_type` | source |
| `content_hash` | text | not in the payload | change detection |
| `index_status`, `index_status_changed_at` | `0007_artifacts_index_status.py`, `0008_artifacts_index_status_changed_at.py` | not in the payload | `pending` on insert, set by the enrichment job |
| `visibility` | `knowledge.kb_config.visibility` (per KB) | `visibility` | `kb_config.get_kb_visibility` at ingest |
| `extra` | jsonb | selected keys flattened into the payload | holds `document_text`, `title`, `source_type`, `source_connector_id`, `source_ref`, taxonomy ids, tags and graph bookkeeping |
| chunk fields | `knowledge.parent_chunks` (parent text) | `text`, `chunk_index`, `text_fingerprint`, `heading_path`, `parent_chunk_id` | chunking |
| enrichment fields | not stored | `text_enriched`, `context_prefix`, `questions`, `product`, `cheap_layer_version`, `quality_score`, `feedback_count`, link and entity counters | enrichment job |

Facts a reader needs when relying on these fields:

- `valid_from` and `valid_until` exist on a point once the enriched path has written it. A point written only by the plain path has neither field until enrichment rewrites it (`qdrant_store.py:316` to `:322` holds the plain payload). The temporal filter in retrieval (`klai-retrieval-api/retrieval_api/services/search.py:91`) reads both the `valid_from`/`valid_until` pair and the older `valid_at`/`invalid_at` pair.
- `belief_time_end` is never null. The ingest parser does not read `belief_time_end` from frontmatter (`routes/ingest.py:285` to `:296`), so each new row starts at the sentinel; a null in a page's frontmatter is accepted by the docs app and then ignored at ingest.
- Only `assertion_mode` is returned as response metadata (`klai-retrieval-api/retrieval_api/services/search.py:559`, `klai-retrieval-api/retrieval_api/models.py:152`); `provenance_type`, `confidence` and `source_note` stay in the Qdrant payload and are not in the response. None of them changes ranking: the evidence-tier scoring was removed on 20 August 2026 (commit `2ce085277`, `klai-retrieval-api/tests/test_retired_retrieval_experiments.py:12`).
- There is no `chunk_type` field; the per-chunk classification was removed on 8 June 2026.

### 2.3 The three axes

- **Provenance origin** (`provenance_type`): `observed` (captured from an event), `extracted` (derived by extraction), `synthesized` (built from several sources), `revised` (an updated version of an artifact).
- **Assertion mode** (`assertion_mode`): `factual`, `procedural`, `quoted`, `belief`, `hypothesis`, `unknown`. Legacy aliases (`fact`, `claim`, `speculation`, `note`) are mapped on ingest (`routes/ingest.py:199`). Automatic labelling does not exist; the value comes from frontmatter or from the MCP save tools, otherwise it is `unknown` (GAP-EPIST-01).
- **Synthesis depth** (`synthesis_depth`): 0 raw capture, 1 extracted claim, 2 synthesis of several sources on one topic, 3 cross-topic synthesis, 4 published and curated. Pages from the docs app default to 4, and the MCP tool `create_docs_page` writes pages with depth 1 (`klai-knowledge-mcp/main.py:1281`).

The model has no separate type for captured sources versus constructed claims (GAP-MODEL-01).

### 2.4 Content type

`content_type` is document-level metadata such as `kb_article` or `pdf_document`, default `unknown`. It is returned with results and is not used for scoring.

### 2.5 Visibility values

| Place | Values |
|---|---|
| Ingest and Qdrant (`kb_config.py`, payload `visibility`) | `internal` (default), `public`, `private` |
| Portal knowledge base (`klai-portal/backend/app/api/app_knowledge_bases.py:845`) | `public`, `internal` |
| Docs app (`klai-docs/migrations/001_docs_schema.sql:23`) | `public`, `private` |

A slug that starts with `personal-` is always `private` (`kb_config.py:52`). A visibility change on an existing knowledge base goes through `PATCH /ingest/v1/kb/visibility` (`routes/ingest.py:1484`), which updates `knowledge.kb_config` and rewrites the payload of all points of that knowledge base. A value for logged-in external users does not exist (GAP-VIS-01).

## 3. Versions, supersession and deletion

**A changed document.** `POST /ingest/v1/document` closes the active row of the same (org, kb, path), or for connector documents of the same `(source_connector_id, source_ref)`, so a rename closes the old path (`pg_store.py:401`, called at `routes/ingest.py:675`). It inserts the new row with `index_status='pending'` and writes `derivations` edges for valid `derived_from` ids (active artifacts of the same org, `pg_store.py:60`), then links the closed rows to the new id through `superseded_by` (`pg_store.py:457`, called at `routes/ingest.py:702`). The old row stays in PostgreSQL with its full `document_text` in `extra`, so history is the chain of closed rows. Qdrant keeps only the current version: both upsert functions delete the points of (org, kb, path) after a successful upsert (`qdrant_store.py:241`), and a closed row under an old path has its points deleted explicitly.

**A deleted document.** Every single-document delete route calls `document_purge.purge_documents` (`knowledge_ingest/document_purge.py:29`; callers `routes/personal.py:86`, `routes/kb_sources.py:395`, `adapters/crawler.py:881`, `routes/ingest.py:1282`, `:1426`, `:1466`). It closes the active rows, walks `superseded_by` back through every version, removes the graph episodes and the Qdrant points, and then deletes the rows themselves: artifacts with their `document_text`, parent chunks, image rows, queue and entity rows, derivations, crawled pages and page links. A replaced version therefore keeps its history, while a deleted document leaves nothing behind. PostgreSQL stays the retry ledger while this runs. The step-by-step description is [knowledge-ingest-flow.md](knowledge-ingest-flow.md#replaced-keeps-history-deleted-is-gone).

Deleting a whole knowledge base (`DELETE /ingest/v1/kb`) and deprovisioning an organisation go further: they also delete the images in object storage under `{org_id}/images/{kb_slug}/` or `{org_id}/images/`, and the knowledge rows that carry the knowledge-base or organisation key (queue rows, connector fences and, for an organisation, the graph refresh budget). In the knowledge-base delete the rows go last, so a failure in an earlier store keeps them and the portal's retry finishes the delete (`klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:1351-1376`, `klai-knowledge-ingest/knowledge_ingest/pg_store.py:862-934`, `klai-libs/image-storage/klai_image_storage/storage.py:176-205`, `klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:449-468`).

**Keeping the stores consistent.** The write path is synchronous: PostgreSQL row, then Qdrant points, then an enrichment job (Procrastinate) that rewrites the points and sets `index_status`. A nightly read-only job compares the active PostgreSQL artifacts with the distinct `artifact_id` values in Qdrant and, when the graph is on, the episode ids that finished graph extractions record with the episodes the graph holds (`lost_graph_extractions`, :136), and logs `pg_qdrant_reconcile` with status ok, failed (drift or a lost extraction) or error (including a graph that cannot be read) (`knowledge_ingest/consistency_reconcile.py:175`, cron `30 3 * * *` at :228); a Grafana rule alerts on a failed or missing run (`deploy/grafana/provisioning/alerting/ingest-rules.yaml`, runbook [pg-qdrant-reconcile.md](../runbooks/pg-qdrant-reconcile.md)). The job reports and does not repair. Repair is an operator script, `klai-knowledge-ingest/knowledge_ingest/scripts/repair_pg_qdrant_drift.py` for Qdrant and `klai-knowledge-ingest/scripts/reextract_document_graph.py` for one document that lost graph episodes (dry run by default); `stale_pending_artifact_reaper.py` and `zombie_recovery.py` sit next to it. A transactional outbox does not exist (GAP-SYNC-01).

**Point in time.** `belief_time_start` and `belief_time_end` are stored per row, and retrieval filters out points whose `valid_until` has passed or whose `valid_from` is in the future. A query "what did we hold on date D" has no endpoint (GAP-TEMPORAL-02).

## 4. Stores

| Store | Holds | Source of truth for |
|---|---|---|
| PostgreSQL `knowledge` (FORCE row level security by `org_id`, migrations `0001` to `0015`) | `artifacts`, `parent_chunks`, `artifact_images`, `page_links`, `derivations`, `kb_config`, `org_config`, crawl state (`crawled_pages`, `crawl_jobs`, `crawl_domains`, `crawl_job_frontier`), `connector_resource_fences`, `graph_refresh_budget`, `rag_eval_results` | documents, versions, the full text (`extra->>'document_text'`, the replay source of `rebuild_kb`), visibility per knowledge base |
| Qdrant `klai_knowledge` | vectors `vector_chunk`, `vector_questions` (dense) and `vector_sparse`, plus the payload above (`qdrant_store.py:49`, vectors at :73) | what retrieval can find |
| FalkorDB (compose service `falkordb`), one graph per organisation (`group_id` = `org_id`) | entities and facts extracted by Graphiti | graph search |
| PostgreSQL `public.portal_*` | knowledge bases, taxonomy, gaps, proposals, support cases | portal settings; the ingest worker reads aliases, product rules and taxonomy nodes directly (`kb_config.py:197`) |
| PostgreSQL `docs` schema and Gitea | organisations, knowledge bases, custom domains; page files with frontmatter | the pages of a docs knowledge base |

Tables that exist and are never written: `knowledge.entities` and `knowledge.artifact_entities` (no insert anywhere, only deletes), and `knowledge.embedding_queue` (only deletes at `pg_store.py:732`, `:880`, `:985`). Entity data that is produced lives in FalkorDB and in the payload keys `entity_uuids`, `entity_names` and `entity_pagerank_max`.

Collection settings: the `org_id` index is created with `is_tenant` when `ensure_collection` creates it; on an existing collection the function logs the current state and does not rebuild (`qdrant_store.py:89` to `:125`, upgrade script `klai-knowledge-ingest/scripts/upgrade_org_id_tenant_index.py`, GAP-TENANCY-01).

## 5. Taxonomy as it runs

| Piece | What exists | Anchor |
|---|---|---|
| Nodes and proposals | `portal_taxonomy_nodes` (kb, parent, name, slug, order, description), `portal_taxonomy_proposals` (type, status, payload, confidence, reviewer) | `klai-portal/backend/app/models/taxonomy.py:12` |
| Admin screen | knowledge base tab "taxonomy" with nodes, proposals and coverage | `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/taxonomy.tsx` |
| Portal routes | node CRUD, proposals, bootstrap, backfill trigger and status, top tags, coverage | `klai-portal/backend/app/api/taxonomy.py` |
| Ingest routes | `/ingest/v1/taxonomy/` backfill, remove-node, replace-node, bootstrap-proposals, classify, auto-categorise, top-tags, coverage-stats | `knowledge_ingest/routes/taxonomy.py` |
| Classification | `taxonomy_classifier.classify_document` calls the LiteLLM alias `klai-ingest` (`knowledge_ingest/config.py:433`) and returns up to 5 nodes with confidence at least 0.5 and up to 5 tags; with no nodes it makes no call; a capacity error leaves the document unclassified (null) so it is picked up later. At ingest a centroid match is tried first and the model only when no centroid matched | `taxonomy_classifier.py:57` |
| Bootstrap | `proposal_generator.generate_bootstrap_proposals_v2` (clustering by UMAP and HDBSCAN) runs only from the admin route `taxonomy_bootstrap_proposals` | `routes/taxonomy.py:505` |
| Scheduled | `register_taxonomy_tasks`: the backfill task and the periodic `taxonomy-unclassified-sweep` (cron `17 * * * *`) that queues a backfill for every knowledge base with chunks that have no label; `register_auto_categorise_task` | `taxonomy_tasks.py:34` and :46, `auto_categorise_tasks.py:15`, registered at `enrichment_tasks.py:127` |
| Query time | retrieval filters on `taxonomy_node_ids` when the request carries them (`search.py:466`, at most 50 ids, `models.py:57`); the older single-id field `taxonomy_node_id` is still accepted. Portal chat, the widget path and `klai-knowledge-mcp` do not send node ids | `search.py:466` |
| Gap rows | a gap row gets `taxonomy_node_ids` through a call to `/ingest/v1/taxonomy/classify` (`klai-portal/backend/app/services/gap_events.py:236`) | no route reads it any more: the list routes `gaps/summary` and `gaps/by-taxonomy` were removed on 12 November 2026 (`klai-portal/backend/app/api/app_gaps.py:54` is the one route left there) |

The scheduled sweep classifies documents that lack a label. Nothing re-clusters an existing taxonomy or proposes new nodes after the bootstrap (GAP-TAX-01). Retrieval-api has no taxonomy routes: its only route is `POST /retrieve` (`klai-retrieval-api/retrieval_api/main.py:100`, `klai-retrieval-api/retrieval_api/api/retrieve.py:1426`), so a coverage fraction does not exist (GAP-TAX-02). The coverage widget in the portal uses ingest `coverage-stats`, which counts chunks per node from Qdrant (`routes/taxonomy.py:738`).

## 6. The graph layer as it runs

- **Library and store.** `graphiti-core[falkordb]` 0.31 together with the in-repo `klai-libs/graphiti-compat` (candidate search over approximate nearest neighbours) in both knowledge-ingest and retrieval-api (`pyproject.toml` in each). FalkorDB runs as the compose service `falkordb` (`deploy/docker-compose.yml:2142`, stop grace period 60 s at :2155, query limits at :2171). It persists by snapshot only, so the longer grace period gives its shutdown save time to finish; the nightly reconcile counts documents whose recorded episodes the graph lacks (#2341, section 3). One graph per organisation.
- **Switches.** `GRAPHITI_ENABLED` and `GRAPH_ANN_ENABLED` are `true` for retrieval-api (`docker-compose.yml:1627`, `:1634`) and knowledge-ingest (:2221, :2228).
- **Extraction.** A Phase 2 enrichment job calls `graph.ingest_episode` (`knowledge_ingest/graph.py:971`): one episode per document with `reference_time = belief_time_start`, custom extraction instructions, three attempts with backoff, behind a semaphore and a rate-limited transport. Extraction rules carry a version (`GRAPHITI_EXTRACTION_VERSION = 2`, `enrichment_policy.py`); navigation and index pages are skipped.
- **Current extraction only** (#2339, `3987ee2a9`). The graph keeps the current extraction of each document. Ingest defers the graph job with `replace_stale=True` (`routes/ingest.py:1049`), `retire_earlier_extractions` (`graph.py:684`) deletes earlier extractions once the new one is complete, and `delete_kb_episodes` (`graph.py:595`) also removes the episode id from each fact and deletes facts left empty. The organisation-wide orphan sweep after a connector purge keeps history; the older backlog is removed only by `klai-knowledge-ingest/scripts/prune_stale_graph.py --execute`.
- **Query time.** `retrieve.py:857` starts `graph_search.search(query, org_id, top_k=20)` in parallel with the Qdrant search and merges its chunks into the Qdrant results with `_rrf_merge` (`retrieval_api/api/ranking.py:65`, k = 60). If the graph leg fails it is logged and dropped. Facts that Graphiti expired or invalidated are filtered out afterwards in Python (`retrieval_api/services/graph_search.py:280`).
- **Cost model and rollout.** [SPEC-GRAPH-SCALE-001](../specs/SPEC-GRAPH-SCALE-001/spec.md) holds the cost model and the ANN rollout.

## 7. The AI interface

`klai-knowledge-mcp/main.py` is a FastMCP server over streamable HTTP. Identity comes from an OAuth bearer token verified through the portal (`_identify_via_oauth_token`, :429) or from the internal secret with `X-User-ID`, `X-Org-ID` and `X-Org-Slug` headers (:479); tools take no `org_id` or `user_id` parameter. LibreChat reaches it over the internal network (`deploy/librechat/librechat.yaml:141`); external AI clients use the public MCP host with OAuth (`deploy/caddy/Caddyfile:290`).

| Tool | Kind | Minimum role | Behaviour | Anchor |
|---|---|---|---|---|
| `search_knowledge(query, top_k=8, scope, kb_slugs)` | read | any verified user | calls retrieval-api `/retrieve`; the scope parameter selects personal, organisation or combined knowledge; the personal scope needs a user id and ignores `kb_slugs` | `main.py:1546` |
| `save_personal_knowledge` | write | any | `POST /ingest/v1/document` with `kb_slug = personal_kb_slug(user)`, `source_type=mcp`, frontmatter with assertion mode, tags and source note | `main.py:891` |
| `save_org_knowledge` | write | company | same, with the organisation knowledge base as target | `main.py:960` |
| `list_docs_kbs`, `list_docs_pages`, `get_docs_page` | read | any | against the docs app | `main.py:1028`, `:1058`, `:1115` |
| `create_docs_page`, `update_docs_page` | write | company | docs app write; frontmatter validated there | `main.py:1195`, `:1350` |
| `shield_check_compliance`, `shield_prepare_prompt` | other | platform admin | Klai Shield | `main.py:1728`, `:1757` |

The role gate is `_TOOL_MIN_ROLE` (`main.py:308`). The read surface is the one retrieval tool plus the docs reads; tools for related concepts, belief evolution, provenance chains and recent items do not exist (GAP-MCP-01). The prompt file `klai-knowledge-mcp/agent-system-prompt.md` names the tool `save_personal_knowledge` and the assertion modes the server accepts, and a test fails when it names a tool the server does not register (`klai-knowledge-mcp/tests/test_agent_system_prompt.py:18-30`); no code in the repository loads that file.

A chat turn or an MCP search whose result is empty or weak produces a gap event, except when only personal knowledge was searched: a chat turn whose scope is personal registers none (`klai-portal/backend/app/services/retrieval_context.py:1109-1118`), and an MCP search registers none when the scope is personal or the caller has the personal role, which retrieval-api narrows to personal scope (`klai-knowledge-mcp/main.py:1683-1687`); how gap events become a list of knowledge gaps is in [knowledge-gap-to-article.md](knowledge-gap-to-article.md) and [support-gap-detection.md](support-gap-detection.md).

## 8. Organisation, personal and shared knowledge

- **Organisation knowledge.** Knowledge bases of an organisation carry visibility `internal` or `public`. Every retrieval query carries a must-filter `org_id = X` built in `retrieval_api/services/search.py:131`; ingest checks the caller's identity against the organisation before it accepts a document (`routes/ingest.py:1100`).
- **Personal knowledge.** Each user has a knowledge base `personal-<zitadel_user_id>`, created on signup or first use (`klai-portal/backend/app/services/default_knowledge_bases.py:27`, `owner_type='user'`). Personal documents run the same ingest and enrichment pipeline as organisation documents; `enrichment_policy.py` has no branch on personal scope (GAP-PRIV-01 holds the design question).
- **Enforcement points for visibility.**

| Layer | Rule | Anchor |
|---|---|---|
| Ingest | slug `personal-*` is stored as `private`; an ingest naming `personal-<other user>` is refused with `personal_kb_owner_mismatch` | `kb_config.py:52`, `routes/ingest.py:1106` |
| Retrieval | `scope=personal` narrows to the caller's own slug; the other two scopes leave out points with visibility `private` unless they carry the caller's `user_id` | `search.py:131` to `:210` |
| Portal | `is_personal_kb` has no administrator bypass | `klai-portal/backend/app/services/access.py:27` |
| Docs reader | a knowledge base that is personal or `private` is not served | `klai-docs/app/(reader)/[...path]/page.tsx:116` |

- **Listing and deleting personal items.** `GET /knowledge/v1/personal/items` and `DELETE /knowledge/v1/personal/items/{artifact_id}` (`routes/personal.py:31`, `:60`); the delete purges all versions as in section 3.
- **Offboarding.** `kb_offboarding.apply_dispositions` (`klai-portal/backend/app/services/kb_offboarding.py:313`): a knowledge base the user owns alone is transferred or deleted as an administrator chooses, and the personal knowledge base is deleted (`kb.personal_purged_on_offboard`, :476), through the docs app, knowledge-ingest and the portal database; partner API keys and MCP tokens of the user are revoked. There is no export step (GAP-OFFBOARD-01).
- **Not built.** Promotion of a personal note to the organisation (GAP-PERSONAL-01) and sharing between organisations (GAP-FED-01).

## 9. The publication layer (docs app)

`klai-docs` is a Next.js app. A knowledge base is a Gitea repository (`docs.knowledge_bases.gitea_repo`); a page is a markdown file with frontmatter. The editor (BlockNote) lives in the portal frontend (`klai-portal/frontend/package.json:29`), not in `klai-docs`.

- **Write path.** `PUT /api/orgs/{org}/kbs/{kb}/pages/{path}` validates an optional `frontmatter` object with a zod schema (`knowledgeFrontmatterSchema`: provenance type, assertion mode, synthesis depth, confidence, belief times, `superseded_by`, `derived_from`; passthrough for other keys), gives a page a stable `id` on first save and commits through the Gitea API (`klai-docs/app/api/orgs/[org]/kbs/[kb]/pages/[...path]/route.ts:18`, `:151`). `lib/markdown.ts:4` parses and serialises the frontmatter and keeps unknown keys on a read-modify-write.
- **Into the index.** A Gitea push webhook (registered by `klai-docs/lib/knowledge_ingest.ts`) calls `/ingest/v1/webhook/gitea` (`routes/ingest.py:1132`), and knowledge-ingest re-ingests the changed pages. Creating or deleting a knowledge base and changing its visibility also call ingest.
- **Read path.** `app/(reader)/[...path]/page.tsx` serves a page under the organisation's subdomain or a verified custom domain (`docs.custom_domains`, route `/api/tls-validate`).
- **Docs tables.** `docs.organizations`, `docs.knowledge_bases`, `docs.custom_domains`, `docs.page_edit_restrictions` and idempotency keys (`klai-docs/migrations/001_docs_schema.sql`, `002`).
- **Frontmatter conventions.** Dates are written as quoted strings so that the YAML parser keeps them as strings. `derived_from` holds page ids (UUIDs), never slugs.

## 10. Technology stack as configured

All services below are defined in `deploy/docker-compose.yml`.

| Layer | Component | Configured as |
|---|---|---|
| Ingest | `knowledge-ingest` (:2188), `docling-serve` (:1577), `crawl4ai` (:2374) | document parsing with the docling hybrid chunker; Crawl4AI for web crawling |
| Retrieval | `retrieval-api` (:1603) | one `/retrieve`; see [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md) |
| Vectors | `qdrant` (:2120), image `qdrant/qdrant:v1.20.0` | dense `vector_chunk`, dense `vector_questions` (HyPE), sparse `vector_sparse` |
| Embeddings and reranker | BGE-M3 dense through the alias `klai-bge-m3` (`deploy/litellm/config.yaml:319`); a sparse sidecar (`SPARSE_SIDECAR_URL`); the reranker at `infinity_reranker_url` (`klai-retrieval-api/retrieval_api/config.py:16`, set in compose as `INFINITY_RERANKER_URL`, `deploy/docker-compose.yml:1620`) | the host that serves them is set by compose environment, see [platform.md](platform.md) |
| Graph | `falkordb` (:2142), image `falkordb/falkordb:6.0.4`, Graphiti | section 6 |
| Relational | `postgres` (:232) | schemas `knowledge`, `docs`, `public` (portal) |
| LLM routing | `litellm` (:383) | aliases `klai-fast`, `klai-primary`, `klai-medium`, `klai-large` for chat; `klai-ingest` for enrichment and taxonomy classification; `klai-judge` for the judging calls of the gap flow; both background aliases map to Mistral models (`config.yaml:268`, `:300`) |
| Pages | `gitea` (:1684), `docs-app` (:1733) | one repository per knowledge base |
| AI interface | `klai-knowledge-mcp` (:1764) | section 7 |
| Web search | `searxng` (:1591) | Startpage and DuckDuckGo enabled, Google and Bing disabled (`deploy/searxng/settings.yml:27`) |
| Personal data masking | `presidio-analyzer` (:545), `presidio-anonymizer` (:600) | PII detection |
| Identity | `zitadel` (:321), `caddy` (:182) | OIDC and reverse proxy |

There is no retrieval orchestration framework between the services: no Haystack dependency exists in retrieval-api or knowledge-ingest.
