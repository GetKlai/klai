# Product Gaps Backlog: what the design wants and the code does not do yet

> The one home of the target design for Klai Knowledge. What runs is in [klai-knowledge-architecture.md](klai-knowledge-architecture.md); history is in [evolution.md](evolution.md). Each entry holds what the design wants, what the code does today with anchors, and the status. Anchors were opened on 12 November 2026 against commit `3987ee2a9` (origin/main), with the two later changes of 12 November (#2340, #2341) folded in and the anchors rechecked at `631b5a82f`. Paths are relative to the repository root.
> The entries condense the target design that stood in the architecture document until 12 November 2026. The full original text (schemas, example queries, process descriptions) is in the git history: `git show acd6c377c:docs/architecture/klai-knowledge-architecture.md`. The research behind several entries is in [knowledge-architecture-research-notes.md](../research/knowledge-architecture-research-notes.md).

## How to read this

- **Gap id.** Stable handle `GAP-<area>-NN`. Documents and code comments cite these ids.
- **Status.** `NOT BUILT` (nothing in the code), `PARTLY` (some of the design runs), `BUILT differently` (the need is met by another mechanism, described in the entry). Closed ids are in one table below.
- **Effort.** Order of magnitude only: S, M, L, XL. Not a commitment.
- **Where the gap flow is designed.** The weekly list, the proposal per group, destinations per knowledge base and the closing of the loop are designed in [knowledge-gap-to-article.md](knowledge-gap-to-article.md) (phase 1 is built, phases 2 to 4 are not). The analysis of support cases is in [support-gap-detection.md](support-gap-detection.md). The entries below hold only what those documents do not.

## Closed

| Id | Outcome | Closed by |
|---|---|---|
| GAP-TEMPORAL-01 | Retrieval filters on both the older `valid_at`/`invalid_at` pair and the ingest-written `valid_from`/`valid_until` pair (`klai-retrieval-api/retrieval_api/services/search.py:91`); both upsert paths delete the points of (organisation, kb, path) after a successful upsert. Retro: [2026-06-08-temporal-filter-field-mismatch.md](../retros/2026-06-08-temporal-filter-field-mismatch.md) | `a61f7f752` (filter), 2026-06-11 status |
| GAP-EVID-01 | Evidence-tier scoring removed: the 30-day shadow changed 2,186 of 8,946 orderings and recorded no paired answer-quality outcome. Assertion mode stays document metadata and no longer affects order (`klai-retrieval-api/tests/test_retired_retrieval_experiments.py:8`) | `2ce085277` (#1122), 2026-08-20 |
| GAP-EVID-02 | The 5-to-3 grouping of assertion modes for scoring was never built and has no consumer; none will be added without a new product requirement | `2ce085277` (#1122) |
| GAP-RETR-01 | Retrieval gate removed: the 30-day shadow produced 3 unique would-bypass decisions in about 4,500 requests, all with low retrieval confidence. Retrieval is mandatory for every knowledge-enabled request | `2ce085277` (#1122) |
| GAP-EVAL-01 | Scored suite runs load with `require_reference_answer=True` (`klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py:143`, `eval/intake_quality.py:36`); no fallback to `expected_topics`. Baseline `baseline-v5` still has to be recaptured | `710614179`, 2026-06-11 |
| GAP-EVAL-02 | `ragas_runner` compares `expected_chunks` before the judge; a missing canary writes a NULL-metric row and trips the Grafana alert `rag_eval_canary_dropped` (`deploy/grafana/provisioning/alerting/rag-eval-rules.yaml`). A live canary run is still to be checked | `710614179`, 2026-06-11 |
| GAP-INGEST-02 | Per-chunk `chunk_type` classification removed (`klai-knowledge-ingest/knowledge_ingest/enrichment.py:10`); rationale in [chunk-type-retrieval-value.md](../research/chunk-type-retrieval-value.md). A future chunk-type-aware retrieval starts from that document | `f6d0d146f`, 2026-06-08 |
| GAP-LOOP-02 | The need (a paraphrase of a gap collapses into one entry) is met in PostgreSQL instead of a Qdrant registry: every row has a `question_key` (`klai-portal/backend/app/services/gap_events.py`), and `fold_into_open_group` (:347) moves open rows onto a matching group, chosen by an embedding shortlist of 30 groups and a judging call (alias `klai-judge`, 120 second deadline). The weekly list groups chat signals by article or topic words without a model (`knowledge_gap_report.py`) | `47af2f5ee` (#1624), `f2ea9f9e3` (#2321) |
| GAP-LOOP-05 | Gaps no longer come from one signal. Producers: low-confidence chat turns (`retrieval_context.py`, `gap_classification.py:8`), external AI clients (`klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py`, first in `b196c5636`), a colleague's answer review (`app_activity.py`), the conversation judge (`conversation_judge.py`, `ce6228295`), and imported or synchronised support cases (`support_cases.py`, `e50747ba4`) | see commits |

## Open: the gap-handling loop

The designed loop: users ask, low-confidence answers and support conversations become gap candidates, the candidates are aggregated and prioritised, an owner reviews them, a human writes or an AI drafts the article, the article is published and re-indexed, and fewer low-confidence answers follow. Humans decide what to write and sign off every article; the AI proposes and never publishes by itself. Detection, aggregation and re-indexing run without a person.

### GAP-LOOP-01: a judge that says covered, partial or new  ·  L  ·  PARTLY

- **Design.** A two-phase check per candidate. Phase 1 searches the existing items (hybrid search, top 5) and compares cosine similarity: above 0.90 the item is covered and skipped, from 0.60 to 0.90 it goes to phase 2, below 0.60 it is a new gap. Phase 2 is a language-model judge over the item and the top 3 chunks that returns `{verdict: covered|partial|new, missing_aspects: [...], article_id}`. For batch gap work with no latency requirement, a cross-encoder reranks the top 20 bi-encoder hits down to 5 before the judge reads them; the cross-encoder reads query and document together and separates "same topic" from "covered", for example Windows versus Mac. The thresholds 0.60 and 0.90 are starting points: cosine similarity is unreliable for error codes and product names, so every deployment needs calibration on real data. The `missing_aspects` output is the labour-saving core for editors.
- **Today.** `classify_gap(chunks)` (`klai-portal/backend/app/services/gap_classification.py:8`) is a pure threshold function: no chunks gives `hard`, all reranker scores below `klai_gap_soft_threshold` (or a dense fallback below `klai_gap_dense_threshold`) gives `soft`, otherwise `None`. A verdict with reasons exists elsewhere: the conversation judge files a gap when it blames the knowledge (`conversation_judge.py`, `file_judge_gap`), the support-case analysis files findings with one of six diagnoses (`models/retrieval_gaps.py:35`), and one `klai-judge` call per group on request writes the proposal with a `missing` field (`gap_proposals.py:65`, `draft_proposal`). No cosine 0.90/0.60 pipeline and no `covered|partial|new` verdict on the chat path.

### GAP-LOOP-03: gap lifecycle with an article back-link  ·  L  ·  PARTLY

- **Design.** Status `open`, `in_progress`, `resolved`. A resolved gap is never deleted; it carries `resolving_article_id` (the article that addressed it). If the same problem returns in at least 3 new conversations after resolution, the gap is re-opened with its full history. Worked example: "Gap G47 was first detected 2026-01-14, resolved 2026-02-28 by article X, re-opened 2026-03-12 because the article was too generic." The same idea applies to knowledge in general: history is linked, not erased.
- **Today.** `portal_retrieval_gaps` has `resolved_at` and `resolved_by` in {rescorer, review, manual, test} (`models/retrieval_gaps.py:30`; `manual` is unreachable since the resolve endpoint is gone). The rescorer `rescore_open_gaps` (`gap_rescorer.py:94`) re-asks open chat-path queries against `/retrieve` and closes rows whose `classify_gap` is now `None`; it never closes review, judge or support rows. Triggers wired: a completed connector sync (`klai-portal/backend/app/api/internal.py:747`). The endpoint `POST /internal/v1/orgs/{id}/page-saved` exists (`internal.py:1127`) and nothing in this repository calls it. The owner can decide a group for good (reject, out of scope: `app_gap_clusters.py:382`, table `portal_gap_proposals`). No `in_progress`, no `resolving_article_id`, no re-open rule, no `destination_url`; the design of closing through the destination is in [knowledge-gap-to-article.md](knowledge-gap-to-article.md).

### GAP-LOOP-04: editorial priority beyond frequency  ·  M  ·  PARTLY

- **Design.** `priority_score = frequency x urgency_weight x recency_factor`, with `urgency_weight` 2.0 when an error code is present, 1.5 for an escalation, 1.0 otherwise. The editorial list shows per gap: type, target article, frequency, what is missing and example agent responses from real conversations.
- **Today.** The list orders groups by distinct conversations, then severity of the kind, then last seen, with groups that have widget turns first (`knowledge_gap_report.py:395`). Article gaps are shown by default and contact requests behind a filter (`filter_clusters`). No urgency weight and no recency factor.

### GAP-LOOP-06: AI-drafted articles written into a knowledge base  ·  L  ·  PARTLY

- **Design.** "Human writes or AI drafts new or updated article": the AI may draft from a gap group, a human authorises, and nothing is published without that human. One explicit accept-and-publish action for the owner is the end state in [support-gap-detection.md](support-gap-detection.md); the destination per knowledge base (Klai Docs draft page with `draft: true` that ingest skips, Notion, `BaseAdapter.write_draft`, `destination_url`) is phase 3 of [knowledge-gap-to-article.md](knowledge-gap-to-article.md).
- **Today.** `POST /api/app/gaps/clusters/{key}/proposal` (`klai-portal/backend/app/api/app_gap_clusters.py:283`, `create_gap_proposal`) stores one proposal per group in `portal_gap_proposals`: what is missing, where it belongs, an example text (at most 4,000 characters) and the open facts. The owner copies it. Nothing is written to a knowledge base, Docs, Notion or a connector; `docs_client.py` has no draft function and no `write_draft` or `destination_url` exists.

### GAP-LOOP-07: weekly view, alias screen and the five measures  ·  M  ·  NOT BUILT

- **Design.** Phase 2: a weekly view with a ceiling of 20 groups, a threshold (at least 3 conversations or 2 weeks), `expires_at` after 4 weeks and a Monday mail. Phase 4: a screen to edit knowledge-base aliases and product rules. Section 5 of [knowledge-gap-to-article.md](knowledge-gap-to-article.md) names five weekly measures.
- **Today.** None of it: `list_gap_clusters` has only `days<=30` and `include_decided` (`app_gap_clusters.py:148`), no gap mail exists, and no frontend file edits `aliases` or `product_rules`; both fields are writable through the knowledge-base update API (`klai-portal/backend/app/api/app_knowledge_bases.py:812`) and applied by `knowledge_ingest/cheap_layer.py`. Code that assembles the five measures was not found.

### GAP-LOOP-08: helpdesk transcripts as an extraction adapter  ·  M  ·  BUILT differently

- **Design.** An adapter that extracts from support transcripts: `problem_summary`, `information_sought` (normalised as a search query), `unanswered_questions` (the primary gap signal), `agent_uncertainty_indicators` (exact phrases such as "I don't know"), `knowledge_gap_signal` (none, weak, strong) and `resolution.resolved`. A second pass ("gleaning") runs only when the signal is not `none` or when `resolution.resolved` is false, since the first pass under-reports exactly those cases. Native-language inference: do not translate Dutch to English before extraction. The source schema is marked illustrative in [knowledge-pipeline-architecture.md](../research/knowledge-pipeline-architecture.md).
- **Today.** Support cases are analysed by `support_case_analysis.py` (extraction, verification, retrieval against the knowledge base, assessment; `ANALYSIS_VERSION` at :52) and produce gap rows with one of six diagnoses. The fields above are not the analyzer's schema. Scribe transcripts are ingested as documents and emit no gap events.

## Open: provenance, supersession and the dual store

### GAP-PROV-01: provenance graph, entity registry and supersession analytics  ·  L  ·  PARTLY

- **Design.** A `derivations` adjacency graph, a `knowledge.entities` registry with `artifact_entities` (a `resolved` flag per link), and the `superseded_by` chain, powering an invalidation cascade ("retract src-2847, flag everything derived from it") and analytics straight on PostgreSQL. The cascade is a recursive query: start from `derivations WHERE parent_id = $source_id` and follow `child_id` through `derivations` until no new child appears. An analytic example: per entity of type `product_area`, the count of linked artifacts and how many have `resolved = false`.
- **Today.** `derivations` is written for any ingest whose frontmatter carries valid `derived_from` ids of active artifacts of the same org (`klai-knowledge-ingest/knowledge_ingest/pg_store.py:60`); `superseded_by` is written when a document is replaced (`routes/ingest.py:702`). `entities` and `artifact_entities` have no insert anywhere. Entity data that is produced lives in FalkorDB and in the payload keys `entity_uuids`, `entity_names`, `entity_pagerank_max`.

### GAP-PROV-02: derived_from chain and computed confidence  ·  L  ·  PARTLY

- **Design.** Every knowledge artifact keeps a `derived_from` chain (source and span, for example the quoted sentence of a transcript) and a `confidence` computed from the count of independent sources (example: 0.87 from 3 consistent transcripts; one transcript gives less). The chain enables the invalidation cascade, confidence calibration and temporal reasoning ("what did we believe about this procedure in Q3 2024"). Example artifact: `provenance_type: synthesized`, `synthesis_depth: 2`, three `derived_from` entries with spans, `belief_time_end` at the active sentinel.
- **Today.** `search_knowledge` returns the source `artifact_id`; `create_docs_page` can store `derived_from` (`klai-knowledge-mcp/main.py:1195`); ingest validates the ids and writes `knowledge.derivations`. Editor display, delete warnings, automatic source capture and confidence from source count are not built. `confidence` is a frontmatter label (high, medium, low), never computed and never read by retrieval.

### GAP-SYNC-01: transactional outbox and repair  ·  L  ·  PARTLY

- **Design.** The PostgreSQL write, including an `embedding_queue` row, is followed by a worker that upserts into Qdrant and marks the row processed. On a Qdrant failure the worker retries from the PostgreSQL record, which is the source of truth. A nightly reconciliation checks both directions.
- **Today.** `knowledge.embedding_queue` exists and is never inserted into (only deletes at `pg_store.py:732`, `:880`, `:985`). The write path is synchronous PostgreSQL then Qdrant then enrichment. The nightly job (`consistency_reconcile.py:175`, `30 3 * * *`) detects and alerts (`ingest-rules.yaml`, [pg-qdrant-reconcile.md](../runbooks/pg-qdrant-reconcile.md)) and does not repair. It detects Qdrant drift in both directions and, since #2341, active documents whose finished graph extraction records an episode the graph does not hold (`consistency_reconcile.py:136`). Repair is the operator script `knowledge_ingest/scripts/repair_pg_qdrant_drift.py` for Qdrant and `klai-knowledge-ingest/scripts/reextract_document_graph.py` for one graph document (dry run by default). Commit `b7b3a9896` (#2022) writes a document's new points before its old ones go.

### GAP-MODEL-01: separate types for captured sources and constructed claims  ·  M  ·  NOT BUILT

- **Design.** Two structural artifact types. A `source_document` records something that existed independently of the system (a transcript, an import); it is immutable after ingest and never serves as an answer. A `knowledge_artifact` is an intentionally constructed claim; it evolves through `superseded_by` and is what the system serves, citing source documents. A raw transcript is a source document, an extracted problem and solution pair is a knowledge artifact, and a conclusion from a human-AI session that was deliberately saved is a knowledge artifact, whatever the AI's part. The operational rule: the system serves artifacts and cites sources; it never serves a raw source as if it were a conclusion.
- **Today.** No column or code distinguishes the two (`rg source_document|knowledge_artifact` finds nothing in code). `provenance_type` and `synthesis_depth` describe origin and processing, which is the nearest approximation.

### GAP-EPIST-01: automatic assertion-mode labelling  ·  L  ·  NOT BUILT

- **Design.** The four-way split quote, paraphrase, interpretation, synthesis is not solvable with open-source tools at useful accuracy (annotators disagree in 25 to 30 percent of cases without the source text). Recommended V1: three labels (verbatim, attributed, own conclusion) in tiers. Tier 1, a structural parser for blockquotes, quotation marks and citation syntax, zero cost, expected 20 to 40 percent coverage at about 90 percent accuracy. Tier 2, a self-hosted model with 5 to 10 few-shot examples per label and a rationale; below about 0.75 confidence the item goes to review; expected accuracy 65 to 75 percent. Tier 3, human review in Label Studio (open source) with uncertainty sampling, which the design records as saving about 66 percent of annotation effort. After 500 to 1,000 labelled examples, fine-tune `deberta-v3-base`. Defer for good: the paraphrase and interpretation boundary without source text, cross-document synthesis detection, fine-grained paraphrase types. Labels are an internal signal and are never shown to users. Research: [assertion-modes-research.md](../research/assertion-modes/assertion-modes-research.md).
- **Today.** `assertion_mode` comes from frontmatter or from the MCP save tools, otherwise `unknown` (`routes/ingest.py:199`, defaults at :240). No classifier exists.

### GAP-TEMPORAL-02: point-in-time queries and a full temporal model  ·  L  ·  NOT BUILT

- **Design.** The V1 model (valid time only, active sentinel instead of null, epoch integers) is built. On top of it the design wants: "what did we know about X on date D" as a query (semantic search plus `belief_time_start <= D AND belief_time_end >= D`); "which items became outdated in the last 6 months" and "evolution of our position on topic Y" as queries on PostgreSQL; and for V2 automatic detection of temporal contradiction at ingest (the Graphiti approach of invalidating contradicted edges), interval splits mid-range (`FOR PORTION OF`, not native in PostgreSQL), temporal joins ("items active at the same time as item X") and a transaction-time audit trail (extension `temporal_tables`, or XTDB if a regulatory audit trail is needed). The sentinel avoids a known Qdrant `DatetimeRange` bug (issue 5641, December 2024).
- **Today.** Fields and filters exist (architecture document, section 3). The graph filters expired facts at query time (`retrieval_api/services/graph_search.py:280`). No endpoint or tool takes a date.

## Open: taxonomy

### GAP-TAX-01: a taxonomy that maintains itself  ·  M  ·  NOT BUILT

- **Design.** Tiered by tenant size. Under 100 documents: no automatic discovery; a shared base taxonomy maintained by Klai with tenant-level tags. 100 to 5,000 documents: topic discovery (BERTopic, or FASTopic to be evaluated, which reports better coherence and stability) once the corpus passes about 1,000 documents; the output is a proposal that a human reviews before activation, with `min_cluster_size` at about 1 to 3 percent of the document count and an expected outlier rate of 20 to 40 percent. Over 5,000: the same with a fixed `random_state` for reproducibility, monthly automatic monitoring (outlier rate, coverage per node, document velocity) and a quarterly human review of flagged items (estimated 30 to 60 minutes per tenant per quarter). Automate: tagging new documents against the approved taxonomy, alerting when more than 30 percent of a batch is unclassified, candidate labels for new clusters, merge suggestions for nodes with high cosine similarity and few documents. Always a human decision: activating, merging, renaming and splitting nodes. The review is a queue (system proposes, reviewer approves or rejects with a short reason, approved changes apply, rejected ones are logged); Argilla with active learning is the candidate for the interface, at an estimated 2 to 4 hours per quarter per reviewer. Do not use `partial_fit` online learning as the main evolution mechanism (its decay causes semantic drift); labels written by a model are plausible and need validation (Cohen's kappa 0.66 in the cited study). Quality metrics: coverage (1 minus outlier rate) as the main measure, plus NPMI or C_V coherence and topic diversity; no automatic metric predicts retrieval quality, so human sign-off stays.
- **Today.** Section 5 of the architecture document. The only discovery is the admin bootstrap `generate_bootstrap_proposals_v2` (`klai-knowledge-ingest/knowledge_ingest/routes/taxonomy.py:505`, its only caller). The registered tasks are the backfill, the hourly sweep for unclassified documents (`taxonomy_tasks.py:34`, `taxonomy-unclassified-sweep`, cron `17 * * * *`) and auto-categorise; none re-clusters, monitors outliers or suggests merges.

### GAP-TAX-02: coverage as a fraction  ·  M  ·  NOT BUILT

- **Design.** Coverage is the share of the corpus assigned to non-noise clusters.
- **Today.** No coverage fraction is computed: retrieval-api has one route, `POST /retrieve` (`klai-retrieval-api/retrieval_api/main.py:100`), and no taxonomy routes. The portal coverage widget counts chunks per node through ingest `coverage-stats` (`routes/taxonomy.py:738`).

### GAP-TAX-03: classification used at retrieval time  ·  L  ·  PARTLY

- **Design.** Per-document taxonomy classification narrows retrieval.
- **Today.** Documents are classified at ingest (centroid match first, then the `klai-ingest` alias; `taxonomy_classifier.py:57`) and the ids are stored in the payload. Retrieval filters on `taxonomy_node_ids` only when a request carries them (`search.py:466`); portal chat, the widget path and `klai-knowledge-mcp` do not send them. Gap rows store the ids (`gap_events.py:236`); no route reads them by taxonomy node, since `GET /api/app/gaps/by-taxonomy` was removed on 12 November 2026.

## Open: retrieval routing

### GAP-ROUTE-01: a routing step that scores complexity on several dimensions  ·  L  ·  NOT BUILT

- **Design.** LiteLLM's native Complexity Router, scoring every query on 7 dimensions across 4 tiers.
- **Today.** A custom callback, `deploy/litellm/custom_router.py`, with three signals: tool-call history, last-user-message token count above a limit, and web-search content; plus a 3,000 token safety net (header at :7, constants at :42). Common cases mis-route in both directions (a short but complex question goes to the fast model, a long trivial paste to the large one).

### GAP-ROUTE-02: the medium tier in routing  ·  M  ·  NOT BUILT

- **Design.** Routing across all four tiers by complexity.
- **Today.** The router assigns `klai-large`, `klai-fast` or leaves `klai-primary`; `klai-medium` is reached only by an explicit request, since no router fallback points at it (`deploy/litellm/config.yaml:365`) (`custom_router.py:182` to `:214`).

### GAP-ROUTE-03: a model tie-breaker in source routing  ·  M  ·  NOT BUILT

- **Design.** An optional language-model layer decides when keyword and centroid routing are inconclusive.
- **Today.** `route_to_sources` has one layer, the centroid match, and answers `semantic` or, when it selects nothing, `none` with no selection (`klai-retrieval-api/retrieval_api/services/router.py:226-280`). The model branch and the `router_llm_fallback` setting no longer exist (removed in #2363).

### GAP-ANALYTICS-01: analytic questions answered from PostgreSQL  ·  L  ·  NOT BUILT

- **Design.** Two mechanisms decide whether a question goes to Qdrant (semantic) or PostgreSQL (analytic). A hard-coded router: analytic signals ("how many", "most common", "trend", "rate") go to the `knowledge` schema, content signals ("what does", "how does", "explain", "steps for") to Qdrant, ambiguous ones to both. For conversational interfaces, the model gets two tools with clear descriptions and chooses by intent; both results can be combined in one answer.
- **Today.** Retrieval is always a search over Qdrant and the graph. No analytic router or analytic tool exists; the PostgreSQL tables that analytics would need (entities) are not written (GAP-PROV-01).

### GAP-ANSWER-01: a hand-off flag in the grounded answer  ·  S  ·  NOT BUILT

- **Design.** Every AI answer cites sources and the response format carries `{answer, sources: [{title, url, excerpt}], handoff}`, where `handoff` is true when retrieval confidence is below a threshold or the user asks for a person. When nothing is found, the answer says so.
- **Today.** Sources are returned. A field named `handoff` was not found in `klai-portal/backend/app/services/retrieval_context.py` or in `klai-retrieval-api/retrieval_api`. The nearest feature is the ticket a reviewer files from a conversation (`create_ticket`, `app_activity.py:1567`, [SPEC-KNOWLEDGE-ESCALATION-001](../specs/SPEC-KNOWLEDGE-ESCALATION-001/spec.md)).

## Open: tenancy, privacy and visibility

### GAP-TENANCY-01: tenant index on the existing collection, tiered shards  ·  S  ·  PARTLY

- **Design.** One collection for all tenants, with `org_id` as a tenant index (`is_tenant: true`, Qdrant 1.12 and later) so that filtered search keeps its recall, and Qdrant 1.16 tiered multitenancy to promote tenants above a vector-count threshold (for example 20,000) to dedicated shards. Collection-per-tenant is rejected because Qdrant discourages more than about 10 collections: each carries its own memory, file descriptors and index state.
- **Today.** `ensure_collection` creates the `org_id` index with `is_tenant=True` on a new collection and logs the state of an existing one (`klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:89`). Upgrading an existing collection is the operator script `klai-knowledge-ingest/scripts/upgrade_org_id_tenant_index.py`, run online. The production state of that index cannot be read from the repository; the live payload schema shows it. No `shard_key` or sharding code exists.

### GAP-PRIV-01: personal knowledge without the enrichment model calls  ·  S  ·  NOT BUILT

- **Design.** Personal knowledge is embedded directly (BGE-M3) and is not processed by contextual-prefix or HyPE enrichment or by any third-party cloud model until the legal basis is established; the pipeline for it is self-hosted only. This is framed as a compliance boundary, so the intended policy is confirmed with legal and privacy before the code changes. The change itself is a scope check in the enqueue path.
- **Today.** The enqueue path gates enrichment on chunk count and organisation configuration; `knowledge_ingest/enrichment_policy.py` has no branch on personal scope, so a personal document runs the same enrichment and graph steps as an organisation document.

### GAP-VIS-01: a visibility value for logged-in external users  ·  M  ·  NOT BUILT

- **Design.** Visibility `external` between `internal` (organisation members) and `public` (anonymous): logged-in external users such as customers with accounts. Visibility is a second access layer on top of organisation isolation, at interface level. Personal knowledge has no visibility value; it is only ever visible to its owner.
- **Today.** Values are `internal`, `public`, `private` in ingest and Qdrant, `public`/`internal` in the portal and `public`/`private` in the docs app (architecture document, section 2.5).

### GAP-PERSONAL-01: promoting a personal note to the organisation  ·  M  ·  NOT BUILT

- **Design.** A user contributes a personal note on purpose. It is an immutable copy, not a sync: a new artifact in the organisation scope with `provenance_type: synthesized`, `synthesis_depth: 1`, `derived_from` pointing to the personal note, and the author recorded; the personal note gets an informational `contributed_to_org: true`, is not changed, and the organisation artifact evolves independently. When both scopes are searched, results carry the scope so that the answer can say "from your notes" or "from the organisation's knowledge base".
- **Today.** No promotion route, no `contributed_to_org` and no scope label in answers exist (`rg contributed_to_org|source_scope|promote` finds nothing in portal, ingest, retrieval, MCP or docs).

### GAP-OFFBOARD-01: export of a personal knowledge base on offboarding  ·  M  ·  NOT BUILT

- **Design.** When a user leaves, the personal scope is offered as a markdown archive; after the export window the Qdrant points and PostgreSQL rows of that user are deleted. Organisation artifacts the user contributed stay, attributed through an `author` field. The right to erasure for personal data is then one operation that does not touch organisation knowledge.
- **Today.** `apply_dispositions` deletes the personal knowledge base through the purge chain (`klai-portal/backend/app/services/kb_offboarding.py:313`, personal case at :476) and revokes the user's partner keys and MCP tokens. There is no export step and no `author` field.

### GAP-FED-01: knowledge shared between organisations  ·  XL  ·  NOT BUILT

- **Design.** A decision is open. Two scenarios: a Klai-maintained shared baseline scope that all organisations can read (generic best practices; needs Klai governance), and opt-in federation in which organisations share chosen items with each other (feasible, operationally complex; V2 at the earliest). It needs a permission model, governance and conflict resolution that do not exist.
- **Today.** Nothing in portal, ingest, retrieval, MCP or docs (`rg federat|klai_shared_baseline`).

## Open: AI interface

### GAP-MCP-01: the read tools of the knowledge model  ·  L  ·  PARTLY

- **Design.** Five read tools that expose the depth of the model to an agent: `search(query, scope: org|personal|both, type?, time_range?)`, `related_concepts(concept_id, depth)`, `belief_evolution(topic, from_date, to_date)`, `provenance_chain(claim_id)` and `recent(days, type, scope)`. Identity comes from the authenticated session; tools take no organisation or user id.
- **Today.** `search_knowledge(query, top_k, scope, kb_slugs)` (`klai-knowledge-mcp/main.py:1546`) with `scope` and `kb_slugs`, plus the docs read tools. No `type` or `time_range` parameter and none of the other four tools.

## Index

| Id | Area | Effort | Status |
|---|---|---|---|
| GAP-LOOP-01 | judge covered, partial, new | L | PARTLY |
| GAP-LOOP-03 | gap lifecycle and back-link | L | PARTLY |
| GAP-LOOP-04 | editorial priority | M | PARTLY |
| GAP-LOOP-06 | AI draft written to a knowledge base | L | PARTLY |
| GAP-LOOP-07 | weekly view, alias screen, measures | M | NOT BUILT |
| GAP-LOOP-08 | helpdesk extraction adapter | M | BUILT differently |
| GAP-PROV-01 | provenance graph and entity registry | L | PARTLY |
| GAP-PROV-02 | derived_from chain and computed confidence | L | PARTLY |
| GAP-SYNC-01 | transactional outbox and repair | L | PARTLY |
| GAP-MODEL-01 | source and artifact types | M | NOT BUILT |
| GAP-EPIST-01 | automatic assertion-mode labelling | L | NOT BUILT |
| GAP-TEMPORAL-02 | point-in-time queries, V2 temporal model | L | NOT BUILT |
| GAP-TAX-01 | self-maintaining taxonomy | M | NOT BUILT |
| GAP-TAX-02 | coverage as a fraction | M | NOT BUILT |
| GAP-TAX-03 | classification at retrieval time | L | PARTLY |
| GAP-ROUTE-01 | multi-dimension routing | L | NOT BUILT |
| GAP-ROUTE-02 | medium tier in routing | M | NOT BUILT |
| GAP-ROUTE-03 | model tie-breaker in source routing | M | NOT BUILT |
| GAP-ANALYTICS-01 | analytic questions from PostgreSQL | L | NOT BUILT |
| GAP-ANSWER-01 | hand-off flag in the answer | S | NOT BUILT |
| GAP-TENANCY-01 | org_id index on existing collection, shards | S | PARTLY |
| GAP-PRIV-01 | personal knowledge without enrichment calls | S | NOT BUILT |
| GAP-VIS-01 | visibility for external users | M | NOT BUILT |
| GAP-PERSONAL-01 | promote personal note to organisation | M | NOT BUILT |
| GAP-OFFBOARD-01 | export on offboarding | M | NOT BUILT |
| GAP-FED-01 | sharing between organisations | XL | NOT BUILT |
| GAP-MCP-01 | read tools of the knowledge model | L | PARTLY |

Closed: GAP-TEMPORAL-01, GAP-EVID-01, GAP-EVID-02, GAP-RETR-01, GAP-EVAL-01, GAP-EVAL-02, GAP-INGEST-02, GAP-LOOP-02, GAP-LOOP-05 (table at the top).
