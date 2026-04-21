---
id: SPEC-SEC-010
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: critical
---

# SPEC-SEC-010: Retrieval-API Hardening

> NOTE: The ID `SEC-010` is re-used here per the Phase 3 security-audit fix roadmap
> (`.moai/audit/99-fix-roadmap.md`). The prior NEN 7510 SPEC that previously occupied
> this directory shipped in 2026-03 (status: Done) and its content is superseded by
> this audit-driven rewrite. Git history preserves the old content.

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft, consolidating findings from the Phase 3 security audit (2026-04-19):
  - F-001 (`.moai/audit/04-tenant-isolation.md` § F-001) — retrieval-api has zero authentication
  - F-010 (`.moai/audit/04-tenant-isolation.md` § F-010) — no rate limit / request-size bounds
  - F-014 (`.moai/audit/04-2-query-inventory.md` § F-014) — body `user_id` trusted → cross-user leak within tenant
- Severity escalated to CRITICAL after PRE-B in `.moai/audit/04-3-prework-caddy.md` confirmed Zitadel org_ids are 18-digit Snowflake numerics (enumerable)
- Scope excludes kb_slugs predictability (parking lot) and Qdrant filter construction (F-013 is already correct)

---

## Goal

Harden `klai-retrieval-api` so every non-health request is authenticated, bounded, and cross-checked against the caller identity before reaching Qdrant. After this SPEC lands:

- A caller without a valid `X-Internal-Secret` OR valid Zitadel JWT is rejected at the middleware (F-001).
- A caller holding a valid Zitadel JWT for user A in tenant X cannot set `body.user_id = B` or `body.org_id = Y` and succeed (F-014).
- A caller cannot submit `top_k = 100000`, a 10 000-item conversation_history, or similarly abusive payloads (F-010).
- The service fails to start when `INTERNAL_SECRET` is unset — deploys cannot land in a fail-open state.

This is defense-in-depth: Docker-intern network isolation remains the first line. This SPEC adds a second and third line (authentication + identity binding) so that a network-segregation escape does not automatically turn into multi-tenant data exposure.

## Success Criteria

- Every request on any route except `/health` is rejected with HTTP 401 unless it carries a valid `X-Internal-Secret` header OR a valid Zitadel JWT whose `aud` matches the configured audience.
- The service exits non-zero at startup when `INTERNAL_SECRET` is unset, empty, or whitespace-only.
- With a JWT present, `body.org_id != token.resourceowner` yields HTTP 403, and `body.user_id != token.sub` yields HTTP 403 (both unless caller role is `admin`).
- Pydantic bounds return HTTP 422 for `top_k > 50`, `conversation_history` length > 20, `kb_slugs` length > 20, `taxonomy_node_ids` length > 50, and any `conversation_history[*].content` longer than 8 000 characters.
- Redis sliding-window rate limiter caps requests per caller identity (JWT `sub` OR internal-secret + source-IP) at configurable RPM (default 600).
- All three known callers (`portal-api` partner_chat, `klai-focus` retrieval_client, LiteLLM knowledge hook) send the new header and continue to work end-to-end.
- All secret comparisons use `hmac.compare_digest`.
- New automated tests cover token-confusion, cross-user, cross-org, bounds violations, startup-fail, and rate-limit.
- Test coverage on new/changed modules (`middleware/auth.py`, `models.py`, `config.py`) is ≥ 85 %.

## Environment

- **Service:** `klai-retrieval-api`, Python 3.13, FastAPI, uv
- **Port:** `8040`, Docker-intern only (not exposed via Caddy per `SERVERS.md` and confirmed in `.moai/audit/04-3-prework-caddy.md` Caddy verify)
- **Stack additions:** `python-jose` (JWT), Redis async client (already used elsewhere in Klai), structlog (already present)
- **Current middleware chain** (`retrieval_api/main.py:59`): only `RequestContextMiddleware` — no auth layer today
- **Current models** (`retrieval_api/models.py:8-17`): `RetrieveRequest(org_id, user_id, scope, top_k, conversation_history, kb_slugs, taxonomy_node_ids)` — no Pydantic `Field()` bounds
- **Known callers (MUST be updated in the same deploy):**
  - `klai-portal/backend/app/services/partner_chat.py:84` — `retrieve_context()` (portal-api)
  - `klai-focus/research-api/app/services/retrieval_client.py` — `retrieve_broad()` / `retrieve_narrow()`
  - LiteLLM knowledge hook (`deploy/litellm/klai_knowledge.py` or hook-config entry)
- **Reference implementations (read-only, pattern source):**
  - `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py` — InternalSecretMiddleware shape (note: that code is fail-open per F-003 — do NOT copy the fail-open branch)
  - `klai-focus/research-api/app/core/auth.py` — Zitadel JWT decode via `python-jose`, JWKS cache, audience verification
  - `klai-portal/backend/app/services/partner_rate_limit.py` — Redis sliding-window pattern
- **Secrets:** SOPS → Docker Compose env (`INTERNAL_SECRET`, `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`, `RATE_LIMIT_RPM`)
- **Observability:** logs via structlog → stdout → Alloy → VictoriaLogs (existing pipeline)

## Assumptions

- Network isolation (Docker-intern only, no public Caddy route) remains in place. This SPEC adds to, not replaces, network segregation.
- `portal_api` DB-role does NOT have `BYPASSRLS` (confirmed PRE-A). RLS remains the backstop for callers that flow through portal-api.
- Zitadel access tokens expose `sub` (user id) and `resourceowner` (org id, 18-digit snowflake string). This matches the claims research-api already consumes.
- All three caller services can be updated, SOPS-re-encrypted, and redeployed together in a single deploy window. No transitional "accept-without-secret" mode is required.
- The LiteLLM knowledge hook uses the internal-secret path (not per-user JWT), because its principal originates from LiteLLM team-key metadata rather than an end-user session. Cross-user checks (REQ-3) apply only when a JWT is present.
- Redis is reachable (already used by portal-api). If Redis is down, the rate-limiter MAY fail OPEN to preserve retrieval-api availability — explicit and logged, not silent.
- Qdrant filters (F-013) are correct and out of scope.
- There is no existing unit-test suite for auth in retrieval-api today; a new `tests/test_auth.py` is introduced.

## Out of Scope

- `kb_slugs` format guessability (intra-tenant cross-KB via predictable `personal-<user_id>` slugs) — parking-lot item in `.moai/audit/04-2-query-inventory.md`, may become a separate SEC SPEC.
- Qdrant filter changes in `services/search.py` — F-013 confirms those are correct today.
- Replacing the shared `INTERNAL_SECRET` with mTLS between portal-api and retrieval-api — deferred.
- Public exposure via Caddy — retrieval-api stays Docker-intern.
- Role-based authorization beyond a simple `admin` override on cross-user/cross-org checks (no per-role ACL matrix).
- Dashboards / alert rules for rejected requests in Grafana — counters exposed but dashboards are a separate infra task.
- Sibling SPECs: SEC-011 knowledge-ingest fail-closed, SEC-012 JWT audience mandatory elsewhere, SEC-004 defense-in-depth middleware for focus/scribe, SEC-008 Caddy exposure hardening.

## Security Findings Addressed

| Finding | Severity | Source | Summary |
|---|---|---|---|
| **F-001** | **CRITICAL** (escalated from HIGH) | `.moai/audit/04-tenant-isolation.md` § F-001; `.moai/audit/04-3-prework-caddy.md` § PRE-B | retrieval-api has no auth middleware and no route dependencies; `RetrieveRequest.org_id` is trusted directly from the request body. PRE-B confirmed Zitadel org_ids are 18-digit snowflake numerics → enumerable at low cost. |
| **F-010** | LOW | `.moai/audit/04-tenant-isolation.md` § F-010 | `RetrieveRequest` fields have no `Field(...)` bounds — caller can send `top_k=100000` or massive `conversation_history` lists. |
| **F-014** | HIGH | `.moai/audit/04-2-query-inventory.md` § F-014 | `RetrieveRequest.user_id` is trusted blindly when `scope=personal`. Combined with F-001 this is a full privileged-to-any-user escalation within a tenant. |

Combined severity after PRE-B: **CRITICAL**.

## Threat Model

**Asset:** Multi-tenant knowledge in Qdrant. Per-org chunks in the `klai_knowledge` collection (org_id-scoped) and per-user personal chunks in the `klai_focus` collection (tenant_id + user_id scoped).

**Attacker profile:** Any principal with network reach to `retrieval-api:8040`. Today that is limited to services on the Docker-intern `klai-net`. Tomorrow it could include a compromised sibling container, a Caddy misconfiguration that briefly proxies port 8040, a dev environment that shares secrets with prod, or (worst case) an attacker that pivots through a public-facing service (e.g. via F-017 klai-connector, now known to be publicly exposed). Because Zitadel org_ids are 18-digit snowflake numerics (PRE-B) — essentially `36XXXXXXXXXXXXXXXX` with timestamp-prefix clustering — an attacker who reaches port 8040 without auth does **not** need to guess UUIDs. The addressable space of currently-active org_ids for a multi-year-old platform is in the low thousands; at ~60 ms per unauthenticated probe and no rate limit (F-010), enumeration of every live tenant's existence takes minutes, not years. Once an attacker finds one org_id, F-014 lets them pivot from any legitimate intra-tenant foothold to any other user's `scope=personal` chunks by swapping `user_id` in the body — a classic IDOR amplification.

**Post-fix mitigation:** Without a valid `INTERNAL_SECRET` or Zitadel JWT, the attacker is rejected at the first middleware (REQ-1). An attacker who obtains a legitimate JWT for user A in tenant X cannot probe other orgs (REQ-3.1) or other users in the same org (REQ-3.2). Bounds (REQ-2) block single-request DoS. The sliding-window rate-limiter (REQ-4) caps enumeration even by an attacker who somehow rotates through valid credentials. The Qdrant-side `must` filter on `org_id` / `tenant_id` (F-013, unchanged) remains the final backstop. Net effect: combined severity returns to MEDIUM for a network-only attacker and LOW for a legitimate-but-curious JWT holder.

## Requirements

### REQ-1: Authentication Middleware

The service SHALL authenticate every request before any route handler runs and MUST fail closed on configuration errors.

**REQ-1.1:** WHEN the service starts IF `INTERNAL_SECRET` is unset, empty, or whitespace-only THEN the service SHALL log a structured error event (`level=error`, `reason=missing_internal_secret`) and exit with non-zero status. Enforced via a `pydantic-settings` `model_validator(mode="after")` in `retrieval_api/config.py`.

**REQ-1.2:** WHEN the service receives a request on any path except `/health` THEN the middleware SHALL accept the request if exactly one of the following holds:
- Header `X-Internal-Secret` is present AND `hmac.compare_digest(header_value, settings.internal_secret)` returns True, OR
- Header `Authorization: Bearer <jwt>` is present AND `python-jose.jwt.decode()` succeeds with `algorithms=["RS256"]`, `issuer=settings.zitadel_issuer`, and `audience=settings.zitadel_api_audience`.

Otherwise the middleware SHALL return HTTP 401 with body `{"error": "unauthorized"}` and bind `reason` ∈ `{missing_credentials, invalid_internal_secret, invalid_jwt_signature, invalid_jwt_audience, expired_jwt}` to the structlog context.

**REQ-1.3:** WHEN JWT validation succeeds THE middleware SHALL attach `request.state.auth = AuthContext(method="jwt", sub=<token.sub>, resourceowner=<token.resourceowner>, role=<token.role_or_None>)`. WHEN internal-secret validation succeeds THE middleware SHALL attach `request.state.auth = AuthContext(method="internal", sub=None, resourceowner=None, role="service")`. IF both credentials are present the middleware SHALL prefer the JWT path (stricter identity checks via REQ-3).

**REQ-1.4:** The middleware SHALL be registered in `retrieval_api/main.py` AFTER `RequestContextMiddleware` (so `request_id` is bound for auth-failure logs) and BEFORE router includes.

**REQ-1.5:** The middleware SHALL use `hmac.compare_digest` for secret comparison. The literal `==` SHALL NOT appear in the secret-compare code path.

**REQ-1.6:** `/health` SHALL be exempted from auth, cross-user/org checks, and rate-limit — it remains the Docker healthcheck endpoint.

### REQ-2: Request Bounds and Size Caps

The service SHALL reject oversized payloads at the Pydantic validation layer, before any downstream call.

**REQ-2.1:** `RetrieveRequest.top_k` SHALL be declared as `Field(8, ge=1, le=50)`. Values outside [1, 50] yield HTTP 422.

**REQ-2.2:** `RetrieveRequest.conversation_history` SHALL be declared as `Field(default_factory=list, max_length=20)`. Longer lists yield HTTP 422.

**REQ-2.3:** `RetrieveRequest.kb_slugs` SHALL be declared as `Field(None, max_length=20)`. Longer lists yield HTTP 422.

**REQ-2.4:** `RetrieveRequest.taxonomy_node_ids` SHALL be declared as `Field(None, max_length=50)`. Longer lists yield HTTP 422.

**REQ-2.5:** A Pydantic `field_validator` on `conversation_history` SHALL assert that every `entry["content"]` is a string of length ≤ 8 000 characters. Violations yield HTTP 422.

**REQ-2.6:** The service SHALL NOT silently truncate bounded fields — violations always return 422 with a descriptive message. Silent truncation would mask buggy callers.

### REQ-3: Cross-User and Cross-Org Guard

When a JWT is present, the middleware (or a FastAPI dependency chained after body parsing) SHALL verify body identity fields against the token.

**REQ-3.1:** WHEN `request.state.auth.method == "jwt"` AND the body contains `org_id` AND the token role is not `admin` THEN the service SHALL reject the request with HTTP 403 if `str(body.org_id) != str(auth.resourceowner)`. Response body: `{"error": "org_mismatch"}`. The response SHALL NOT echo either value.

**REQ-3.2:** WHEN `request.state.auth.method == "jwt"` AND the body contains `user_id` AND the token role is not `admin` THEN the service SHALL reject the request with HTTP 403 if `str(body.user_id) != str(auth.sub)`. Response body: `{"error": "user_mismatch"}`.

**REQ-3.3:** WHEN `request.state.auth.method == "internal"` THEN cross-user and cross-org checks SHALL be skipped. Internal-secret callers are authoritative service principals for the `org_id`/`user_id` they pass (e.g. portal-api after its own `_get_caller_org` resolution; LiteLLM hook after its team-key-to-org mapping). This exception SHALL be documented in the middleware module docstring with a pointer to this requirement.

**REQ-3.4:** Log records for rejected requests under REQ-3.1 / REQ-3.2 SHALL bind `reason`, `auth_method`, `jwt_sub_hash` (SHA-256 first 12 chars of `token.sub`), `path`, and `request_id`. They SHALL NOT log the plaintext token, plaintext `sub`, or plaintext `resourceowner`.

### REQ-4: Rate Limiting

The service SHALL apply a Redis sliding-window rate limit per caller identity.

**REQ-4.1:** The limiter SHALL use the ZSET sliding-window pattern from `klai-portal/backend/app/services/partner_rate_limit.py` (ZADD + ZREMRANGEBYSCORE + ZCARD in an atomic pipeline).

**REQ-4.2:** The rate-limit key SHALL be:
- JWT path: `retrieval:rl:jwt:<sha256(auth.sub)[:32]>`
- Internal path: `retrieval:rl:internal:<source_ip>` (source_ip = first hop of `X-Forwarded-For` if present else `request.client.host`)

**REQ-4.3:** Default limit: `RATE_LIMIT_RPM=600` requests per minute per key. Exceeded → HTTP 429 with `Retry-After: <seconds>` header and body `{"error": "rate_limit_exceeded"}`.

**REQ-4.4:** `/health` SHALL NOT be rate-limited.

**REQ-4.5:** IF Redis is unreachable THEN the limiter SHALL fail OPEN, accept the request, and log a `WARNING rate_limiter_degraded` event with `reason=redis_unreachable`. Availability outweighs rate-limit strictness when Redis is the failure.

### REQ-5: Configuration

**REQ-5.1:** `retrieval_api/config.py` SHALL add, via `pydantic-settings`:
- `internal_secret: str` — no default, REQUIRED (validator per REQ-1.1)
- `zitadel_issuer: str` — required (used for JWT issuer check)
- `zitadel_api_audience: str` — required (used for JWT audience check)
- `rate_limit_rpm: int = 600`
- `redis_url: str` — required (used by the rate-limiter; may reuse an existing config value if already present)

**REQ-5.2:** Empty values for `internal_secret`, `zitadel_issuer`, `zitadel_api_audience`, or `redis_url` SHALL trigger startup failure. No runtime fallback to "disable if unset" — the whole point is to eliminate F-001 / F-003-style fail-open bugs.

**REQ-5.3:** Environment variables SHALL use the existing `RETRIEVAL_API_` prefix convention.

**REQ-5.4:** Secrets SHALL live in SOPS and be injected through Docker Compose env. No secrets in code, no secrets in logs.

### REQ-6: Caller Updates

The three known callers SHALL pass `X-Internal-Secret` on every retrieval-api call. Deployment is coordinated so all four services roll together.

**REQ-6.1:** `klai-portal/backend/app/services/partner_chat.py` SHALL include `X-Internal-Secret: <secret>` on every call to retrieval-api. The secret is read from a new portal-api setting (e.g. `RETRIEVAL_API_INTERNAL_SECRET`).

**REQ-6.2:** `klai-focus/research-api/app/services/retrieval_client.py` (both `retrieve_broad` and `retrieve_narrow`) SHALL include the header.

**REQ-6.3:** LiteLLM knowledge hook (`deploy/litellm/klai_knowledge.py` or its hook-config YAML) SHALL include the header, secret injected via LiteLLM env.

**REQ-6.4:** Callers that currently forward an end-user JWT SHALL continue to do so in addition to the internal secret (belt-and-braces). When both are present, the middleware prefers the JWT path per REQ-1.3.

### REQ-7: Observability

**REQ-7.1:** Every auth decision SHALL be logged at INFO (accept) or WARN (reject) with `request_id`, `auth_method`, `reason`, `path`, `status_code`, `duration_ms`. Never log the plaintext secret, plaintext JWT, raw `user_id`, or raw `org_id` — use hashed/truncated forms.

**REQ-7.2:** Prometheus counters SHALL expose:
- `retrieval_api_auth_rejected_total{reason=...}`
- `retrieval_api_rate_limited_total{method=jwt|internal}`
- `retrieval_api_cross_user_rejected_total`
- `retrieval_api_cross_org_rejected_total`

**REQ-7.3:** A single `request_id:<uuid>` query in VictoriaLogs SHALL show Caddy → caller service → retrieval-api reject/accept for one request.

### REQ-8: Tests (Mandatory)

**REQ-8.1:** Startup-fail test — launch service with empty `INTERNAL_SECRET`, assert non-zero exit + structured error event.

**REQ-8.2:** Token-confusion test — JWT with `aud=other-app` → HTTP 401 with `reason=invalid_jwt_audience`.

**REQ-8.3:** Cross-user test — JWT with `sub=user_a`, body `user_id=user_b`, `scope=personal` → HTTP 403 with `reason=user_mismatch`.

**REQ-8.4:** Cross-org test — JWT with `resourceowner=org_x`, body `org_id=org_y` → HTTP 403 with `reason=org_mismatch`.

**REQ-8.5:** Bounds tests — `top_k=100000` (422), `conversation_history` length 21 (422), `kb_slugs` length 21 (422), `taxonomy_node_ids` length 51 (422), `conversation_history[0].content` length 10000 (422).

**REQ-8.6:** Rate-limit test — send `RATE_LIMIT_RPM + 1` in one minute, expect last → 429 + `Retry-After`.

**REQ-8.7:** Internal-secret happy-path test — valid `X-Internal-Secret` caller POSTs `/retrieve` with arbitrary `org_id`/`user_id`; REQ-3.3 means this succeeds (service caller trusted).

**REQ-8.8:** Coverage of new/changed modules (`middleware/auth.py`, `models.py`, `config.py`) ≥ 85 %.

### REQ-9: Rollout and Rollback

**REQ-9.1:** Deploy order on core-01:
1. Update SOPS with `INTERNAL_SECRET`, `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`, `RATE_LIMIT_RPM` for retrieval-api + caller services.
2. Rebuild images for retrieval-api, portal-api, research-api, LiteLLM together.
3. `docker compose up -d` for all four services in one step.

**REQ-9.2:** Post-deploy smoke: one retrieval call from portal-api partner_chat, one from research-api retrieval_client, one from LiteLLM hook — all HTTP 200 with plausible payload. VictoriaLogs shows matching `request_id` across caller → retrieval-api.

**REQ-9.3:** Rollback: revert all four service images to prior versions; SOPS keys may remain (they are no-ops for old images). Document as a runbook in `klai-infra/runbooks/` when the SPEC lands.

## Non-Functional Requirements

- **Performance:** Middleware auth (including cached JWKS) adds < 5 ms p95 to request latency. JWKS cached in-memory 1 h, reusing the research-api pattern.
- **Privacy:** No plaintext `INTERNAL_SECRET`, plaintext JWT, plaintext `user_id`, or plaintext `org_id` in logs — hashed/truncated forms only.
- **EU compliance:** Logs remain on-prem (Alloy → VictoriaLogs, existing pipeline). No new external observability dependencies.
- **Security:** All secret comparisons `hmac.compare_digest`. JWKS fetched only over HTTPS. Redis connection reuses the existing pool.
- **Behavioural compatibility:** On the happy path, callers see identical response shapes and timings (± < 5 ms auth cost). No changes to retrieval logic, Qdrant filters, or response models.
