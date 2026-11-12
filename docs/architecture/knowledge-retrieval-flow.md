# Knowledge Retrieval Flow: the retrieval service

> Engineering reference for `klai-retrieval-api`, the service that turns a question and a caller's scope into ranked passages and a citable evidence pack.
> Verified against the code on 12 November 2026 (`origin/main` at `3987ee2a9`), with the two later changes of 12 November (#2340, #2341) folded in and the anchors rechecked at `631b5a82f`.
>
> This document covers the service itself: who may call it, what a request means, the pipeline in the order the code runs it, what is stored and logged per request, how quality is measured, and the settings. It leaves two neighbouring subjects to their own documents:
>
> - How a chat turn uses retrieval (the case, the selection step, the writer, the gap decision) is in [chat-system.md](chat-system.md).
> - How chunks get into Qdrant, Postgres and the graph is in [knowledge-ingest-flow.md](knowledge-ingest-flow.md).
>
> The LibreChat to LiteLLM hook that used to sit in front of this service was removed on 21 October 2026 and is not described here. Every caller now talks to `/retrieve` directly.
>
> Anchors are `path:line`, relative to `klai-retrieval-api/retrieval_api/` unless the path starts with another top-level directory (`klai-portal/`, `deploy/` and so on). Where a feature exists in the code and has no effect in production, the text says "defined, not in effect" and gives the anchor.

---

## 1. The service

FastAPI app (`main.py:72`) on port 8040, image `ghcr.io/getklai/retrieval-api`, reachable only on the internal Docker network (`deploy/docker-compose.yml:1603`). There is no public route. The service is stateless apart from in-process caches; its stores are Qdrant (`klai_knowledge`), FalkorDB through Graphiti, and the portal Postgres.

| Route | What it does | Callers |
|---|---|---|
| `POST /retrieve` (`api/retrieve.py:1412`) | The pipeline in section 4 | Section 2 |
| `POST /chat` (`api/chat.py:24`) | Server-sent-event answer: coreference, embedding, hybrid search, rerank, evidence pack, then a streamed LLM answer | None in the repository. Defined, not in effect |
| `GET /internal/v1/taxonomy/{trees,coverage,tree,coverage-single}` (`api/taxonomy.py:37-80`) | Reads taxonomy nodes and coverage from the portal database | None in the repository (a portal test mocks the URL). Defined, not in effect |
| `GET /health` (`main.py:110`) | Probes the embedding service, Qdrant and LiteLLM (3 s each) and FalkorDB when the graph is enabled; any failure gives HTTP 503 `degraded` | Container health checks. Does not probe the reranker, the sparse sidecar, Redis or Postgres |
| `/metrics` (`main.py`, Prometheus ASGI mount) | Counters and histograms of section 5 | Scraped by Alloy |

Middleware order on a request: CORS (empty allow-list, so deny by default; registered last, which makes it the outermost layer, and enforced by an ast-grep check in CI), then request context (binds `request_id` and `org_id` from `X-Request-ID` and `X-Org-ID` for the logs), then authentication (`main.py:89`).

At start-up the service opens an asyncpg pool to the portal database when `PORTAL_EVENTS_HOST` is set (`services/events.py:51`, at most two connections) and sends one dummy rerank call to load the reranker model, with a 120 s timeout whose failure is only logged (`main.py:28`). Without the pool, product events, the telemetry table, parent-text lookup, alias lookup and taxonomy are silently off.

## 2. Who calls the service, and how each caller is identified

### 2.1 Callers

All callers use `POST /retrieve` with `X-Internal-Secret` and an `X-Caller-Service` header. The secret that retrieval-api accepts is `INTERNAL_SECRET`; each caller holds the value under its own setting name.

- portal-api chat: widget, partner API, internal chat: sends `X-Caller-Service` `portal-api`. Query clipped to 7800 characters; `scope` `org` for widget and partner, the profile's scope for the internal chat; `top_k` per surface; last 6 history turns, each clipped to 7800 characters; `query_variants` (two paraphrases on a first widget or partner question, the case queries on follow-ups, none for fanned-out messages); `max_sources` = `min(top_k, 8)`; `sub_queries` (up to 6); `product`; `page_context` (widget); for the internal chat also `user_id`, `scope`, `telemetry_level`, `kb_narrow` (true in strict mode; the service ignores it, see 3.1) and, for `scope=both` without `kb_slugs`, `include_owned_private_kbs` (`retrieval_context.py:855-863`). It sends neither `raw_query` nor `coreference_resolved`, so retrieval-api resolves coreference itself whenever history is not empty. Timeout 10 s widget and partner, 60 s internal. Anchor: `klai-portal/backend/app/services/retrieval_context.py:811` (body), `:875` (call), `:512-516` (timeouts); `klai-portal/backend/app/services/chat_prompting.py:104-126` (history).
- portal-api gap re-scorer: sends `X-Caller-Service` `portal-api`. `scope=org`, `top_k=5`, `purpose=background`, no `user_id`. Timeout 10 s. Anchor: `klai-portal/backend/app/services/gap_rescorer.py:149-176`.
- portal-api support-case analysis: sends `X-Caller-Service` `portal-api`. `scope=org`, exactly one `kb_slugs` entry, `user_id`, `top_k=8`, optional `raw_query`; the caller discards `graph_edge` chunks and checks each chunk's `kb_slug` against its own. Timeout 15 s. Anchor: `klai-portal/backend/app/services/support_case_analysis.py:67-69`, `:765-800`.
- portal-api shield: sends `X-Caller-Service` `portal-api`. `scope=both`, `raw_query` equal to `query`, `telemetry_level=shadow`, `effective_role=admin`; it reads `chunks` from the response and also looks for `trusted_sources` and `sources`, keys the response does not have, so its `sources` list is always empty. Timeout 5 s. Anchor: `klai-portal/backend/app/api/shield.py:596-648`.
- klai-knowledge-mcp `search_knowledge`: sends `X-Caller-Service` `knowledge-mcp`. `raw_query` equal to `query`, `top_k` clamped to 1..15, `scope` from the tool (default `both`), `kb_slugs` clamped to 20 and dropped for `personal`, `telemetry_level=shadow`, `effective_role`. Timeout 3 s. Anchor: `klai-knowledge-mcp/main.py:1546-1650`; URL from `KNOWLEDGE_RETRIEVE_URL` (`deploy/docker-compose.yml:1785`).
- knowledge-ingest RAGAS harness: sends `X-Caller-Service` `knowledge-ingest`. `query`, `org_id`, `user_id`, `kb_slugs` from the suite; every other field at its default, so `purpose=chat` and `telemetry_level=shadow`. Timeout 10 s (`rag_eval_retrieval_timeout`). Anchor: `klai-knowledge-ingest/knowledge_ingest/eval/retrieval_client.py:55-120`, `klai-knowledge-ingest/knowledge_ingest/config.py:549`.
- Measurement scripts: sends `X-Caller-Service` portal's own. `meetlat` replays a chat turn with `debug_stages=true`; the one-off launcher opens the service's Postgres connections read-only. Anchor: `klai-portal/backend/scripts/meetlat.py:846-865`, `deploy/scripts/retrieval-api-oneoff-launch.py`.

`KNOWN_CALLER_SERVICES` lists eight names: knowledge-ingest, knowledge-mcp, scribe, retrieval-api, connector, mailer, litellm and portal-api (`klai-libs/identity-assert/klai_identity_assert/models.py:157-168`). Only portal-api, knowledge-mcp and knowledge-ingest send `/retrieve` calls today.

### 2.2 Authentication (`middleware/auth.py:336`, `dispatch` at `:345`)

1. `/health` and every path under `/metrics` skip authentication (`middleware/auth.py:59`).
2. A `Authorization: Bearer` header takes precedence: a Zitadel JWT, RS256, keys from the issuer's JWKS endpoint (3 s fetch, cached in memory, refreshed on an unknown key id), issuer and audience mandatory. The scopes are the `scope` claim plus the Zitadel project-role claims. The JWT path is switched on only when `ZITADEL_ISSUER` and `ZITADEL_API_AUDIENCE` are both non-empty (`config.py:126-127`); the compose file takes the audience from `RETRIEVAL_API_ZITADEL_AUDIENCE` and defaults it to empty (`deploy/docker-compose.yml`, retrieval-api block). No caller in the repository sends a JWT to this service: portal-api (`klai-portal/backend/app/services/retrieval_context.py:896`, `gap_rescorer.py:156`, `support_case_analysis.py:784`, `api/shield.py:627`), klai-knowledge-mcp (`klai-knowledge-mcp/main.py:1641-1649`), the knowledge-ingest evaluation (`klai-knowledge-ingest/knowledge_ingest/eval/retrieval_client.py:88`) and the cross-lingual runner (`klai-retrieval-api/evaluation/cross_lingual_runner.py:114`) all send `X-Internal-Secret`. The JWT path is therefore code that the repository's own callers do not use; whether it is switched on in a given environment depends on `RETRIEVAL_API_ZITADEL_AUDIENCE`, which belongs to the environment's configuration and is operated from private infrastructure.
3. Otherwise `X-Internal-Secret` is compared in constant time with `INTERNAL_SECRET`.
4. Otherwise HTTP 401 `missing_credentials`.
5. A Redis sorted-set sliding window of 60 s limits each identity to `RATE_LIMIT_RPM` requests (default 600). The key is the hash of the JWT `sub`, or, for internal-secret callers, the TCP peer address (`middleware/auth.py:330`, `services/rate_limit.py:68`).

`POST /retrieve` additionally requires the scope `klai:internal:retrieval:query` for JWT callers; internal-secret callers pass that check (`api/retrieve.py:73-76`, `middleware/auth.py:664`).

### 2.3 Identity assertion (`verify_body_identity`, `middleware/auth.py:496`)

The body names an `org_id` and perhaps a `user_id`. The service does not trust them; it checks them against an authority before any search runs (`api/retrieve.py:520`).

- JWT with role `admin`: No cross-check; the body identity is pinned as the verified caller
- JWT, other roles: Body `user_id` must equal the JWT `sub` (403 `user_mismatch`); `X-Org-Id` is required; portal-api `/internal/identity/verify` is called with `caller_service=retrieval-api`; the body `org_id` must equal the org the portal returns (403 `org_mismatch`)
- Internal secret, body without `user_id`: `X-Caller-Service` must be present and known (400 `missing_caller_service` or `unknown_caller_service`); portal-api `/internal/identity/verify-tenant` confirms the org; the verified tenant is pinned
- Internal secret, body with `user_id`: Same header check; portal-api `/internal/identity/verify` confirms user and org (403 `identity_assertion_failed`, reason only in the logs); the verified caller from the portal's answer is pinned

The client is `klai-libs/identity-assert` (2 s timeout, 60 s cache, `klai_identity_assert/client.py:43-46`); `PORTAL_API_URL` and `PORTAL_INTERNAL_SECRET` are required at boot (`config.py:141-142`). Synthetic `partner:<key_id>` user ids are resolved by the portal (`klai-portal/backend/app/services/identity_verifier.py:114-118`), not in this service. The pinned identity is what the product event of section 5 records, never the body. `purpose=background` skips the product event and forces telemetry off, and it does not skip identity verification.

## 3. The request

### 3.1 Fields (`models.py:26-104`)

- `query`: required. The question as the pipeline searches it
- `raw_query`: none. The visitor's original words when the caller rewrote them; used for the literal leg, the evidence pack and the "caller already resolved" decision
- `coreference_resolved`: none (tri-state). True or false states the caller's coreference decision explicitly; none means "older caller", see step 2
- `org_id`: required. Zitadel org id; always a filter
- `scope`: `org`; `personal`, `org`, `both`. Which knowledge the caller may search, section 3.2
- `user_id`: none. Required (HTTP 400) for `personal` and `both` (`api/retrieve.py:475`)
- `top_k`: 8, 1..50. Passages in the answer
- `conversation_history`: empty, at most 20 entries. Entries with `content` over 8000 characters are rejected with 422, never truncated (`models.py:106-120`)
- `page_context`: none. `url`, `path`, `title`, `referrer`, `excerpt`, each length-bounded; a weak ranking hint, never a filter
- `kb_slugs`: none, at most 20. Knowledge bases to search, section 3.2
- `include_owned_private_kbs`: false. With `scope=both` and no `kb_slugs`: also every private KB the user owns
- `taxonomy_node_ids`, `tags`: none, at most 50 and 20. Extra filters (`services/search.py:466-485`). No caller in the repository sends `taxonomy_node_ids`
- `telemetry_level`: `shadow`; `off`, `shadow`, `full`. Upper bound for what is stored, section 5
- `purpose`: `chat`; `chat`, `background`. `background` means a service re-ran retrieval on its own: no product event, telemetry off
- `effective_role`: `unknown`. The value `personal` narrows the request to personal scope, section 3.2
- `sub_queries`: none, at most 6 entries of 500 characters. Two or more usable entries fan out one retrieval per sub-question, step 0b
- `query_variants`: none, at most 3 entries of 500 characters. The same question in other words, each run as its own pass, step 1
- `max_sources`: none, 1..8. Distinct sources the evidence pack may carry
- `product`: none, 100 characters. The product the question is about; a preference, never a filter
- `debug_stages`: false. Adds the source keys each stage saw to the response (measurement only)
- `kb_narrow`: false. Accepted and never read by the service (`models.py:55` is the only occurrence). Defined, not in effect

### 3.2 How a caller's scope becomes filters

Every Qdrant leg of the search carries one filter (`services/search.py:132` `_scope_filter`, `:91` `_temporal_validity_filter`, assembled in `_search_knowledge`, `:432`). Its parts:

- `org_id` equals the request's `org_id`, always.
- Temporal validity, excluding a chunk whose `invalid_at` or `valid_until` is at or before now, or whose `valid_at` or `valid_from` is after now. Both payload contracts are honoured.
- `scope=personal`: `kb_slug` equals the caller's canonical personal slug (`personal_kb_slug(user_id)`, the slug `personal-<user_id>`). There is no visibility clause, because the slug is the ownership proof. The service adds the canonical slug whatever `kb_slugs` or `effective_role` say.
- `scope=org` or `both`: a chunk passes when it is not `visibility=private`, or when it is private and its `user_id` is the caller's. For `scope=both` without `include_owned_private_kbs` the second branch also requires the chunk's `kb_slug` to be the canonical personal slug or one of the request's `kb_slugs`. Without a `user_id` only non-private chunks match.
- `kb_slugs`, when the list is non-empty (`services/search.py:209`): `kb_slug` is one of the slugs. For `scope=both` with a `user_id` the clause is "one of the slugs, or the canonical personal slug", so the personal knowledge stays in the result when org KBs are selected. For `personal` the clause is ANDed with the canonical slug.
- `taxonomy_node_ids` (matches `taxonomy_node_ids` or the older `taxonomy_node_id`) and `tags`, when given.

`effective_role=personal` makes the request `scope=personal` and drops its `kb_slugs` before anything else runs (`api/retrieve.py:487-491`).

**Empty list versus null.** On the retrieval-api side the two mean the same: `kb_slugs` of `null` or `[]` is "no knowledge-base filter", because the code tests `if request.kb_slugs:` (`services/search.py:209`). The router (`api/retrieve.py:1002-1011` and `services/router.py:36-45`), the evidence-pack source cap (`api/retrieve.py:493`) and the alias lookup (`services/search.py:293-300`) read the list the same way. An empty list therefore never means "search nothing" here.

On the caller side the portal keeps a third state. The internal chat's per-user setting is tri-state: none is every org KB, `[]` is no org KB, a list is a subset (`klai-portal/backend/app/services/chat_profile.py:91`). The portal translates before it calls: `[]` becomes `scope=personal` with no `kb_slugs`, a subset becomes `kb_slugs`, none becomes no `kb_slugs`, and `scope` is `both` when the personal knowledge base is enabled and `org` otherwise (`chat_profile.py:97-98`). `_retrieve_body` sends `kb_slugs` only when the list is non-empty (`retrieval_context.py:849`), and adds `include_owned_private_kbs=true` for `scope=both` without `kb_slugs` (`:862-863`). knowledge-mcp and the shield follow the same rule (`klai-knowledge-mcp/main.py:1622`). So no caller forwards an empty list.

- Personal off, every org KB (none): `scope=org`, no `kb_slugs`
- Personal off, subset selected: `scope=org`, `kb_slugs=[...]`
- Personal on, none selected: `scope=both`, no `kb_slugs`, `include_owned_private_kbs=true`
- Personal on, subset selected: `scope=both`, `kb_slugs=[...]`
- Personal on, empty list (no org KB): `scope=personal`, no `kb_slugs`
- Personal off, empty list: no `/retrieve` call. Open mode answers in general mode; strict mode refuses without a model call (`chat_profile.py:93-104`, `retrieval_context.py:775-781`)

## 4. The pipeline

### 4.1 The order, as a table

The orchestrator is `retrieve()` (`api/retrieve.py:1412-1451`) over a state object (`RetrievalPipelineState`, `:419`). Timeouts are per call; the service has no overall request deadline.

| # | Step | Function and anchor | Service and timeout | On failure |
|---|---|---|---|---|
| 0 | Identity and telemetry level | `_resolve_identity_and_telemetry` `api/retrieve.py:467` | Portal identity check 2 s (cached 60 s); telemetry level from Postgres (cached 5 min) | Identity: error to the caller. Telemetry lookup: falls back to `shadow`, never `full` |
| 0b | Sub-question fan-out | `_retrieve_sub_queries` `:173`, entered at `:529` | Recursion into `retrieve()`, at most 3 at once | One failed sub-question is reported in its result; all failed gives 502 |
| 1 | Query-variant passes started | `_start_query_variant_passes` `:652`, `_query_variant_pass` `:624` | Own embedding, search and rerank per variant | A failed pass is logged and skipped |
| 2 | Coreference | `_run_coreference` `:703`, `services/coreference.py:59` | LiteLLM `klai-fast`, 3.0 s | Original query is used |
| 3 | Extra query legs | `search.extra_query_legs` `services/search.py:415`, started at `api/retrieve.py:1426` | KB sample 2 s, alias lookup 1 s, translation 3.0 s, whole step 3.0 s | No extra legs, search continues |
| 4 | Embeddings | `_run_embed` `:750`, `services/tei.py` | Dense: embedding service, 120 s. Sparse: sidecar, 5.0 s | Dense failure fails the request; sparse failure drops the sparse legs |
| 5 | Source router | `_run_router` `:788`, `services/router.py:226` | Qdrant facet and scroll | No decision, request continues |
| 6 | Qdrant hybrid search, with graph search in parallel | `_run_search` `:833`, `services/search.py:432`, `services/graph_search.py:203` | Qdrant 5.0 s; graph 5.0 s plus 1.0 s | Qdrant failure fails the request; graph failure gives no graph facts |
| 6a | Graph merge | `_label_graph_results` `:367`, `_rrf_merge` `api/ranking.py:65` | Qdrant lookups, 2 s each | A fact whose document does not resolve through the request's scope filter is not merged |
| 7 | Page-context boost on candidates | `_run_page_context_candidate_boost` `:893` | none | n/a |
| 8 | Lookalike collapse, rerank, per-parent dedup | `_run_rerank` `:904`, `services/reranker.py` | Infinity `bge-reranker-v2-m3`, 60 s | Candidates pass on unscored, band becomes `unknown` |
| 9 | Rank score, page boost, product demotion | `_run_rerank` `:904` | none | n/a |
| 10 | Quality floor | `_run_quality_floor` `:971` | none | n/a. Defined, not in effect |
| 11 | Source-aware selection | `_run_source_select` `:996`, `services/diversity.py:24` | none | n/a |
| 12 | Variant fusion | `_merge_query_variant_passes` `:665` | none | n/a |
| 13 | Quality boost | `_run_quality_boost` `:1040` | none | n/a. Defined, not in effect |
| 14 | Parent-text lookup | `_run_parent_lookup` `:1123`, `services/parent_lookup.py:37` | Postgres, no explicit timeout | Child text is served |
| 15 | Response build, passage window | `_run_response_build` `:1140`, `services/parent_lookup.py:66` | none | Child text is served |
| 16 | Confidence band, evidence pack | `_run_confidence_band` `:1194`, `api/ranking.py:18`, `services/evidence_pack.py:145` | none | n/a |
| 17 | Decision record, shadow row, product event | `:1248`, `:1328` | Postgres, fire and forget | Counted as dropped, never surfaced |
| 18 | Response | `_build_response` `:1386` | none | n/a |

### 4.2 Step by step

**Step 0, identity and telemetry.** Validation, the personal-role narrowing of section 3.2, the evidence source cap (`max_sources`, else 5 when the request names more than one distinct KB, else 3, `api/retrieve.py:493`), the identity check of section 2.3, and the telemetry level (section 5) happen before any search. A structured event `retrieval_personal_scope_canonical_filter_applied` is logged for every `personal` and `both` request (`:501`).

**Step 0b, sub-question fan-out.** With two or more non-empty `sub_queries`, each runs the full pipeline as a recursive `retrieve()` call, three at a time (`sub_query_max_concurrent`), each with `top_k = min(top_k, 4)` (`sub_query_top_k`). A sub-request has `raw_query` set to its own text, `coreference_resolved` unset and `sub_queries` removed; any `query_variants` of the parent request are carried into every sub-request unchanged. The identity check runs again per sub-request (the portal's answer is cached for 60 s). A 4xx from a sub-call is re-raised as it is; other failures are recorded per sub-question in `sub_results` and only when every sub-question failed does the request return 502. The merge deduplicates chunks by `chunk_id`, namespaces the evidence ids (`Q<i>E<n>`, `Q<i>S<n>`, `services/evidence_pack.py:302`) and takes the best band over the sub-results. One `knowledge.queried` event is emitted for the original question.

**Step 1, query variants.** Each variant that differs (case-insensitively) from the query gets a background pass: its own embeddings, a hybrid search of 60 candidates, lookalike collapse, a rerank against the variant's own text to `3 * top_k`, per-parent dedup, product demotion and a cut to `top_k` (`:624-649`). The fusion happens in step 12. The passes run beside coreference, search and the main rerank.

**Step 2, coreference.** The step is skipped when the caller already decided: `coreference_resolved` true, or, when that field is absent, `raw_query` present and different from `query` (`_caller_pre_resolved`, `:151`). It is also skipped when the history is empty (`services/coreference.py:70`). Otherwise one LiteLLM chat call (`klai-fast`, temperature 0, the last 3 history turns) rewrites the query into a standalone question, with a 3.0 s deadline (`coreference_timeout`). The shared LLM-safety check runs on the input and the output, and a guard (`rewrite_preserves_subject`) rejects a rewrite that drops the subject of the original. Timeout, error, block or rejected rewrite all return the original query. Because the portal sends neither signal, this rewrite runs on every portal turn that has history.

**Step 3, extra query legs.** Started as a task right after coreference and awaited by the search (`services/search.py:415`). It can add up to two more (dense, sparse) vector pairs to the same Qdrant call:

- Translated leg. The language of the query is identified; the languages of the searched KBs come from a sample of 128 chunks read through the request's own scope filter (2 s, cached an hour, a failed sample cached a minute). A language counts when at least a quarter of the sample is in it. If the query's language is known and is not one of them, one LiteLLM call (`klai-fast`, same 3.0 s) translates the query into the KB's most common language.
- Alias leg. KB owners keep alias groups in `portal_knowledge_bases.aliases`, read from Postgres for the selected KBs of the org, plus the caller's own personal KB (1 s, cached 60 s, `services/search.py:340`). When the query mentions a term of a group, the leg is the query plus the other names of that group (`expand_with_aliases`).

**Step 4, embeddings.** The resolved query goes to the embedding service (OpenAI-compatible `POST /v1/embeddings`, `services/tei.py:26`) and to the sparse sidecar (`POST /embed_sparse_batch`, 5.0 s). When the text the visitor typed (`raw_query`, else `query`) differs from the resolved text, it is embedded as well and becomes the literal leg, so an exact term that a rewrite dropped can still enter the candidate pool (`:766-775`). The model name is sent only when `TEI_MODEL` is non-empty; the compose file leaves it empty.

**Step 5, source router.** A signal for step 11, not a filter. It runs when the router is enabled, the scope is `org` or `both`, and the searched KBs carry at least 4 distinct `source_label` values (facet, cached 600 s). It compares the query vector with a centroid per label (mean of up to 10 chunk vectors, cached 600 s); a cosine margin above 0.15 selects one label, above 0.08 two. An LLM fallback layer exists (`router_llm_fallback`, default false) and needs an `llm_fn` that `_run_router` never passes, so it cannot run (`services/router.py:240-290`, `api/retrieve.py:813-820`). Defined, not in effect.

**Step 6, Qdrant search.** One `query_points` call on `klai_knowledge` with `Fusion.RRF` over prefetch legs, each limited to `max(candidates * 4, 20)` = 240 and each carrying the filter of section 3.2; final limit `retrieval_candidates` = 60; 5.0 s (`services/search.py:490-540`). The legs:

| Leg | Vector | Present when |
|---|---|---|
| Resolved query, chunk text | `vector_chunk` | Always |
| Resolved query, generated questions | `vector_questions` | Always |
| Resolved query, sparse | `vector_sparse` | The sidecar answered |
| Literal query (inserted first) | `vector_chunk`, `vector_sparse` | The visitor's text differs from the resolved text |
| Translated query, alias-expanded query | `vector_chunk`, `vector_sparse` each | Step 3 produced them |

The literal, translated and alias legs do not include `vector_questions`. A Qdrant error is logged (`qdrant_search_failed`) and the request fails. In parallel, when `graphiti_enabled`, the graph search of section 4.3 runs as an asyncio task. The payload fields read per hit are listed in `services/search.py:545-572`.

**Step 6a, graph merge.** Each graph fact is resolved to its document (the `(kb_slug, path)` pointer, or the legacy artifact id) with one Qdrant lookup per document or artifact, at most 20, 2 s each, through the request's scope filter (`api/retrieve.py:367`, `services/search.py:658-735`). That lookup gives the fact its display fields (title, URL, source label) and decides whether it is merged: only facts whose document resolves through the filter are kept, so a graph fact follows the same knowledge-base, visibility and personal scope as the vector legs, and a fact without a pointer or with a failed lookup is not served (#2346). The decision record carries what the graph returned (`candidates_returned`) and what was merged (`in_scope`). The Qdrant list and the graph list are then merged by reciprocal rank fusion with k=60 (`api/ranking.py:65`), and the fused value replaces `score`. Graph facts get ids of the form `graph:<uuid>`. `candidates_retrieved` is the merged length, up to 70: Qdrant returns at most `retrieval_candidates` = 60 hits (`config.py:21`, `services/search.py:534`), and the graph leg is cut to Graphiti's default of 10 facts before `top_k=20` is applied (`services/graph_search.py:280-297`, `api/retrieve.py:857`).

**Step 7, page-context boost.** When `page_context.url` is given, candidates whose `source_url` is the same page or in the same path subtree get `score * 1.08` (`api/page_context.py:19`, `:47`), then the list is re-sorted.

**Step 8, lookalike collapse and rerank.** Near-identical candidates collapse to one when their 64-position MinHash fingerprints are at least 0.7 similar (`lookalike_min_similarity`); the first stays unless a later one is about the requested product. A candidate without a fingerprint, and every graph fact, is never collapsed (`services/lookalike.py:36`). The first `reranker_candidates` (60) go to Infinity (`POST /v1/rerank`, model `bge-reranker-v2-m3`, passages `context_prefix + "\n\n" + text`, `top_n = min(len, 3 * top_k)`), timeout `reranker_timeout` (code default 30 s, compose 60 s). The reranker sees the child chunk, not the parent. On any error the first `top_n` candidates pass on with `reranker_score=None` and a warning is logged (`services/reranker.py:46-56`). With the reranker disabled the result is the per-parent-deduplicated candidate list cut to `top_k`. Afterwards `_one_per_parent` (`api/retrieve.py:604`) keeps the best child per `parent_chunk_id` (or its own id), so several children of one section do not take several slots.

**Step 9, rank score and boosts.** In `active` mode (the compose default, `ranking_contract_mode`) every reranked chunk gets `final_rank_score`: the reranker score, else the search score (`:79`). The page-context boost is then applied again, to `final_rank_score` and `reranker_score`, capped at 1.0. When the request has a `product`, chunks labelled with another product lose `product_boost` (0.05); unlabelled chunks and the product's own keep their scores (`services/lookalike.py:72`). Nothing is dropped for its product alone. In `shadow` mode the serving chunks carry no `final_rank_score` and a copy replays the active pipeline for a logged comparison (`:947-953`, `:1060-1106`).

**Step 10, quality floor.** Removes chunks with `quality_score` below `retrieval_quality_floor` (0.05) (`quality_floor.py:56`). Defined, not in effect: `_search_knowledge` copies a fixed field list from the Qdrant payload and that list has no `quality_score` and no `feedback_count` (`services/search.py:545-572`), the reranker copies dicts without adding them, and graph facts have none. The filter therefore always sees the default of 0.5 and removes nothing.

**Step 11, source-aware selection.** Picks `top_k` chunks, at most `source_quota_max_per_source` (2) per `source_label` first, then fills the remainder from the leftovers regardless of label. Labels chosen by the router get +0.05 in the sort key only, and only when one of the candidates carries such a label; a personal knowledge base is never boosted by label (`services/diversity.py:24-120`, `api/retrieve.py:1000-1010`). With `source_quota_enabled` false the step is skipped and no `top_k` cut happens here.

**Step 12, variant fusion.** The main top-k and every variant's top-k are fused by reciprocal rank (k=60); membership and order follow the fusion, each chunk keeps its own reranker score, and the list is cut to `top_k` with per-parent dedup again (`:665-700`). The decision record gets `query_variants_run`, `_failed` and `_added`.

**Step 13, quality boost.** For chunks with `feedback_count` of 3 or more, `final_rank_score` is multiplied by `1 + 0.2 * (quality_score - 0.5)` (`quality_boost.py:36`). Defined, not in effect, for the reason in step 10: the two fields never reach the chunk. The portal does record votes (`klai-portal/backend/app/services/quality_scorer.py:58-110`), so the feedback loop stores data that does not change ranking.

**Steps 14 and 15, parent text and the passage window.** The `parent_chunk_id` of each served chunk is looked up in one query, `SELECT id, text FROM knowledge.parent_chunks WHERE id = ANY($1::bigint[])` (`services/parent_lookup.py:37`). The served `text` is the parent when the parent fits in 2000 characters, otherwise a 2000-character window centred on the child and trimmed to whitespace (`PARENT_WINDOW_CHARS`, `parent_window` at `services/parent_lookup.py:66`); when the child cannot be found in the parent, the child text is served. `is_parent_text` is true whenever a parent row was found, including when only the window is served. `score` in the response is `final_rank_score` in `active` mode and the search score otherwise.

**Step 16, confidence band and evidence pack.**

- Band (`api/ranking.py:18`). Take the maximum over all served chunks that have a numeric `reranker_score`, valued as `final_rank_score` when present (so the page-context boost and the product demotion count) and as `reranker_score` otherwise. At or above `confidence_band_high_threshold` (0.60) the band is `high`, below `confidence_band_low_threshold` (0.30) it is `low`, else `medium`. It is `unknown` when the reranker is disabled, nothing was served, or no chunk has a reranker score (the fallback of step 8). The thresholds are validated as `0 <= low < high <= 1` (`config.py`). The thresholds were calibrated on 24 September 2026 on 254 preview turns of one tenant: the share of answered drafts without unsupported statements was 72% for high, 67% for medium and 67% for low, with overlapping intervals, so no better cut point was shown and the defaults stayed; the band does separate refusals and off-topic outcomes (60% of low, 9% of high) (source: the comment at `config.py:86-94` and `klai-portal/backend/scripts/calibrate_confidence_bands.py`).
- Evidence pack (`services/evidence_pack.py:145`). Items are numbered `E1..`, sources `S1..`. A chunk's source key is its normalised `source_url`, else `artifact:<artifact_id>`, else the chunk is left out of the pack (`chunk_source_key`, `:125`). At most `evidence_max_sources` distinct sources are kept. `no_citable_reason` is `no_evidence` (no chunks) or `no_citable_sources` (chunks, none with a key); `below_relevance_threshold` exists in the code and is never produced, because the caller does not pass `min_relevance_score`. The `query` argument is accepted and not used. The pack is built from the response chunks, so its item text is the window of step 15.

**Step 17, record and event.** Section 5.

### 4.3 Graph search (`services/graph_search.py`)

Graphiti over FalkorDB, one graph database per org. `graphiti.search(query, group_ids=[org_id], num_results=_GRAPH_FETCH)` with `_GRAPH_FETCH` twice Graphiti's `DEFAULT_SEARCH_LIMIT` (`:49`, `:264`); facts whose `expired_at` or `invalid_at` is in the past are dropped in Python and the rest is cut to `DEFAULT_SEARCH_LIMIT` (`:280-298`, tested by `klai-retrieval-api/tests/test_graph_search_current_facts.py`). The filters the graph leg applies are the org (the group id) and fact validity; the request's `kb_slugs`, `scope` and `user_id` are not inputs of the search itself and are applied to its results in step 6a, by resolving each fact's document through the scope filter. A fact becomes a chunk-shaped dict with `content_type="graph_edge"`, `scope="org"`, score = Graphiti score times `1 + 0.1 * ln(1 + weight)` (`:382-459`). The search embedder is the same embedding model (dimension 1024, `:146-152`). A graph search makes no language-model call: in the locked `graphiti-core` 0.31.2, `Graphiti.search` runs the recipe `EDGE_HYBRID_SEARCH_RRF` (BM25 and cosine similarity over the edges, reciprocal-rank-fusion reranking) with `limit` set to `num_results` (`graphiti_core/graphiti.py:1955-1958`, `graphiti_core/search/search_config_recipes.py:111-116`), the only per-query model call in it is one embedding of the query (`graphiti_core/search/search.py:149`), and the cross-encoder, the one reranker that would call a model, is used only when a recipe asks for it (`search.py:404`). `DEFAULT_SEARCH_LIMIT` is 10 (`graphiti_core/search/search_config.py:29`), so `_GRAPH_FETCH` is 20. The Graphiti LLM client (`graphiti_llm_model`) is built and not called at search time.

Time budget: `graph_search_timeout` (5.0 s) for the search plus a separate 1.0 s to resolve each fact's episode back to a document (`_PROVENANCE_TIMEOUT`, `:52`), so 6 s at worst. A circuit breaker opens after 3 failures within 60 s and stays open for 60 s; one probe per cooldown closes it again (`:55-132`). A disabled graph, an open circuit, a timeout, an error and a query with no alphanumeric characters all return no facts, and the request goes on. `/health` answers 503 only when FalkorDB does not answer a ping.

The compose file records a measurement behind the 5 s value: over 10,151 graph searches in 30 days the mean was 252 ms and the worst case 2,679 ms (comment in `deploy/docker-compose.yml`, retrieval-api block). The `graph_ann_enabled` setting (code default false, compose true) turns on approximate-nearest-neighbour candidate search in the FalkorDB compatibility layer, with a fallback to the scan per query (`services/graph_search.py:36`).

## 5. What is stored and logged per request

### 5.1 Telemetry modes

The effective level is `min(request telemetry_level, portal_orgs.telemetry_level)`; `purpose=background` forces `off` (`api/retrieve.py:541-551`, `services/tenant_telemetry.py:90-112`, canonical level cached 5 minutes, lookup failure gives `shadow`). A caller that asks for `full` while the tenant stands on `off` therefore gets `off`.

| Level | Stored or logged |
|---|---|
| `off` | Metadata log lines and counters only |
| `shadow` | As `off`, plus one row in `telemetry.query_shadow` |
| `full` | As `shadow`, plus the query text and the coreference rewrite in the decision record |

### 5.2 Where it goes

- Log line `retrieval_decision_record`: Per-step timings, band, router decision, source-select data, graph counts, evidence-pack summary (counts, labels, ids), variant counts, `ranking_contract_mode`; query text only at `full`; `retention_class` is `content` or `metadata`. Written by `api/retrieve.py:1248-1275`, `tracing.py`.
- Log line `retrieve`: Counts and milliseconds. Written by `api/retrieve.py:1310`.
- `telemetry.query_shadow` (portal Postgres): `request_id` (primary key), `org_id`, the 1024-dimension embedding of the resolved query, symbolic features (token count, language, brand flag, question word, URL or e-mail flag), band, chunk ids, top reranker score; written at `shadow` and `full`; purged after 7 days by the portal. Written by `services/telemetry.py:30-122`, `services/features.py:88`; purge in `klai-portal/backend/app/services/telemetry_purge.py:133-144`.
- `product_events` (portal Postgres): `knowledge.queried` with `scope`, `kb_slugs`, `had_results`, `result_count`; tenant and user come from the verified identity of section 2.3. Written by `services/events.py:91`, `api/retrieve.py:1328`.
- Prometheus: Step latencies, request and chunk counts, the band counter per org, graph-contribution counter, quality-floor counter, telemetry decision counters, dropped-event counters, auth and rate-limit rejections. Written by `metrics.py`.
- Redis: The rate-limit window only (`retrieval:rl:*`). Written by `services/rate_limit.py`.

No product event is emitted for `purpose=background`, for the sub-question legs of a fan-out (the fan-out emits one), or when no verified identity is pinned (warning `product_event_skipped_no_identity`). At most `retrieval_events_max_pending` (1000) event writes are pending; beyond that an event is dropped and counted (`retrieval_events_dropped_total`). Shadow-row failures are counted in `telemetry_shadow_drop_total{reason}`. The RAGAS harness sends neither `purpose` nor `telemetry_level`, so its queries take the defaults of section 3.1 and are logged like any chat query.

The trace steps are `coreference`, `embed`, `gate`, `router`, `qdrant_search`, `graph_search`, `rerank`, `quality_floor`, `source_select`, `quality_boost`, `evidence_tier`, `parent_lookup`, `response_build`, `confidence_band` and `total` (`tracing.py:27-43`). `gate` is always recorded as skipped and `evidence_tier` is never recorded: both belong to experiments retired in August 2026 (`api/retrieve.py:562-568`, `klai-retrieval-api/tests/test_retired_retrieval_experiments.py`), and `retrieval_bypassed` in the response is always false and kept for rolling compatibility.

### 5.3 The response

`RetrieveResponse` (`models.py`): `query_resolved`; `chunks` (`ChunkResult`: ids, `text`, `context_prefix`, `heading_path`, `score`, `reranker_score`, scope, validity timestamps, `source_url`, `source_ref`, `source_connector_id`, `kb_slug`, `source_label`, `product`, `title`, `original_filename`, `image_urls`, `entity_names`, `is_parent_text`); `metadata` (candidates retrieved, reranked to, retrieval and rerank milliseconds, graph result count and milliseconds); `confidence_band`; `evidence_pack` {`items`, `sources`, `no_citable_reason`}; `sub_results` (fan-out only); `debug_stages` (only when asked: stage name to the distinct source keys for `search` and `rerank`). The response has no `sources`, `trusted_sources` or token-count field.

### 5.4 Gap classification is the caller's

The service does not classify gaps. Callers do, from the response: no chunks is a hard gap; chunks whose reranker scores are all below 0.4 is a soft gap, and when no chunk has a reranker score the search scores are compared with 0.35. The rule exists twice, in `klai-portal/backend/app/services/gap_classification.py:8` (thresholds `klai_gap_soft_threshold` and `klai_gap_dense_threshold`, `klai-portal/backend/app/core/config.py:351-352`) for the portal's chat paths and in `klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py:84` for knowledge-mcp. The retrieval log used for feedback correlation is written by the callers as well, not by this service (`klai-portal/backend/app/services/retrieval_log.py`, `klai-knowledge-mcp/main.py:1675`).

## 6. Quality measurement

### 6.1 Nightly RAGAS evaluation

The harness lives in knowledge-ingest, not in this service (`klai-knowledge-ingest/knowledge_ingest/eval/ragas_runner.py`, `register_eval_tasks` at `:604`). One Procrastinate task per suite runs on the `rag-eval` queue at 02:00 UTC for the suites `chat` and `knowledge_org`, with one attempt and a queueing lock per suite (`:674-700`). A second periodic task, the ingest-gap canary, runs at 02:30 UTC (`:651`).

The suites are YAML files read from `RAG_EVAL_SUITES_DIR`. The repository ships only a synthetic `_sample.yaml`; in production the directory is mounted read-only from outside the repository (`deploy/docker-compose.yml:2241`, `:2262`).

Per query, the harness:

1. Calls `/retrieve` (10 s), section 2.1.
2. Has `klai-ingest` (the setting `rag_eval_judge_model`) write the answer from the retrieved chunks: Dutch prompt, 512 tokens, 30 s (`klai-knowledge-ingest/knowledge_ingest/eval/judge_client.py:206-255`).
3. Scores four RAGAS metrics: `context_precision` and `context_recall` judged by `klai-ingest`, `faithfulness` judged by `klai-medium` (`rag_eval_faithfulness_model`), `answer_relevancy` judged by `klai-ingest` with the embeddings alias `klai-bge-m3` (`klai-knowledge-ingest/knowledge_ingest/config.py:549-554`). A judge call that fails stores a NULL metric, not an exception.

Results go to `knowledge.rag_eval_results` (suite, variant, query id, the four metrics, retrieved chunk ids, retrieval milliseconds, total tokens, `meta` JSON, run time; `deploy/postgres/migrations/014_rag_eval_results.sql`). A failed query keeps NULL metrics and `meta.error`. A suite entry may declare `expected_chunks`; a missing marker fails the row before RAGAS scoring and is recorded in `meta.canary`. Procedure: [rag-quality.md](../runbooks/rag-quality.md).

Alerts, in `deploy/grafana/provisioning/alerting/rag-eval-rules.yaml`; dashboard `deploy/grafana/provisioning/dashboards/rag-quality.json`:

- `rag_eval_faithfulness_low`: The suite average of `faithfulness` for `variant='baseline'` is below 0.80 on two consecutive nightly runs (evaluated every 6 hours)
- `rag_eval_canary_dropped`: Any baseline row has `meta.canary.passed = false` within 36 hours
- `RAG: Low-confidence served rate > 20%`: For an org with at least 20 requests in the hour, (`low` plus `unknown`) divided by all of `retrieval_confidence_band_total` over 1 hour exceeds 0.20 for 5 minutes
- `retrieval_graph_degraded` (`datastore-rules.yaml`): More than 30 events `graph_search_timeout`, `graph_search_failed` or `health_check_failed: falkordb` in 30 minutes

Chat-answer quality (the meetlat measurement) is portal-side: `klai-portal/backend/scripts/meetlat.py` replays turns against `/retrieve` with `debug_stages` and reports where the expected article stood after search, after rerank and in the served list. A cross-lingual retrieval run with a labelled fixture is in `klai-retrieval-api/evaluation/cross_lingual_runner.py` with `test_queries_cross_lingual.json`; it is run by hand: its own docstring says so (`cross_lingual_runner.py:1-14`), nothing in `.github/workflows/`, `deploy/` or the Makefile calls it (CI runs only its unit test, `klai-retrieval-api/tests/test_cross_lingual_runner.py`), and it posts to `/chat` (`:121`), the endpoint its own comment calls dormant in production (`:104`). Checked on 12 November 2026.

### 6.2 Contract tests in CI

- `.github/workflows/retrieval-api.yml`: dependency audit, ruff check and format, the pytest suite (about sixty test modules in `klai-retrieval-api/tests/`), an ast-grep guard that the CORS middleware is added last, then image build, scan and deploy.
- `.github/workflows/graph-contract.yml`: runs `graphiti-core` against a real FalkorDB (the image is read from the compose file) with `tests/graph_contract` of knowledge-ingest, requires the same `graphiti-core` version to be locked in knowledge-ingest and retrieval-api, and runs `klai-retrieval-api/tests/test_graph_search_current_facts.py` to prove that retrieval-api returns only current facts.
- `klai-retrieval-api/tests/test_deploy_contract.py`: the Dockerfile copies the path dependencies (`klai-libs/identity-assert`, `chat-prompts`, `citations`) before installing, and the workflow rebuilds when they change.
- A caller and the receiver agree on identity headers only if both are tested; the rule is in `.claude/rules/klai/projects/knowledge.md` ("Receiver changes require caller changes").

## 7. Configuration reference

Settings are pydantic-settings fields in `config.py`; the environment name is the upper-cased field name, without prefix. Values from `deploy/docker-compose.yml` (retrieval-api block, `:1603-1690`) are listed where they differ from the default; secrets are not listed.

| Setting | Code default | Compose | Notes |
|---|---|---|---|
| `qdrant_url`, `qdrant_collection` | `http://qdrant:6333`, `klai_knowledge` | URL set, collection not | |
| `tei_url`, `tei_model` | GPU-host bridge address on port 7997, `BAAI/bge-m3` | URL set; `TEI_MODEL` taken from the host environment, empty by default | Empty means the request carries no `model` key (`services/tei.py:19`) |
| `infinity_reranker_url` | GPU-host bridge address on port 7998 | Not set | Compose sets `TEI_RERANKER_URL`, which no code reads. Defined, not in effect |
| `litellm_url`, `litellm_api_key` | `http://litellm:4000`, empty | Set | |
| `retrieval_candidates` | 60 | | Qdrant final limit; prefetch per leg is `max(candidates * 4, 20)` |
| `reranker_candidates` | 60 | | Env `RERANKER_CANDIDATES`; it was 20 until 23 October 2026. The comment at `config.py:22-25` records: on the meetlat retrieval cases 60 returned the article of 1 of 22 internal turns that 20 missed, lost none, left the 36 widget cases unchanged, and cost 230 ms at p50 (582 to 812 ms) |
| `reranker_enabled`, `reranker_timeout` | true, 30.0 | `"true"`, `"60.0"` | |
| `sparse_sidecar_url`, `sparse_sidecar_timeout` | GPU-host bridge address on port 8001, 5.0 | URL set | |
| `coreference_model`, `coreference_timeout` | `klai-fast`, 3.0 | | The same alias and deadline serve the translation |
| `sub_query_top_k`, `sub_query_max_concurrent` | 4, 3 | | |
| `falkordb_host`, `falkordb_port` | `falkordb`, 6379 | Same | |
| `graphiti_enabled`, `graph_ann_enabled` | true, false | `"true"`, `"true"` | |
| `graph_search_timeout` | 5.0 | `"5.0"` | |
| `graphiti_llm_model` | `klai-fast` | `${GRAPHITI_LLM_MODEL:-klai-fast}` | Built, not called at search time |
| `synthesis_model` | `klai-primary` | | Only `/chat` |
| `source_quota_enabled`, `source_quota_max_per_source`, `source_preference_boost` | true, 2, 0.05 | | |
| `product_boost`, `lookalike_min_similarity` | 0.05, 0.7 | | |
| `ranking_contract_mode` | `active` | `${RANKING_CONTRACT_MODE:-active}` | `shadow` or `active`, validated at boot; `shadow` is a rollback mode |
| `retrieval_quality_floor` | 0.05 | | Env `RETRIEVAL_QUALITY_FLOOR`; no effect, step 10 |
| `confidence_band_high_threshold`, `confidence_band_low_threshold` | 0.60, 0.30 | | |
| `router_enabled`, `router_min_source_label_count`, `router_margin_single`, `router_margin_dual`, `router_centroid_ttl_seconds` | true, 4, 0.15, 0.08, 600 | | |
| `router_llm_fallback` | false | | Cannot run, step 5 |
| `portal_events_host` and the `portal_events_*` family | empty, port 5432, user and database `klai` | Host `postgres`, password set | Empty host switches off events, telemetry, parent lookup, aliases and taxonomy |
| `retrieval_events_max_pending` | 1000 | | |
| `internal_secret` | empty | Set | Boot fails when empty |
| `zitadel_issuer`, `zitadel_api_audience` | empty | Issuer set; audience from `RETRIEVAL_API_ZITADEL_AUDIENCE`, default empty | JWT path is live only with a non-empty audience |
| `rate_limit_rpm` | 600 | `${RETRIEVAL_API_RATE_LIMIT_RPM:-600}` | |
| `redis_url` | empty | Set | Boot fails when empty |
| `portal_api_url`, `portal_internal_secret` | empty | `http://portal-api:8010`, set | Boot fails when empty |

`OPENAI_API_KEY` is set to a dummy value in compose; it only satisfies the constructor of the Graphiti client.

Timeouts that are not settings: dense embedding 120 s, Qdrant search 5.0 s (the Qdrant client also 5.0 s), KB language sample 2 s, alias lookup 1 s, extra legs 3 s as a whole, display-metadata lookups 2 s each, graph provenance 1 s, JWKS fetch 3 s, health probes 3 s, the HTTP call inside coreference 120 s (the 3 s `wait_for` is the real deadline), `/chat` synthesis 120 s.

In-process caches: KB languages (1 h), aliases (60 s), router catalog and centroids (600 s), canonical telemetry level (5 min), JWKS, and the identity-assert answers (60 s). There is no result cache.

## 8. Key files

| File | Role |
|---|---|
| `klai-retrieval-api/retrieval_api/main.py` | App, middleware, lifespan, `/health` |
| `klai-retrieval-api/retrieval_api/api/retrieve.py` | The pipeline and its orchestrator |
| `klai-retrieval-api/retrieval_api/api/ranking.py` | Confidence band and graph merge |
| `klai-retrieval-api/retrieval_api/api/page_context.py` | Page-context boost |
| `klai-retrieval-api/retrieval_api/models.py` | Request and response models |
| `klai-retrieval-api/retrieval_api/config.py` | Settings |
| `klai-retrieval-api/retrieval_api/middleware/auth.py` | Authentication, rate limit, identity assertion |
| `klai-retrieval-api/retrieval_api/services/search.py` | Filters, extra legs, the Qdrant call, display metadata |
| `klai-retrieval-api/retrieval_api/services/graph_search.py` | Graph leg and circuit breaker |
| `klai-retrieval-api/retrieval_api/services/coreference.py` | Coreference and translation calls |
| `klai-retrieval-api/retrieval_api/services/reranker.py`, `tei.py` | Reranker and embedding clients |
| `klai-retrieval-api/retrieval_api/services/router.py`, `diversity.py`, `lookalike.py` | Router, source selection, lookalikes |
| `klai-retrieval-api/retrieval_api/services/parent_lookup.py` | Parent text and the passage window |
| `klai-retrieval-api/retrieval_api/services/evidence_pack.py` | Evidence pack and fan-out merge |
| `klai-retrieval-api/retrieval_api/services/telemetry.py`, `tenant_telemetry.py`, `events.py`, `features.py` | Shadow rows, telemetry level, product events |
| `klai-retrieval-api/retrieval_api/tracing.py`, `metrics.py` | Decision record and Prometheus metrics |
| `klai-libs/identity-assert/` | Identity assertion client |
| `klai-libs/retrieval-telemetry/` | Gap classification and retrieval log for knowledge-mcp |
| `klai-portal/backend/app/services/retrieval_context.py` | The portal's request builder and client |
| `klai-knowledge-ingest/knowledge_ingest/eval/` | Nightly RAGAS harness |
| `deploy/docker-compose.yml` | Environment values of the running service |
