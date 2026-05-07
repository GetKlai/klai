# Research: SPEC-MCP-RETRIEVAL-001

**Date:** 2026-05-07
**Author:** Mark Vletter
**Scope:** Add a `search_knowledge` MCP tool to `klai-knowledge-mcp` so third-party LLMs (Claude Desktop, Cursor, ChatGPT custom connectors) can read the user's KB via the OAuth path that SPEC-MCP-AUTH-001 already provides.

---

## 1. Why this SPEC exists

The current `klai-knowledge-mcp` ([main.py](../../klai-knowledge-mcp/main.py)) exposes three write tools (`save_personal_knowledge`, `save_org_knowledge`, `save_to_docs`) but no read tool. KB retrieval today happens exclusively inside the LiteLLM proxy via `async_pre_call_hook` ([deploy/litellm/klai_knowledge.py:1111](../../deploy/litellm/klai_knowledge.py)), which fires on every chat completion in LibreChat.

That hook is not reachable from external MCP clients. Once SPEC-MCP-AUTH-001 lands, those clients can authenticate via OAuth — but they still cannot search. This SPEC closes that gap with one additional tool.

## 2. What SPEC-MCP-AUTH-001 already gives us (verified on `hotfix/oauth-csrf-exempt`)

- **OAuth 2.1 Authorization Server** in portal-api ([app/services/mcp_oauth.py](../../klai-portal/backend/app/services/mcp_oauth.py), 895 LOC): DCR (RFC 7591), `/oauth/authorize` consent flow, `/oauth/token` with PKCE S256, refresh-token rotation with replay-detection, audience-binding (RFC 8707) on `mcp.getklai.com`.
- **Resource Server** primitives in `klai-knowledge-mcp/main.py:276-395`: `_VerifiedIdentity` dataclass and a `_identify_request(ctx)` dispatcher that branches on `Authorization: Bearer klai_mcp_*` (OAuth path) vs. `X-Internal-Secret` (LibreChat path). Both paths converge on the same identity shape, so tools never branch on which path ran.
- **Caller-service whitelist:** `knowledge-mcp` is in `KNOWN_CALLER_SERVICES` ([klai-libs/identity-assert/klai_identity_assert/models.py:116-127](../../klai-libs/identity-assert/klai_identity_assert/models.py)).
- **Retrieval-API scope:** `svc-knowledge-mcp` already holds `klai:internal:retrieval:query` ([retrieval_api/api/retrieve.py:38](../../klai-retrieval-api/retrieval_api/api/retrieve.py)).
- **Caddy route:** `mcp.getklai.com` → `klai-knowledge-mcp:8080`, DNS-rebinding protection enabled.

## 3. Verified gaps to close in this SPEC

### 3.1 `VerifyResult` does not carry `client_id`

[mcp_oauth.py:194-226](../../klai-portal/backend/app/services/mcp_oauth.py) defines `VerifyResult` with `user_id`, `org_id`, `org_slug`, `scopes`, `resource_uri`. The DB row already has `client_id` (FK→`portal_oauth_clients`) but the verify response strips it. To label telemetry per OAuth client, the verify-endpoint and the client-side asserter must propagate it.

### 3.2 `_VerifiedIdentity` does not carry `client_id`

[main.py:232-236](../../klai-knowledge-mcp/main.py) is intentionally minimal:

```python
class _VerifiedIdentity:
    user_id: str
    org_id: str
    org_slug: str
```

Adding `client_id: str | None = None` is non-breaking for existing save-tools (they don't read it) and lets the new `search_knowledge` tool tag telemetry without branching on auth-path.

### 3.3 Telemetry helpers live only in the LiteLLM hook

[deploy/litellm/klai_knowledge.py:1429-1445](../../deploy/litellm/klai_knowledge.py): `_fire_retrieval_log`, `_fire_gap_event`, `_classify_gap`, plus `product_events` `knowledge.queried` emission, are private to the LiteLLM hook process. The MCP-tool would need to duplicate them, OR they get extracted to a shared lib.

A shared lib is the right call: both callers write to the **same** `retrieval_log` table and the **same** `product_events.event_type='knowledge.queried'` stream. A duplicated copy would diverge silently on the next schema change.

### 3.4 `retrieval_log` has no `caller_client_id` column

The internal LiteLLM hook posts to `portal-api /internal/v1/retrieval-log`. To distinguish "Claude Desktop searched X" from "LibreChat user searched X" in dashboards and eval-sets, a new `caller_client_id TEXT NULL` column is required, with a partial index on `WHERE caller_client_id IS NOT NULL` so LibreChat-only queries are unaffected.

`product_events` uses a JSONB `properties` column already, so labelling there is migration-free — just add `properties->>'caller_client_id'` at emit time.

## 4. Why one tool, two parameters (and what's deliberately NOT exposed)

The LiteLLM hook does ~11 distinct things per retrieval call: trivial-filter, identity-resolve, template fetch, KB-feature gate, scope/kb_slugs/narrow flags, history-coreference, taxonomy classify, query-rewrite, gap-detection, retrieval-log, system-prompt-injection. Most of these are **only** meaningful inside the internal LibreChat UX and are wrong inside an MCP tool result:

| LiteLLM-hook concern | Belongs in MCP tool? | Why not |
|---|---|---|
| Trivial-message filter | No | External LLM doesn't pass trivial input |
| Templates (system-prompt prefix) | No | LiteLLM-pre-call concern, ties to LibreChat user-settings |
| Scope/`kb_slugs` UI flags | No | External LLM has no UI; RLS in retrieval-api enforces tenant scope automatically |
| Conversation history coreference | No | External LLM resolves pronouns itself before formulating the query |
| Query rewrite via Mistral | No | The caller IS already a frontier LLM; adding a rewrite step is wasteful |
| Taxonomy classify + filter | No | External clients don't know your taxonomy; coverage-aware filter is internal |
| Gap detection event | Yes | Highest-value telemetry — gaps surface in admin dashboards |
| Retrieval log row | Yes | Feeds RAGAS eval-set; labelling differentiates traffic distributions |
| `_klai_kb_meta` metadata signal | No | Only consumed by LiteLLM custom_router for model-upgrade |
| System-prompt injection block | No | Tool returns data, not system instructions |
| Image-URLs | Deferred (v1.1) | Requires a separate MCP resource-flow; out of scope for first iteration |

The result: a tool surface that is `search_knowledge(query, top_k=8)`. Anything more would be over-engineering.

## 5. Result-shape decision: structured chunks, not markdown

The LiteLLM hook builds a markdown system-prompt block with strict citation instructions ("STRIKT: kopieer URL exact"). For an MCP tool, that goes in the **tool description** (which the MCP host shows the LLM), not the **tool result** (which is data). The tool returns a `list[dict]` with `title`, `source_url`, `text`, `score`, `scope`. The host LLM then renders citations naturally.

This mirrors how the save-tools already work: their tool descriptions carry the "WHEN TO CALL" and parameter guidance, while the function returns plain confirmation strings.

## 6. Failure-mode design

The LiteLLM hook fails-loud-via-system-prompt: when retrieval-api is down, it injects "[Klai Kennisbank — TIJDELIJK NIET BEREIKBAAR]" into the system prompt. That makes sense inside LibreChat where the LLM call is going to fire anyway.

In an MCP tool, the equivalent is raising a `ToolError`. The MCP host (Claude Desktop, Cursor) surfaces this to the LLM, which can then explain the failure to the user. Returning empty results on retrieval failure would be wrong — the LLM would say "I couldn't find anything in your KB" when the KB was simply unreachable.

| Failure | Behaviour |
|---|---|
| retrieval-api 4xx (config error) | `ToolError` with status code |
| retrieval-api 5xx | `ToolError` with status code |
| retrieval-api timeout (3.0s) | `ToolError` with timeout reason |
| Empty result set (no chunks match) | Return `[]` (legitimate result) |
| Telemetry post fails | Log warning, never propagate (fire-and-forget) |
| Identity verify fails | Existing dispatcher path raises `_IdentificationFailed` |

## 7. Why `mcp:knowledge` covers both save and search (no new scope in v1)

SPEC-MCP-AUTH-001 issues tokens with a single scope `mcp:knowledge`. Splitting into `mcp:knowledge:read` and `mcp:knowledge:write` is a reasonable design but adds zero value until a real customer asks for read-only tokens (e.g. a publishing tool that should never modify the KB). YAGNI: the split can be added later non-breakingly by:

1. Keeping `mcp:knowledge` as a super-scope that includes both
2. Adding `mcp:knowledge:read` as a narrower scope on new clients
3. Updating consent-UI to show scope-level granularity

This SPEC stays single-scope.

## 8. Reference implementations

- **Authentication path:** the existing dispatcher at `klai-knowledge-mcp/main.py:371-395` is the contract. New tool calls `await _identify_request(ctx)` and trusts the returned `_VerifiedIdentity`.
- **Outbound retrieval call:** [deploy/litellm/klai_knowledge.py:174-212](../../deploy/litellm/klai_knowledge.py) — `_retrieve_with_dual_auth` (JWT preferred, X-Internal-Secret fallback) is the correct pattern. Either copy or import once the telemetry-lib extraction lands.
- **Telemetry emission:** the three `_fire_*` helpers in the LiteLLM hook — extract to `klai-libs/retrieval-telemetry/`.
- **Tool description style:** the existing save-tool docstrings (NL+EN trigger phrases, parameter blocks) are the template.

## 9. Risk inventory

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Telemetry-lib refactor breaks LiteLLM hook | medium | high | Phase 1 = library extraction with byte-identical behaviour for `caller_client_id=None`; regression tests in `deploy/litellm/test_klai_knowledge_*.py` must stay green untouched |
| Schema migration on `retrieval_log` blocks portal-api boot on existing rows | low | high | `ADD COLUMN ... NULL` is non-blocking; existing rows have `caller_client_id IS NULL` which means LibreChat (correct semantics retroactively) |
| `client_id` propagation breaks audience-binding | very low | critical | Existing `verify_access_token` already validates resource-binding before constructing VerifyResult; we only add a field to the success path, never weaken the deny paths |
| External LLM passes ambiguous queries → bad results → user blames Klai | medium | low | Tool-description carries "Self-contained: resolve pronouns and references yourself"; gap-events surface the failure mode in admin |
| Retrieval-api 3.0s timeout is too aggressive for cold-cache | low | medium | Same timeout as LiteLLM hook in production; if cold-start P99 spikes, raise via env var without code change |
| Telemetry rate-amplification: external client polls every keystroke | low | low | retrieval-api has its own rate limiting; OAuth-token expiry caps blast radius; revisit if a real abuse pattern shows up |

## 10. Open questions (closed)

- **Q1**: Should we expose a `scope` parameter (`personal`/`org`/`both`)? **No.** External clients have no UI and the user identity already disambiguates (`scope=both` is correct for "everything I have access to"). Future SPEC can add it if a power-user pattern emerges.
- **Q2**: Should we expose `kb_slugs` for filtering by KB? **No.** External LLMs don't know your KB structure. Add a `list_knowledge_bases` tool first if this becomes a real need.
- **Q3**: Should retrieval emit a separate audit-log row per call? **No.** REQ-25 of MCP-AUTH-001 explicitly says "Tool-call events ... worden NIET per call gelogd; `last_used_at` is voldoende". Per-call audit lives in `retrieval_log`, not in `portal_audit_log`.
- **Q4**: Should retrieval support pagination for large result sets? **No in v1.** `top_k` clamp 1-15 is sufficient for the LLM-context-fit problem; pagination is a follow-up only if a customer asks.

## 11. Cross-references

- SPEC-MCP-AUTH-001 ([../SPEC-MCP-AUTH-001/spec.md](../SPEC-MCP-AUTH-001/spec.md)) — authentication foundation; this SPEC depends on its dispatcher and `_VerifiedIdentity` shape.
- SPEC-SEC-IDENTITY-ASSERT-001 — caller-service header contract; we use `X-Caller-Service: knowledge-mcp` on outbound retrieval-api calls.
- SPEC-SEC-SERVICE-AUTH-001 — JWT scope `klai:internal:retrieval:query`; already granted to `svc-knowledge-mcp`.
- SPEC-RAG-QUERY-REWRITE-001 / SPEC-RAG-TAXONOMY-001 — internal LiteLLM-hook concerns; explicitly NOT applied in this SPEC.
- SPEC-KB-015-01 (retrieval-log) / SPEC-KB-014 (gap detection) — telemetry contracts that we extend with `caller_client_id`.
- [.claude/rules/klai/projects/knowledge-ingest.md](../../.claude/rules/klai/projects/knowledge-ingest.md) §"Multi-layer data threading in retrieval results" — confirms the result-shape contract from Qdrant → ChunkResult → JSON → frontend; we re-use the same chunk fields.
