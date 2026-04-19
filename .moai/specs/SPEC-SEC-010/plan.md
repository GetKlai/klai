# Implementation Plan: SPEC-SEC-010 — Retrieval-API Hardening

## Overview

Add an auth + bounds + cross-user/org guard layer to `klai-retrieval-api`, and update the three known callers to pass the new `X-Internal-Secret` header. The work is small in LOC (estimated ~600 LOC including tests) but spans four services and must deploy atomically.

The middleware design is dual-mode (internal-secret OR Zitadel JWT, preferring JWT when both are present) because the three callers are a mix:
- portal-api partner_chat → internal-secret path (portal already resolved the caller's org via `_get_caller_org`)
- research-api retrieval_client → JWT path (forwards the end-user's Zitadel token)
- LiteLLM knowledge hook → internal-secret path (no per-user JWT available in hook context)

## Known Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | All four services must deploy together; partial deploy breaks retrieval | One-shot `docker compose up -d` with all four images pre-built. Document in REQ-9 runbook. |
| 2 | `python-jose` is not yet a dep in retrieval-api | Add to `pyproject.toml`. Lockfile resolves cleanly (already used in research-api). |
| 3 | JWKS endpoint latency/outage could spike auth latency | Reuse research-api's 1 h in-memory JWKS cache pattern. Cold-cache outage → 503, not silent fail-open. |
| 4 | Rate-limiter on JWT `sub` can be defeated by rotating through accounts | Accepted limitation. Per-IP limit on internal path is stricter; this SPEC is primarily a defense-in-depth measure, not a DDoS shield. |
| 5 | Tests may not have a Zitadel mock today | Add a lightweight JWKS fixture + `python-jose`-signed tokens in `tests/fixtures/jwt.py`. Mirror research-api test patterns. |
| 6 | Settings validator that raises on import may break CI test collection | Use `model_validator(mode="after")` with a conditional check (`pytest` sets `INTERNAL_SECRET=test` via conftest). Document in tests. |
| 7 | Log-hashing of `sub` may break existing correlation workflows | sha256 prefix keeps logs queryable by hash; add a note in `.claude/rules/klai/infra/observability.md` after merge. |

## Task Decomposition (11 tasks)

Each task is scoped to one RED-GREEN cycle or a single coordinated caller update.

### TASK-001: Add auth module with AuthContext + InternalSecretMiddleware (fail-closed)

**Files:** `klai-retrieval-api/retrieval_api/middleware/__init__.py` (new), `klai-retrieval-api/retrieval_api/middleware/auth.py` (new)

**Content:**
- `AuthContext` dataclass (`method`, `sub`, `resourceowner`, `role`)
- `InternalSecretMiddleware(BaseHTTPMiddleware)` that validates `X-Internal-Secret` via `hmac.compare_digest`
- NO fail-open branch (in contrast to the knowledge-ingest reference implementation F-003)
- Exempts `/health`

**Requirements:** REQ-1.1, REQ-1.2 (internal path), REQ-1.3 (internal path), REQ-1.5, REQ-1.6

**Tests:** `tests/test_auth.py::test_missing_credentials_rejects_401`, `test_valid_internal_secret_accepts`, `test_invalid_internal_secret_rejects_401`, `test_health_bypass`

**Pattern source:** `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py` (shape only; remove fail-open branch)

**Size:** S (~80 impl + 100 test)

**Dependencies:** none

---

### TASK-002: Extend middleware with JWT validator and dual-credential handling

**Files:** `klai-retrieval-api/retrieval_api/middleware/auth.py` (same file, extend)

**Content:**
- JWT decode via `python-jose.jwt.decode` with `algorithms=["RS256"]`, `issuer`, `audience`
- JWKS client with 1 h in-memory cache (mirror research-api pattern)
- When both internal-secret and JWT are present and both valid → prefer JWT path
- Map JWT failure modes to explicit `reason`: `invalid_jwt_signature`, `invalid_jwt_audience`, `expired_jwt`

**Requirements:** REQ-1.2 (JWT path), REQ-1.3 (JWT path)

**Tests:** token-confusion test (`test_wrong_audience_rejects_401`), `test_expired_jwt_rejects`, `test_valid_jwt_accepts`, `test_both_credentials_prefers_jwt`

**Pattern source:** `klai-focus/research-api/app/core/auth.py` (JWKS cache + decode flow)

**Size:** M (~130 impl + 150 test)

**Dependencies:** TASK-001

---

### TASK-003: Settings — add required fields and startup validator

**Files:** `klai-retrieval-api/retrieval_api/config.py` (modify)

**Content:**
- Add `internal_secret: str`, `zitadel_issuer: str`, `zitadel_api_audience: str`, `rate_limit_rpm: int = 600`, `redis_url: str`
- `model_validator(mode="after")` that raises `ValueError("INTERNAL_SECRET must be set")` on empty/whitespace
- Same treatment for the other required settings per REQ-5.2
- Preserve the existing `RETRIEVAL_API_` env prefix

**Requirements:** REQ-1.1, REQ-5.1, REQ-5.2, REQ-5.3

**Tests:** `tests/test_config.py::test_missing_internal_secret_fails_import`, `test_all_required_present_succeeds`, one case per other required var

**Size:** S (~40 impl + 60 test)

**Dependencies:** none (can land before TASK-001)

---

### TASK-004: Register middleware in main.py

**Files:** `klai-retrieval-api/retrieval_api/main.py` (modify)

**Content:**
- `app.add_middleware(AuthMiddleware)` AFTER `RequestContextMiddleware` and BEFORE any router include
- Ensure `/health` is declared so the middleware exempt works

**Requirements:** REQ-1.4, REQ-1.6

**Tests:** integration test that `GET /health` returns 200 with no creds; `POST /retrieve` without creds returns 401

**Size:** XS (~10 LOC)

**Dependencies:** TASK-001, TASK-002, TASK-003

---

### TASK-005: Add Pydantic bounds to RetrieveRequest

**Files:** `klai-retrieval-api/retrieval_api/models.py` (modify)

**Content:**
- `top_k: int = Field(8, ge=1, le=50)`
- `conversation_history: list[dict] = Field(default_factory=list, max_length=20)`
- `kb_slugs: list[str] | None = Field(None, max_length=20)`
- `taxonomy_node_ids: list[int] | None = Field(None, max_length=50)`
- `@field_validator("conversation_history")` enforcing `len(entry["content"]) <= 8000` per entry

**Requirements:** REQ-2.1, REQ-2.2, REQ-2.3, REQ-2.4, REQ-2.5, REQ-2.6

**Tests:** `tests/test_bounds.py` — one case per bound (`test_top_k_over_limit_422`, `test_conversation_history_too_long_422`, etc.)

**Size:** S (~30 impl + 80 test)

**Dependencies:** TASK-004 (so tests can run end-to-end through auth)

---

### TASK-006: Cross-user / cross-org dependency

**Files:** `klai-retrieval-api/retrieval_api/middleware/auth.py` (add helper) OR new `retrieval_api/deps/identity.py`

**Content:**
- FastAPI dependency `verify_body_identity(request: Request, body: RetrieveRequest)` — invoked on each route that takes `RetrieveRequest`
- Skipped when `request.state.auth.method == "internal"` or `request.state.auth.role == "admin"`
- Otherwise enforces `str(body.org_id) == str(auth.resourceowner)` and `str(body.user_id) == str(auth.sub)` (the latter only when body has `user_id`)
- Returns 403 `{"error": "org_mismatch"}` or `{"error": "user_mismatch"}` as appropriate
- Wired into `retrieval_api/api/retrieve.py` and `retrieval_api/api/chat.py` (both routes that take `RetrieveRequest`)

**Requirements:** REQ-3.1, REQ-3.2, REQ-3.3, REQ-3.4

**Tests:** cross-user case, cross-org case, admin bypass case, internal-secret skip case, missing user_id edge case

**Size:** M (~80 impl + 140 test)

**Dependencies:** TASK-002 (needs AuthContext populated)

---

### TASK-007: Redis sliding-window rate limiter

**Files:** `klai-retrieval-api/retrieval_api/services/rate_limit.py` (new)

**Content:**
- `async def check_and_increment(key: str, limit_per_minute: int) -> tuple[bool, int]`
- Redis ZSET pattern: `ZADD key <now> <uuid>`, `ZREMRANGEBYSCORE key 0 <now-60>`, `ZCARD key`
- Fail-open on Redis exceptions, log `WARNING rate_limiter_degraded`
- Middleware integration in `auth.py` — after auth success, compute the identity key (REQ-4.2) and call the limiter
- On 429, return `Retry-After` header in seconds (= seconds until oldest entry ages out)

**Requirements:** REQ-4.1, REQ-4.2, REQ-4.3, REQ-4.4, REQ-4.5

**Tests:** under-limit accepts, at-limit denies with Retry-After, window slides forward, Redis-down fails open

**Pattern source:** `klai-portal/backend/app/services/partner_rate_limit.py`

**Size:** M (~90 impl + 130 test)

**Dependencies:** TASK-004

---

### TASK-008: Prometheus counters + structured logs

**Files:** `klai-retrieval-api/retrieval_api/metrics.py` (modify), `retrieval_api/middleware/auth.py` (emit)

**Content:**
- Counters `retrieval_api_auth_rejected_total{reason}`, `retrieval_api_rate_limited_total{method}`, `retrieval_api_cross_user_rejected_total`, `retrieval_api_cross_org_rejected_total`
- Emit at each reject site; log at WARN with the fields listed in REQ-7.1
- Hash helper: `_hash_sub(sub: str) -> str` returns `sha256(sub).hexdigest()[:12]`

**Requirements:** REQ-7.1, REQ-7.2

**Tests:** assert counter increments in auth-reject tests (TASK-001, TASK-002, TASK-006, TASK-007)

**Size:** S (~40 impl + reuse existing tests)

**Dependencies:** TASK-006, TASK-007

---

### TASK-009: Update callers — portal-api partner_chat

**Files:** `klai-portal/backend/app/services/partner_chat.py` (modify), `klai-portal/backend/app/core/config.py` (add `retrieval_api_internal_secret`), SOPS file on core-01 (update)

**Content:**
- Read new setting `RETRIEVAL_API_INTERNAL_SECRET`
- On every call to retrieval-api, add `headers={"X-Internal-Secret": settings.retrieval_api_internal_secret}`
- Existing code path otherwise unchanged

**Requirements:** REQ-6.1, REQ-6.4

**Tests:** unit test asserts the header is sent; integration test via mocked retrieval-api asserts 200

**Size:** XS (~15 LOC)

**Dependencies:** none (can be prepared in parallel; merged last)

---

### TASK-010: Update callers — focus retrieval_client + LiteLLM hook

**Files:**
- `klai-focus/research-api/app/services/retrieval_client.py` (modify both `retrieve_broad` and `retrieve_narrow`)
- `klai-focus/research-api/app/core/config.py` (add setting)
- `deploy/litellm/klai_knowledge.py` (modify) OR `deploy/litellm/config.yaml` hook entry — whichever holds the retrieval-api call
- SOPS files for both services

**Content:** same pattern as TASK-009 — read `RETRIEVAL_API_INTERNAL_SECRET`, add header on every retrieval-api call

**Requirements:** REQ-6.2, REQ-6.3, REQ-6.4

**Tests:** mocked retrieval-api assertions; end-to-end smoke in staging

**Size:** S (~30 LOC across files)

**Dependencies:** none

---

### TASK-011: SOPS + Compose env + deploy runbook

**Files:**
- SOPS-encrypted env for retrieval-api, portal-api, research-api, LiteLLM on core-01
- `klai-infra/core-01/compose/*.yml` — wire env vars
- `klai-infra/runbooks/sec-001-rollout.md` (new) — REQ-9 deploy order + rollback

**Content:**
- Generate one strong `INTERNAL_SECRET` (32 bytes hex)
- Set `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`, `RATE_LIMIT_RPM` for retrieval-api
- Set `RETRIEVAL_API_INTERNAL_SECRET` for portal-api, research-api, LiteLLM
- Runbook: SOPS → compose env → rebuild four images → `docker compose up -d` in single step → smoke test sequence → rollback procedure

**Requirements:** REQ-9.1, REQ-9.2, REQ-9.3, REQ-5.4

**Tests:** manual smoke checklist in the runbook

**Size:** S (~2 doc pages + SOPS edits)

**Dependencies:** TASK-001 through TASK-010 merged

---

## Execution Order

**Can start in parallel (no dependencies):**
- TASK-003 (settings — doesn't touch middleware)
- TASK-009 (portal-api caller update prep — only merged at end)
- TASK-010 (focus + LiteLLM caller update prep — only merged at end)

**Then serial:**
1. TASK-001 (internal-secret middleware) — needs TASK-003
2. TASK-002 (JWT path) — needs TASK-001
3. TASK-004 (main.py wiring) — needs TASK-001..003
4. TASK-005 (bounds) — needs TASK-004
5. TASK-006 (cross-user/org) — needs TASK-002
6. TASK-007 (rate limit) — needs TASK-004
7. TASK-008 (metrics + logs) — needs TASK-006, TASK-007
8. TASK-011 (deploy) — needs ALL prior tasks merged

## Dependencies

| Dependency | Version | Purpose |
|---|---|---|
| `python-jose` | latest stable (match research-api) | JWT decode + JWKS |
| `redis` | already used in Klai stack | Sliding-window rate limit |
| `pydantic-settings` | existing | Startup validator |
| `structlog` | existing | Structured auth logs |
| `prometheus_client` | existing | New counters |

No new external infrastructure. Redis already runs in Klai's Docker Compose stack.

## Estimated Scope

- **New files:** `middleware/auth.py`, `services/rate_limit.py`, `tests/test_auth.py`, `tests/test_bounds.py`, `tests/fixtures/jwt.py`, runbook markdown
- **Modified files:** `main.py`, `models.py`, `config.py`, `metrics.py`, plus three caller files + three compose/SOPS updates
- **New env vars:** 5 (retrieval-api: `INTERNAL_SECRET`, `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`, `RATE_LIMIT_RPM`, `REDIS_URL`) + 1 per caller (`RETRIEVAL_API_INTERNAL_SECRET`)
- **Total tasks:** 11
- **Estimated LOC:** ~400 impl + ~600 test + ~50 config/runbook = ~1 050 LOC total

## Rollout Checklist (summary — full version in TASK-011 runbook)

1. Generate `INTERNAL_SECRET` and store in SOPS for all four services.
2. Set `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE` for retrieval-api (match existing research-api values).
3. Verify Redis is reachable from retrieval-api's network namespace.
4. Rebuild retrieval-api, portal-api, research-api, LiteLLM images from the SPEC branch.
5. `docker compose up -d` all four services in one step on core-01.
6. Post-deploy smoke: trigger one partner_chat retrieval, one research-api retrieval, one LiteLLM-hook retrieval. All three must return 200 and appear in VictoriaLogs with matching `request_id`.
7. If any smoke fails: roll all four images back to prior tags; keep SOPS entries (no-op for old images).
8. Confirm Prometheus counters start increasing for auth-accepted paths and no auth-rejected spikes from internal callers.
