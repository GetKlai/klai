# Implementation Plan: SPEC-MCP-RETRIEVAL-001

**Status:** draft
**Methodology:** TDD (per `.moai/config/sections/quality.yaml` development_mode for greenfield additions; library-extraction sections use DDD characterization-first patterns)
**Estimated effort:** ~halve dag implementatie + tests, exclusief manual e2e.

---

## Phase ordering

| Phase | Title | Blocker | Owner |
|---|---|---|---|
| 0 | Wait for SPEC-MCP-AUTH-001 in `main` | upstream PR merge | — |
| 1 | Extract `klai-libs/retrieval-telemetry/` (DDD: characterization-first) | Phase 0 | Backend |
| 2 | Schema + portal-api endpoint + identity propagation | Phase 1 | Backend |
| 3 | `search_knowledge` tool + tests (TDD) | Phase 2 | Backend |
| 4 | Manual e2e in Claude Desktop / Cursor | Phase 3 | Mark |

---

## Phase 0 — Wait for SPEC-MCP-AUTH-001 in main

This SPEC depends on:
- `klai-knowledge-mcp/main.py` containing `_VerifiedIdentity` + `_identify_request` dispatcher
- `klai-knowledge-mcp/dispatcher.py` with `OAUTH_ACCESS_PREFIX` + `looks_like_oauth_access_token`
- portal-api `app/services/mcp_oauth.py` with `verify_access_token` + `portal_mcp_tokens` + `portal_oauth_clients` tables
- Caddy route `mcp.getklai.com` → `klai-knowledge-mcp:8080` with DNS-rebinding protection

All present on `hotfix/oauth-csrf-exempt` (worktree `klai-mcp-auth`). Block merging this SPEC's PR until upstream is in `main`.

---

## Phase 1 — Extract telemetry helpers to shared lib

### Goal

Move four functions from `deploy/litellm/klai_knowledge.py` to a new Python package `klai-libs/retrieval-telemetry/` without changing observable behaviour.

### Affected files (NEW)

- `klai-libs/retrieval-telemetry/pyproject.toml`
- `klai-libs/retrieval-telemetry/klai_retrieval_telemetry/__init__.py`
- `klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_emit.py` (the four `fire_*` functions + `classify_gap`)
- `klai-libs/retrieval-telemetry/klai_retrieval_telemetry/_retrieve.py` (`retrieve_chunks` helper — see A4)
- `klai-libs/retrieval-telemetry/tests/test_emit.py`
- `klai-libs/retrieval-telemetry/tests/test_retrieve.py`

### Affected files (MODIFY)

- `deploy/litellm/klai_knowledge.py` — replace inline helpers with imports; pass `caller_client_id=None`, `auth_path="librechat"`
- `deploy/litellm/pyproject.toml` — add `klai-retrieval-telemetry` path-dep
- `deploy/litellm/Dockerfile` — add COPY line for the new lib (mirror knowledge-mcp Dockerfile pattern; see [.claude/rules/klai/lang/docker.md](../../.claude/rules/klai/lang/docker.md) §"uv pip install skips uv.sources")

### DDD: characterization-first

Before extracting, write tests against the **current** LiteLLM-hook behaviour:

1. `test_fire_retrieval_log_posts_to_portal_api` — record exact request shape (URL, headers, body fields, status-code handling)
2. `test_fire_gap_event_posts_with_classified_type`
3. `test_classify_gap_thresholds` — boundary values around `KLAI_GAP_SOFT_THRESHOLD` and `KLAI_GAP_DENSE_THRESHOLD`
4. `test_fire_product_event_emits_when_org_id_present`

Each test runs against the inline implementation in `deploy/litellm/klai_knowledge.py` BEFORE the extraction. Then the extraction is applied; the tests must pass against the new lib unchanged.

### Function signatures (target)

```python
# klai_retrieval_telemetry/_emit.py
async def fire_retrieval_log(
    *, org_id: str, user_id: str,
    chunk_ids: list[str], reranker_scores: list[float],
    query: str, caller_client_id: str | None = None,
) -> None: ...

async def fire_product_event_knowledge_queried(
    *, org_id: str, user_id: str,
    chunks_returned: int, retrieval_ms: int,
    auth_path: Literal["librechat", "oauth_client"],
    caller_client_id: str | None = None,
) -> None: ...

async def fire_gap_event(
    *, org_id: str, user_id: str,
    query_text: str, gap_type: str,
    chunks: list[dict], retrieval_ms: int,
    taxonomy_node_ids: list[str] | None = None,
    caller_client_id: str | None = None,
) -> None: ...

def classify_gap(chunks: list[dict]) -> str | None: ...
```

```python
# klai_retrieval_telemetry/_retrieve.py
@dataclass(frozen=True, slots=True)
class RetrievalResult:
    chunks: list[dict]
    retrieval_bypassed: bool
    raw: dict  # full /retrieve response for hook-internal use

async def retrieve_chunks(
    *, query: str, org_id: str, user_id: str, top_k: int,
    scope: Literal["personal", "org", "both", "notebook"] = "both",
    raw_query: str | None = None,
    kb_slugs: list[str] | None = None,
    conversation_history: list[dict] | None = None,
    taxonomy_node_ids: list[str] | None = None,
    timeout: float = 3.0,
) -> RetrievalResult: ...
```

The `retrieve_chunks` helper handles JWT-preferred / X-Internal-Secret-fallback auth internally (currently `_retrieve_with_dual_auth` in the LiteLLM hook). It also emits the required `X-Caller-Service: knowledge-mcp` header.

### Regression contract (REQ-24)

`deploy/litellm/test_klai_knowledge_*.py` test files must pass **without modification** after the extraction. If a test imports a private helper directly (e.g. `_classify_gap`), update only the import line; do not change assertions or fixtures.

### Configuration

Telemetry-lib reads its endpoints from env vars (or constructor args):
- `PORTAL_API_URL` (already set in both LiteLLM and knowledge-mcp containers)
- `PORTAL_INTERNAL_SECRET`
- `KNOWLEDGE_RETRIEVE_URL`
- `RETRIEVAL_INTERNAL_SECRET`
- `KLAI_OAUTH_TOKEN_URL`, `KLAI_LITELLM_CLIENT_ID`, `KLAI_LITELLM_CLIENT_SECRET` (JWT path) — for knowledge-mcp these become `KLAI_KNOWLEDGE_MCP_CLIENT_ID` etc. (separate Zitadel client; see Phase 2)

The lib does not read env directly; it accepts a `RetrievalTelemetryConfig` dataclass that callers populate from their own settings.

---

## Phase 2 — Schema, portal-api, identity propagation

### Affected files (MODIFY)

#### Database

- `klai-portal/backend/alembic/versions/XXXX_retrieval_log_caller_client_id.py` (NEW migration)
  - `ALTER TABLE retrieval_log ADD COLUMN caller_client_id TEXT NULL;`
  - `CREATE INDEX CONCURRENTLY retrieval_log_caller_client_id_idx ON retrieval_log (caller_client_id) WHERE caller_client_id IS NOT NULL;` (in `post_deploy_*.sql` because CONCURRENTLY can't run inside a transaction)
- `klai-portal/backend/alembic/versions/post_deploy_XXXX.sql` (NEW)

#### portal-api

- `klai-portal/backend/app/services/mcp_oauth.py`
  - `VerifyResult.client_id: str | None = None`
  - `VerifyResult.to_dict()` includes `client_id` in success response
  - `verify_access_token` joins `portal_oauth_clients` and populates `client_id`
  - Cache serialization includes `client_id` (cache-key unchanged)
- `klai-portal/backend/app/api/internal.py` — `/internal/v1/retrieval-log` body schema accepts optional `caller_client_id: str | None = None` and writes to the new column
- `klai-portal/backend/tests/test_mcp_oauth_unit.py` — extend tests for `client_id` propagation

#### Identity-assert lib

- `klai-libs/identity-assert/klai_identity_assert/mcp_token_client.py`
  - `VerifyResult.client_id: str | None = None`
  - Parser populates from portal response

#### knowledge-mcp

- `klai-knowledge-mcp/main.py`
  - `_VerifiedIdentity.client_id: str | None = None`
  - `_identify_via_oauth_token` sets `client_id=result.client_id`
  - `_identify_via_internal_secret` keeps default `client_id=None`
  - `@MX:ANCHOR` comment updated to mention `client_id` in the contract

### TDD test order

1. RED: `test_verify_result_includes_client_id` against portal-api unit (failing — field doesn't exist)
2. GREEN: add `client_id` to dataclass + DB query
3. RED: `test_mcp_token_client_propagates_client_id` against identity-assert lib
4. GREEN: parse `client_id` from response
5. RED: `test_oauth_path_sets_client_id_on_verified_identity` against knowledge-mcp
6. GREEN: wire `_identify_via_oauth_token` to set the field
7. RED: `test_librechat_path_keeps_client_id_none` (regression)
8. GREEN: confirm path leaves it default

### Migration safety

- Forward: `ADD COLUMN ... NULL` is non-blocking (PostgreSQL 11+: instant metadata-only change). Partial index built CONCURRENTLY in post_deploy.
- Backward: `DROP INDEX IF EXISTS retrieval_log_caller_client_id_idx; ALTER TABLE retrieval_log DROP COLUMN IF EXISTS caller_client_id;` — both safe.
- Verification: `klai-portal/backend/scripts/rls-smoke-test.sql` does not touch `retrieval_log` (Category-A, telemetry-only) — no RLS work needed.

---

## Phase 3 — `search_knowledge` tool + tests

### Affected files (MODIFY)

- `klai-knowledge-mcp/main.py` — add `@mcp.tool` `search_knowledge` after `save_to_docs` (~30 lines)
- `klai-knowledge-mcp/pyproject.toml` — add `klai-retrieval-telemetry` path-dep
- `klai-knowledge-mcp/Dockerfile` — add COPY line for the new lib

### Affected files (NEW)

- `klai-knowledge-mcp/tests/test_search_knowledge.py`
- `klai-knowledge-mcp/tests/test_search_knowledge_telemetry.py`

### Reference implementation (target — see research.md §8 for source patterns)

```python
@mcp.tool(description="""Search the user's Klai knowledge base — personal notes,
organisation docs, and connected sources (Notion, Google Drive, web crawls).

WHEN TO CALL: questions that may be answered by the user's own documentation,
decisions, customer data, or product knowledge. Search BEFORE answering from
general knowledge when the question is org-specific.

PARAMETERS:
  query  - search query in the user's language. Self-contained: resolve
           pronouns and references yourself before passing.
  top_k  - 1-15, default 8.

RETURNS: list of chunks with title, source_url, text, score, scope.
  scope="personal" = user's own saved notes.
  scope="org"      = organisation knowledge.
  Cite by source_url when present; never invent URLs.
""")
async def search_knowledge(query: str, ctx: Context, top_k: int = 8) -> list[dict]:
    identity = await _identify_request(ctx)
    top_k = max(1, min(top_k, 15))

    t0 = time.monotonic()
    try:
        result = await retrieve_chunks(
            query=query,
            org_id=identity.org_id,
            user_id=identity.user_id,
            top_k=top_k,
            scope="both",
            timeout=3.0,
        )
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.RequestError) as exc:
        logger.error(
            "search_knowledge_retrieval_failed: type=%s client_id=%s",
            type(exc).__name__, identity.client_id,
        )
        raise ToolError(_ERR_KB_UNAVAILABLE) from exc

    chunks = result.chunks
    retrieval_ms = int((time.monotonic() - t0) * 1000)

    auth_path = "oauth_client" if identity.client_id else "librechat"
    asyncio.create_task(fire_retrieval_log(
        org_id=identity.org_id, user_id=identity.user_id,
        chunk_ids=[c.get("chunk_id") for c in chunks if c.get("chunk_id")],
        reranker_scores=[c.get("reranker_score") or 0.0 for c in chunks],
        query=query, caller_client_id=identity.client_id,
    ))
    asyncio.create_task(fire_product_event_knowledge_queried(
        org_id=identity.org_id, user_id=identity.user_id,
        chunks_returned=len(chunks), retrieval_ms=retrieval_ms,
        auth_path=auth_path, caller_client_id=identity.client_id,
    ))
    gap = classify_gap(chunks)
    if gap is not None:
        asyncio.create_task(fire_gap_event(
            org_id=identity.org_id, user_id=identity.user_id,
            query_text=query, gap_type=gap, chunks=chunks,
            retrieval_ms=retrieval_ms, caller_client_id=identity.client_id,
        ))

    return [
        {
            "title": c.get("title") or c.get("metadata", {}).get("title", ""),
            "source_url": c.get("source_url") or None,
            "text": c.get("text", "").strip(),
            "score": c.get("reranker_score") or c.get("score"),
            "scope": c.get("scope", "org"),
        }
        for c in chunks
    ]
```

### Test matrix

| Test ID | Scenario | Expected |
|---|---|---|
| T-1 | Happy path: OAuth token, retrieval returns 3 chunks | `len(result) == 3`, all keys present, telemetry-3-fires-fired |
| T-2 | LibreChat path: tool callable via X-Internal-Secret too (not used in production but contract-honoured) | Returns chunks, `caller_client_id=None` in telemetry |
| T-3 | Empty results: retrieval returns `chunks=[]` | Returns `[]`, gap-event fires with `gap_type` |
| T-4 | Retrieval-api 503 | `ToolError` raised, no chunks returned, telemetry skipped |
| T-5 | Retrieval-api 4xx (e.g. 401 on retrieval-api itself) | `ToolError` raised |
| T-6 | Retrieval-api timeout (3.0s exceeded) | `ToolError` raised, log line includes elapsed_ms |
| T-7 | `top_k=20` | Clamped to 15 in upstream call |
| T-8 | `top_k=0` | Clamped to 1 |
| T-9 | `top_k=-5` | Clamped to 1 |
| T-10 | Identity verify fails (invalid token) | Existing `_IdentificationFailed` propagates (regression test from MCP-AUTH-001) |
| T-11 | Telemetry post-failure (mock 503 from portal-api) | Tool returns chunks; warning logged |
| T-12 | Cross-tenant: org-A token, retrieval response somehow contained org-B chunk | Test must fail loudly — but realistically retrieval-api enforces RLS so this is a smoke-check that we forward `org_id` correctly |
| T-13 | OAuth-token with `client_id="claude-desktop"` | Telemetry payloads contain `caller_client_id="claude-desktop"`, `auth_path="oauth_client"` |
| T-14 | Save-tools regression: `save_personal_knowledge` test suite passes unchanged | Existing assertions, no telemetry-lib changes affect them |

### Coverage target

`klai-knowledge-mcp/tests/test_search_knowledge*.py` ≥ 90% line coverage on the new tool function. Existing tests remain at their current coverage.

---

## Phase 4 — Manual e2e

### In Claude Desktop

1. `Settings → Connectors → Add custom connector`
2. URL: `https://mcp.getklai.com/mcp`
3. Browser opens to `my.getklai.com/oauth/authorize?...`; consent screen shows "Claude Desktop" name + redirect_uri
4. Approve
5. In a new chat: ask "Wat zegt onze docs over Voys SIP-trunk-config?"
6. Verify the LLM calls `search_knowledge`, receives chunks, and cites with `source_url`

### In Cursor (if MCP support is GA)

Same flow with `https://mcp.getklai.com/mcp`.

### In ChatGPT custom connectors (if available 2026-Q2)

Same flow.

### Failure-mode validation

- Revoke the OAuth token in `my.getklai.com/settings/integrations` while a chat is in flight; second call must surface 401 → Claude attempts refresh → fails → user gets disconnect signal (delegated to MCP-AUTH-001 contract).
- Trigger retrieval-api 503 (manual via `docker stop klai-retrieval-api` for 60 seconds): `search_knowledge` calls must surface tool-error in the chat UI.

### Telemetry verification

After successful e2e:

```sql
-- VictoriaLogs / Grafana PostgreSQL
SELECT caller_client_id, COUNT(*)
FROM retrieval_log
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY 1;

SELECT properties->>'caller_client_id' AS client, properties->>'auth_path' AS path, COUNT(*)
FROM product_events
WHERE event_type = 'knowledge.queried' AND created_at > NOW() - INTERVAL '1 hour'
GROUP BY 1, 2;
```

Expected: a row with `caller_client_id` = the DCR-issued client_id (string starting with `secrets.token_urlsafe(16)`-shape) and `auth_path = 'oauth_client'`.

---

## MX Tag Plan (Phase 3.5 input)

The new tool function and the modified identity dataclass are the high-impact spots. Apply tags during Phase 3 implementation:

| Location | Tag | Reason |
|---|---|---|
| `_VerifiedIdentity` class | `@MX:ANCHOR fan_in=high` | Called from every MCP-tool dispatcher branch; field-shape is a cross-service contract |
| `_VerifiedIdentity.client_id` field | `@MX:NOTE` | "None means LibreChat path; set means OAuth-issued client_id from portal_oauth_clients (REQ-3, REQ-4)" |
| `search_knowledge` function | `@MX:ANCHOR` | Public MCP-tool surface; signature stability is part of OAuth-client-contract |
| `retrieve_chunks` (telemetry-lib) | `@MX:ANCHOR fan_in=2` | LiteLLM-hook + knowledge-mcp; signature change ripples to both |
| Migration `retrieval_log_caller_client_id` | `@MX:NOTE` | "Non-blocking ADD COLUMN; partial index built CONCURRENTLY in post_deploy" |

No `@MX:WARN` candidates — no goroutines/concurrency hotspots, no high-complexity branches, no global state.

No `@MX:TODO` — Phase 3 is fully scoped.

---

## Configuration changes

### Env vars (NEW)

For `klai-knowledge-mcp` container (in `deploy/docker-compose.yml`):

- `KLAI_KNOWLEDGE_MCP_CLIENT_ID` — Zitadel service-client for retrieval-api JWT (or reuse `svc-knowledge-mcp` from existing identity-verify path)
- `KLAI_KNOWLEDGE_MCP_CLIENT_SECRET` — corresponding secret in SOPS

If the existing `svc-knowledge-mcp` Zitadel client already has `klai:internal:retrieval:query` granted (verified above — yes), no new client needed; reuse the existing token-mint env vars.

### SOPS additions

None — existing secrets cover this.

### Caddy

No changes — `mcp.getklai.com` route already exists from MCP-AUTH-001.

---

## Rollout strategy

1. **Pre-merge:** Phase 1 tests + Phase 2 tests + Phase 3 tests all green in CI.
2. **Merge:** all four phases together as one PR — splitting is awkward because the tool is non-functional without the schema + identity changes, and the telemetry-lib extraction needs the consumer changes to land in the same release to avoid two-deploys-to-validate.
3. **Deploy:** `deploy.sh portal` (migration) → `deploy.sh litellm` (telemetry-lib consumer) → `deploy.sh knowledge-mcp` (new tool). Order matters: portal must be migrated before LiteLLM/knowledge-mcp boot with the new lib that posts `caller_client_id` to a possibly-old portal endpoint (which would 422 on unknown field unless schema accepts optional).
4. **Verify:** check `service:portal-api AND level:error` in VictoriaLogs for 30 minutes post-deploy.
5. **Manual e2e:** Phase 4 within 24 hours of deploy.
6. **Watch for 7 days:** `caller_client_id IS NULL` row-rate stable (LibreChat traffic unchanged); `caller_client_id IS NOT NULL` rows growing if external clients are configured.

---

## Rollback plan

If Phase 3 (search_knowledge) misbehaves in production:

1. Remove the `@mcp.tool` decorator from `search_knowledge` in `main.py` and redeploy `knowledge-mcp` — tool disappears from the MCP-tool-list, save-tools unaffected. ~5 minutes.

If Phase 1 (telemetry-lib) misbehaves (LibreChat retrieval-quality regression):

1. Revert the `deploy/litellm/klai_knowledge.py` import changes; redeploy LiteLLM. The library files stay (no DB-state involved). ~10 minutes.

If Phase 2 (schema) needs rollback:

1. `DROP INDEX retrieval_log_caller_client_id_idx;`
2. `ALTER TABLE retrieval_log DROP COLUMN caller_client_id;`

Both are safe (no data loss for LibreChat-only rows; would lose any OAuth-client telemetry collected so far — acceptable for early-rollout window).

---

## Estimated timeline

| Phase | Hours | Cumulative |
|---|---:|---:|
| 0 (wait) | — | — |
| 1 (telemetry-lib extraction + tests) | 2.0 | 2.0 |
| 2 (schema + portal-api + identity) | 1.5 | 3.5 |
| 3 (search_knowledge tool + tests) | 1.0 | 4.5 |
| 4 (manual e2e) | 0.5 | 5.0 |

Total: ~5 hours of focused work. Real-clock time depends on CI queue + manual-test scheduling.
