---
id: SPEC-RAG-RETRIEVAL-RUNTIME-RELIABILITY-001
version: "0.1.0"
status: accepted
created: 2026-08-19
updated: 2026-08-19
author: Codex
priority: high
related:
  - SPEC-RAG-CORRESPONDENCE-DISTILL-001
  - SPEC-RAG-QUERY-REWRITE-001
  - getzep/graphiti#1272
  - getzep/graphiti#1500
---

# SPEC-RAG-RETRIEVAL-RUNTIME-RELIABILITY-001

## Summary

Two independent fallbacks made the pasted-correspondence path weaker in production:

1. Query rewrite fell back to the raw email after Mistral returned HTTP 429.
2. Graph search fell back to vector-only retrieval after FalkorDB returned `Query timed out`.

Both failures already have partial solutions in the repository, but neither solution owns
the complete runtime path. The rewrite uses a process-local request bucket while bypassing
LiteLLM's token accounting. Knowledge ingest patches Graphiti's FalkorDB query and driver,
while retrieval uses the unpatched library.

This change makes the existing central systems authoritative:

- rewrite calls the loopback LiteLLM proxy as `klai-fast`, so the configured RPM/TPM limit,
  cooldown and `klai-medium` fallback apply;
- the two still-required Graphiti 0.29 compatibility fixes become one shared package used by
  both ingest and retrieval;
- compatibility overrides already fixed upstream are removed instead of carried forward.

No new external service, model, database or distributed limiter is introduced.

## Evidence and root cause

### Query rewrite

`deploy/litellm/klai_kb_query_rewrite.py::rewrite_and_classify` is a preprocessing step.
It turns the current question and optional pasted correspondence into a compact retrieval
query and optional taxonomy node IDs. It does not rewrite the final answer.

The existing `direct_mistral_limiter()` is called before every direct provider request.
Its shared `TokenBucketLimiter`, however, defines one bucket token as one **request**. It
has no prompt-token input and no view of the `klai-primary` or `klai-fast` usage tracked by
LiteLLM. Therefore it can cap direct requests per second but cannot enforce the provider's
shared tokens-per-minute budget.

The proxy already has the missing behavior:

- `klai-primary` and `klai-fast` are budgeted at 45 RPM / 45,000 TPM each;
- `enforce_model_rate_limits` is enabled;
- both aliases fall back to `klai-medium` on rate-limit and other provider failures.

LiteLLM documents that enforced RPM/TPM limits block calls before the provider, and that
configured fallbacks cover rate-limit errors. Keeping a direct third caller beside that
router duplicates quota coordination and makes its usage invisible.

### Graph search

The local locked dependency is `graphiti-core 0.29.3`. Source inspection and a deterministic
local harness prove:

- Falkor edge fulltext search still re-matches every returned relationship with
  `MATCH (n)-[e {uuid: rel.uuid}]->(m)`; upstream PR #1500 replaces this with
  `startNode(e)` / `endNode(e)` but is not released;
- `FalkorDriver.clone()` still constructs a new driver, and the constructor schedules 13
  index/constraint statements. The single-tenant retrieval path clones per request;
- upstream 0.29.3 already routes a single `group_id` to its Falkor graph and already returns
  an empty query for stopword-only input.

`klai-knowledge-ingest/knowledge_ingest/_patch_graphiti.py` contains all four behaviors as
part of a six-patch bundle written for 0.28.x. Retrieval never applies it. Copying the whole
bundle would also overwrite the two fixes now owned by 0.29.3, so only the still-required
edge-search and clone/initialization fixes are shared.

## Runtime contract

| State | Rewrite behavior | Graph behavior | User-visible retrieval |
|---|---|---|---|
| Normal | Proxy `klai-fast` succeeds | Patched graph search succeeds | Hybrid vector + graph |
| Small-model quota exhausted | Proxy attempts `klai-medium` within the 1.5 s total rewrite budget; otherwise raw-query fallback | Unchanged | Hybrid retrieval, with a weaker query if the total rewrite budget expires |
| Rewrite proxy unavailable or total timeout reached | Explicit raw-query fallback with error metadata | Still attempted | Weaker query, but request continues |
| Graph has data and is healthy | Unchanged | Tenant graph searched with shallow driver clone | Graph results enter RRF |
| Graph empty for tenant | Unchanged | Returns no graph results | Vector retrieval continues |
| Graph timeout/unavailable | Unchanged | Warning with traceback, returns no graph results | Vector retrieval continues |
| Service shutdown | No persistent client | Shared Graphiti client is closed | No leaked connection/task |

Ingest remains the owner of graph schema/index initialization. Retrieval applies the query
and clone compatibility fixes but does not run per-request schema initialization.

## Requirements

### REQ-1 — route rewrite through the existing proxy

**THE query-rewrite module SHALL** POST to the local LiteLLM OpenAI-compatible endpoint,
authenticated with `LITELLM_MASTER_KEY`, using the `klai-fast` alias by default.

**THE internal request SHALL** carry the existing `_klai_openai_passthrough` metadata flag,
so the nested proxy request does not enter knowledge retrieval recursively.

**THE call SHALL** retain the single total `QUERY_REWRITE_TIMEOUT` around the proxy request.
Any exception SHALL retain the existing explicit raw-query fallback and diagnostic metadata.
The proxy fallback is opportunistic within that same latency budget; this change does not claim
that a `klai-medium` completion always finishes before the caller timeout.

### REQ-2 — remove the duplicate rewrite quota mechanism

**THE LiteLLM deployment SHALL NOT** make direct `api.mistral.ai` calls from query rewrite or
its canary. The deploy-side vendored limiter, direct-call drift guard and direct limiter
configuration SHALL be removed. The canonical limiter remains in knowledge ingest, where it
still governs that service's own direct LLM workload.

### REQ-3 — centralize only live Graphiti compatibility fixes

**THE repository SHALL** provide one `klai-graphiti-compat` package used by knowledge ingest
and retrieval API.

For `graphiti-core >=0.29,<0.30`, it SHALL:

- generate Falkor edge fulltext Cypher with `startNode` / `endNode`, without relationship UUID
  re-matching;
- clone Falkor drivers with `copy.copy`, preserving the shared connection;
- initialize a tenant database at most once concurrently when the ingest profile requests it;
- be idempotent when applied more than once in a process.

### REQ-4 — preserve upstream-owned behavior

**THE compatibility package SHALL NOT** replace Graphiti 0.29.3's single/multi-group routing
or its existing optimized BFS and empty-fulltext handling. Knowledge-ingest's local patch module
SHALL retain only its two ingest-specific behaviors: case-insensitive node deduplication and
bidirectional edge deduplication.

### REQ-5 — apply compatibility before Graphiti use

**THE retrieval graph module SHALL** patch Graphiti's live helper references before creating
its first client. **THE ingest bootstrap SHALL** apply the shared ingest profile before graph
work.

The retrieval profile SHALL not initialize tenant schemas. The ingest profile SHALL await a
single shared initialization task before using a previously unseen tenant graph.

### REQ-6 — close the retrieval client

**THE retrieval lifespan SHALL** close and clear its lazy Graphiti client during shutdown.
Shutdown SHALL remain safe when Graphiti was disabled or never initialized.

## Acceptance criteria

| ID | Proof |
|---|---|
| AC-1 | Unit test captures a rewrite request and proves loopback proxy URL, `klai-fast`, master-key auth and internal bypass metadata. |
| AC-2 | A proxy 429/5xx/timeout still returns the raw query with `skipped=exception`; no direct provider URL remains in deploy-side Python. |
| AC-3 | Compatibility test captures Falkor fulltext Cypher and proves `startNode/endNode` are used and relationship UUID re-MATCH is absent. |
| AC-4 | Two clones for the same tenant reuse the connection and do not invoke `FalkorDriver.__init__`. |
| AC-5 | Concurrent ingest initialization for one tenant executes `build_indices_and_constraints` exactly once and all callers await it. |
| AC-6 | Retrieval imports/applies the shared package; its existing success, timeout, error and vector-fallback tests remain green. |
| AC-7 | Retrieval lifespan test proves the Graphiti client is closed and cleared. |
| AC-8 | Knowledge-ingest graph tests prove its two ingest-specific patches and Graphiti episode path remain green. |
| AC-9 | Compose validation and both affected service test suites pass. |
| AC-10 | Post-deploy canary: the two Mark emails complete rewrite without raw fallback and graph search without `Query timed out`; retrieved top-5/answer quality is compared with the recorded baseline. |

## Deliberately not done

- No Redis/distributed quota limiter: one already-configured LiteLLM proxy is the quota owner.
- No FalkorDB timeout increase: it would hide the inefficient query rather than repair it.
- No Graphiti fork or unreleased dependency pin: the two small compatibility fixes stay
  removable and are guarded by behavior tests.
- No new rewrite model or prompt changes: this change repairs reliability, not retrieval
  semantics.
- No production mutation in the implementation phase. Deployment and AC-10 require the
  normal main-branch deploy workflow and are reported separately.

## Sources

- LiteLLM, "Enforce Model Rate Limits": https://docs.litellm.ai/docs/proxy/load_balancing#enforce-model-rate-limits
- LiteLLM, "Fallbacks (Provider Failover)": https://docs.litellm.ai/docs/proxy/reliability
- Graphiti PR #1500: https://github.com/getzep/graphiti/pull/1500
- FalkorDB timeout configuration: https://docs.falkordb.com/getting-started/configuration
