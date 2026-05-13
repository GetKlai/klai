# Acceptance Criteria: SPEC-MCP-RETRIEVAL-001

Given/When/Then scenarios that the implementation must pass before this SPEC can be considered complete. AC-1 through AC-10 must pass (in spec.md). Edge cases AC-11+ must pass for production sign-off.

---

## AC-1: External MCP client retrieves chunks via OAuth path

**Given** a user has authorised the OAuth client `claude-desktop` via SPEC-MCP-AUTH-001 consent flow
**And** the user's KB contains at least one document matching the query "VoIP adapter setup"
**When** the OAuth client invokes `search_knowledge(query="VoIP adapter setup", top_k=5)` with `Authorization: Bearer klai_mcp_<...>`
**Then** the tool returns a `list[dict]` with at least one item
**And** every item has keys `title`, `source_url`, `text`, `score`, `scope`
**And** the host LLM can render a citation using the `source_url` field

## AC-2: LibreChat path is byte-functionally unchanged

**Given** the existing LiteLLM `async_pre_call_hook` is unchanged in functional behaviour
**When** a LibreChat user sends any chat completion that triggers retrieval
**Then** the existing `deploy/litellm/test_klai_knowledge_*.py` test suite passes without test-code modification
**And** production retrieval-volume metrics in Grafana for the 7 days post-merge stay within ±10% of the 7 days pre-merge (assuming stable LibreChat traffic)
**And** `_klai_kb_meta` is still populated in the metadata dict for downstream `custom_router` consumption

## AC-3: MCP-call telemetry is labelled with caller_client_id

**Given** an OAuth-authenticated MCP-tool call has just succeeded
**When** querying `retrieval_log` for the matching `request_id`
**Then** the row has `caller_client_id` populated with the OAuth client's `portal_oauth_clients.client_id`
**And** the matching `product_events` row of type `knowledge.queried` has `properties->>'caller_client_id'` equal to the same value
**And** that row has `properties->>'auth_path' = 'oauth_client'`

## AC-4: LibreChat-call telemetry is unlabelled

**Given** a LibreChat retrieval-call has just succeeded
**When** querying `retrieval_log` for the matching `request_id`
**Then** the row has `caller_client_id IS NULL`
**And** the matching `product_events` row has `properties->>'auth_path' = 'librechat'` and no `caller_client_id` key

## AC-5: Retrieval-api timeout surfaces as ToolError

**Given** retrieval-api is artificially slow (mock 5-second response)
**And** the tool's `httpx.AsyncClient(timeout=3.0)` will trigger a TimeoutException
**When** an OAuth client invokes `search_knowledge`
**Then** the tool raises `mcp.server.fastmcp.exceptions.ToolError`
**And** the error message is the bilingual NL/EN generic ("Knowledge base unavailable. Please try again.")
**And** the structured log-line contains `type=TimeoutException` and `elapsed_ms=3000`
**And** the host LLM surfaces this to the user (Claude Desktop displays "Tool error" notice)

## AC-6: top_k clamping

| Input `top_k` | Forwarded to retrieval-api `top_k` |
|---:|---:|
| 0 | 1 |
| -5 | 1 |
| 1 | 1 |
| 8 (default) | 8 |
| 15 | 15 |
| 16 | 15 |
| 999 | 15 |

**Given** the tool is callable
**When** invoked with each of the inputs above
**Then** the retrieval-api receives the corresponding clamped value
**And** no error is raised on out-of-range inputs (defensive default)

## AC-7: Cross-tenant isolation

**Given** OAuth client `client-A` is authorised by user-A in org-A
**And** org-B contains documents matching the same query as org-A
**When** `client-A`'s token is used to invoke `search_knowledge` with that query
**Then** the result contains zero chunks from org-B
**And** the underlying retrieval-api `/retrieve` request body has `org_id=org-A.id`
**And** SPEC-MCP-AUTH-001 audience-binding (`resource_uri = https://mcp.getklai.com`) is verified before the call reaches the tool

## AC-8: Gap detection fires for irrelevant queries

**Given** the user's KB contains no documents matching the query "kwantum-tunnel-effectstabilisator"
**When** an OAuth client invokes `search_knowledge` with that query
**Then** retrieval-api returns chunks with low reranker scores (below `KLAI_GAP_SOFT_THRESHOLD`)
**And** `classify_gap(chunks)` returns a non-`None` gap-type
**And** `fire_gap_event` is invoked with `caller_client_id` set to the OAuth client's id
**And** the gap-event row in `product_events` has `properties->>'caller_client_id'` populated

## AC-9: Telemetry failure does not break retrieval

**Given** portal-api `/internal/v1/retrieval-log` returns HTTP 503
**When** an OAuth client invokes `search_knowledge` and retrieval-api succeeds normally
**Then** the tool returns the chunks successfully (HTTP 200 to the MCP host)
**And** a warning is logged with the telemetry-failure context
**And** no `ToolError` is raised
**And** the `product_events.knowledge.queried` and `gap_event` emits also fire-and-forget; their failures do not affect the response

## AC-10: Save-tools regression

**Given** the existing save-tool tests (`test_save_personal_knowledge`, `test_save_org_knowledge`, `test_save_to_docs`)
**When** the test suite runs after this SPEC's changes
**Then** all save-tool tests pass without modification
**And** the save-tools' upstream calls receive `_VerifiedIdentity` with the same `user_id`/`org_id`/`org_slug` shape as before
**And** the new `client_id` field is ignored by these tools (they don't read it)

---

## AC-11: Empty result set is a legitimate `[]`

**Given** retrieval-api returns `chunks: []` for a valid query
**When** the OAuth client invokes `search_knowledge`
**Then** the tool returns `[]` (NOT `ToolError`)
**And** all three telemetry fires occur (retrieval_log with empty `chunk_ids=[]`, product_event with `chunks_returned=0`, gap-event because `classify_gap([])` returns a gap-type)

## AC-12: 4xx from retrieval-api is treated as ToolError

**Given** retrieval-api returns HTTP 400 (e.g. invalid org_id, schema error)
**When** the OAuth client invokes `search_knowledge`
**Then** the tool raises `ToolError` with status-code in the log-line
**And** the user-facing message stays generic (no detail-leak)

## AC-13: Concurrent tool calls do not cross-contaminate identity

**Given** two parallel `search_knowledge` calls from two different OAuth tokens (different users/orgs) hit the same MCP-instance
**When** both complete
**Then** each call's telemetry records the correct `caller_client_id` for its own token
**And** retrieval-api receives the correct `org_id` per call (no shared mutable state)

## AC-14: Migration is non-blocking

**Given** the production `retrieval_log` table contains millions of rows
**When** the migration `ALTER TABLE retrieval_log ADD COLUMN caller_client_id TEXT NULL` runs
**Then** the operation completes in < 1 second (PostgreSQL 11+ metadata-only ADD COLUMN with NULL default)
**And** no row-level lock is acquired during the ALTER
**And** the partial index is built CONCURRENTLY in post_deploy without blocking writes

## AC-15: Schema rollback is safe

**Given** the SPEC has been deployed and `caller_client_id` rows exist
**When** the rollback SQL `DROP INDEX ... ; ALTER TABLE ... DROP COLUMN caller_client_id;` runs
**Then** the operation completes without errors
**And** no LibreChat-path data is lost (their rows had `caller_client_id IS NULL`, so dropping the column drops only the labels)
**And** OAuth-client retrieval-volume metrics for the rollback window become unrecoverable — accepted loss

## AC-16: Tool description includes citation guidance

**Given** the MCP host (Claude Desktop) lists available tools to the LLM
**When** the host queries the MCP-server's tool-list
**Then** `search_knowledge`'s description contains: WHEN TO CALL guidance, parameter semantics, return-shape description, and the explicit instruction "Cite by source_url when present; never invent URLs"
**And** the description is in English (consumed by the LLM, not the user)

## AC-17: OAuth-client without active token cannot call `search_knowledge`

**Given** a previously-issued OAuth token has been revoked via `DELETE /api/me/mcp-tokens/{id}`
**And** the Redis-cache has been invalidated within 1 second (REQ-22 of MCP-AUTH-001)
**When** the OAuth client retries `search_knowledge` with the revoked token
**Then** `_identify_request` raises `_IdentificationFailed`
**And** the MCP-protocol returns 401 with `WWW-Authenticate` header (REQ-10 of MCP-AUTH-001)
**And** no telemetry is emitted (failure happens before tool body executes)

## AC-18: OAuth scope `mcp:knowledge` covers search

**Given** an OAuth token with scope `mcp:knowledge` only
**When** the client invokes `search_knowledge`
**Then** portal-api `verify_access_token` returns `verified=true` (scope-set contains `mcp:knowledge`)
**And** the tool executes normally
**And** no separate scope-check is performed inside the tool body

## AC-19: Conversation_history is NOT propagated from MCP-pad

**Given** the MCP-tool is invoked
**When** the tool builds the retrieval-api request body
**Then** `conversation_history=[]` (empty list) is sent
**And** retrieval-api skips coreference-resolution
**And** the host LLM was responsible for resolving pronouns before calling the tool (per tool-description)

## AC-20: Retrieval-call uses `X-Caller-Service: knowledge-mcp` header

**Given** the tool calls retrieval-api `/retrieve`
**When** the request is sent
**Then** the request headers contain `X-Caller-Service: knowledge-mcp`
**And** retrieval-api `verify_body_identity` accepts this caller-service value (already in `KNOWN_CALLER_SERVICES`)

---

## Performance criteria

**P-1.** P50 end-to-end latency of `search_knowledge` (host-LLM-tool-call → tool-result-return) is ≤ 800ms when retrieval-api is healthy. P99 ≤ 2.5s. (Measured via the existing retrieval-api metrics; the tool itself adds <50ms overhead beyond the retrieval call.)

**P-2.** Telemetry emit-rate adds ≤ 5% CPU overhead on portal-api under nominal MCP-traffic load (verified via canary).

**P-3.** No memory growth in `klai-knowledge-mcp` container over a 24h period of mixed save/search traffic (verified via container-stats sampling).

---

## Quality gates

**Q-1.** All new tests achieve ≥ 90% line coverage on the changed files (`klai_retrieval_telemetry/*`, `klai-knowledge-mcp/main.py`'s new function, `mcp_oauth.py` changes).

**Q-2.** `ruff check` and `ruff format --check` and `pyright` all green on:
- `klai-knowledge-mcp/`
- `klai-portal/backend/`
- `klai-libs/retrieval-telemetry/`
- `deploy/litellm/`

**Q-3.** Existing ast-grep rules `no-secret-{eq,neq,eq-rhs}-compare`, `cors_middleware_last`, `ruff` `TRY401` (exc_info on warning) remain green.

**Q-4.** Docker image rebuilds for `klai-knowledge-mcp` and `litellm` produce images that boot successfully via `docker compose config` validation + 30-second healthcheck wait.

**Q-5.** `klai-portal/backend/scripts/rls-smoke-test.sql` continues to pass (no RLS-table changes in this SPEC).
