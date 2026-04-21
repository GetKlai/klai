# Acceptance Criteria: SPEC-SEC-010 — Retrieval-API Hardening

All criteria are expressed in EARS format. "The system" refers to `klai-retrieval-api`.

## EARS Requirements

### Authentication (REQ-1)

- **WHILE** retrieval-api receives a request on any path other than `/health` **THE** system **SHALL** require a valid `X-Internal-Secret` header OR a valid Zitadel JWT.
- **WHEN** the service starts **IF** `INTERNAL_SECRET` is unset, empty, or whitespace-only **THE** service **SHALL** log a structured error and exit with a non-zero status.
- **WHEN** the service starts **IF** `ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`, or `REDIS_URL` is empty **THE** service **SHALL** exit with a non-zero status.
- **WHEN** a request presents an `Authorization: Bearer` header **IF** the JWT signature is invalid, the issuer does not match `ZITADEL_ISSUER`, the audience does not match `ZITADEL_API_AUDIENCE`, or the token is expired **THE** system **SHALL** return HTTP 401 with body `{"error": "unauthorized"}` and bind a specific `reason` to the structlog context.
- **WHEN** a request presents an `X-Internal-Secret` header **IF** the header value does not match the configured secret under a constant-time comparison **THE** system **SHALL** return HTTP 401.
- **WHEN** a request presents BOTH a valid `X-Internal-Secret` AND a valid JWT **THE** middleware **SHALL** prefer the JWT path (and therefore apply the stricter cross-user/cross-org guard in REQ-3).
- **WHILE** the request is on `/health` **THE** system **SHALL NOT** require any credentials and **SHALL NOT** enforce rate limits or bounds.

### Bounds (REQ-2)

- **WHEN** a request body contains `top_k` **IF** the value is less than 1 or greater than 50 **THE** system **SHALL** return HTTP 422.
- **WHEN** a request body contains `conversation_history` **IF** the list length exceeds 20 **THE** system **SHALL** return HTTP 422.
- **WHEN** a request body contains `kb_slugs` **IF** the list length exceeds 20 **THE** system **SHALL** return HTTP 422.
- **WHEN** a request body contains `taxonomy_node_ids` **IF** the list length exceeds 50 **THE** system **SHALL** return HTTP 422.
- **WHEN** a request body contains `conversation_history` **IF** any element's `content` is longer than 8 000 characters **THE** system **SHALL** return HTTP 422.
- **WHEN** a bounds violation is detected **THE** system **SHALL NOT** silently truncate — it SHALL always return 422 with a descriptive message.

### Cross-User / Cross-Org Guard (REQ-3)

- **WHEN** a request contains `org_id` or `user_id` in the body **IF** a JWT is present **THE** system **SHALL** reject the request if `str(body.org_id) != str(token.resourceowner)` OR `str(body.user_id) != str(token.sub)` (unless the caller role is `admin`).
- **WHEN** the authentication method is `internal` (internal-secret path) **THE** system **SHALL NOT** enforce the cross-user or cross-org check, because internal-secret callers are authoritative service principals.
- **WHEN** a cross-user or cross-org rejection is logged **THE** log entry **SHALL** contain `reason`, `auth_method`, `jwt_sub_hash`, `path`, `request_id` and **SHALL NOT** contain the plaintext JWT, plaintext `sub`, or plaintext `resourceowner`.

### Rate Limiting (REQ-4)

- **WHILE** the service is running **THE** system **SHALL** track request rates per caller identity in a Redis ZSET sliding window.
- **WHEN** a caller exceeds `RATE_LIMIT_RPM` (default 600) requests in the trailing 60 seconds **THE** system **SHALL** return HTTP 429 with a `Retry-After` header in seconds.
- **WHEN** Redis is unreachable **THE** limiter **SHALL** fail OPEN (accept the request) and log `WARNING rate_limiter_degraded`.
- **WHILE** the path is `/health` **THE** system **SHALL NOT** apply rate limits.

### Caller Updates (REQ-6)

- **WHEN** portal-api calls retrieval-api from `partner_chat.retrieve_context()` **THE** call **SHALL** include the `X-Internal-Secret` header.
- **WHEN** research-api calls retrieval-api from `retrieval_client.retrieve_broad()` or `retrieval_client.retrieve_narrow()` **THE** call **SHALL** include the `X-Internal-Secret` header.
- **WHEN** the LiteLLM knowledge hook calls retrieval-api **THE** call **SHALL** include the `X-Internal-Secret` header.

### Observability (REQ-7)

- **WHEN** an auth decision is made **THE** system **SHALL** emit a structured log entry with `request_id`, `auth_method`, `reason`, `path`, `status_code`, `duration_ms`.
- **WHEN** an auth decision is made **THE** system **SHALL NOT** log the plaintext `INTERNAL_SECRET`, plaintext JWT, raw `user_id`, or raw `org_id`.
- **WHILE** the service is running **THE** system **SHALL** expose Prometheus counters `retrieval_api_auth_rejected_total`, `retrieval_api_rate_limited_total`, `retrieval_api_cross_user_rejected_total`, `retrieval_api_cross_org_rejected_total`.

---

## Test Scenarios

### Scenario 1: Request without credentials is rejected

**Given** retrieval-api is running with a valid `INTERNAL_SECRET`
**When** a client sends `POST /retrieve` with no `X-Internal-Secret` header and no `Authorization` header
**Then** the response status is 401 with body `{"error": "unauthorized"}`
**And** a structured log entry is emitted with `reason=missing_credentials`
**And** the Prometheus counter `retrieval_api_auth_rejected_total{reason="missing_credentials"}` increments by 1

### Scenario 2: Service refuses to start without INTERNAL_SECRET

**Given** the `INTERNAL_SECRET` environment variable is unset (or empty, or whitespace-only)
**When** the service is started
**Then** the process exits with a non-zero status code
**And** a structured error log is emitted with `reason=missing_internal_secret`
**And** no HTTP port is bound

### Scenario 3: Invalid X-Internal-Secret is rejected in constant time

**Given** retrieval-api is running with a valid secret
**When** a client sends `POST /retrieve` with `X-Internal-Secret: wrongvalue`
**Then** the response status is 401
**And** a log entry is emitted with `reason=invalid_internal_secret`
**And** the comparison used `hmac.compare_digest` (verified by code review / static check for absence of `==` in the compare path)

### Scenario 4: Token confusion — wrong audience rejected

**Given** a Zitadel JWT was issued for `aud=other-app`
**When** the partner sends `POST /retrieve` with `Authorization: Bearer <jwt>`
**Then** the response status is 401
**And** the log entry records `reason=invalid_jwt_audience`

### Scenario 5: Cross-user rejected on JWT path

**Given** a JWT with `sub=user_a` and `resourceowner=org_x`
**When** the caller sends `POST /retrieve` with body `{"org_id": "org_x", "user_id": "user_b", "scope": "personal", "query": "secret"}`
**Then** the response status is 403 with body `{"error": "user_mismatch"}`
**And** the response does NOT echo `user_a` or `user_b` back
**And** the log entry contains `jwt_sub_hash` (hashed), NOT plaintext `sub`
**And** the counter `retrieval_api_cross_user_rejected_total` increments by 1

### Scenario 6: Cross-org rejected on JWT path

**Given** a JWT with `sub=user_a` and `resourceowner=org_x`
**When** the caller sends `POST /retrieve` with body `{"org_id": "org_y", "user_id": "user_a", "query": "secret"}`
**Then** the response status is 403 with body `{"error": "org_mismatch"}`
**And** the counter `retrieval_api_cross_org_rejected_total` increments by 1

### Scenario 7: Admin role bypasses cross-user/org check

**Given** a JWT with `sub=admin_user`, `resourceowner=org_admin`, and `role=admin`
**When** the caller sends `POST /retrieve` with body `{"org_id": "org_y", "user_id": "user_c"}`
**Then** the response status is 200 (cross-user and cross-org checks skipped for admin role)

### Scenario 8: Internal-secret caller is NOT subject to cross-user/org checks

**Given** the caller presents a valid `X-Internal-Secret`
**When** the caller sends `POST /retrieve` with body `{"org_id": "any_org", "user_id": "any_user"}`
**Then** the response status is 200
**And** REQ-3.3 documents this as intentional (service principal trust model)

### Scenario 9: Bounds — top_k rejected over limit

**Given** a valid `X-Internal-Secret`
**When** the caller sends `POST /retrieve` with body `{"top_k": 100000, "query": "x"}`
**Then** the response status is 422
**And** the error message names the field `top_k` and the constraint `le=50`

### Scenario 10: Bounds — oversized conversation_history

**Given** a valid caller
**When** the caller sends `POST /retrieve` with `conversation_history` containing 21 items
**Then** the response status is 422

**When** the caller sends `POST /retrieve` with `conversation_history=[{"role": "user", "content": "x" * 10000}]`
**Then** the response status is 422

### Scenario 11: Bounds — oversized kb_slugs / taxonomy_node_ids

**Given** a valid caller
**When** the caller sends `POST /retrieve` with `kb_slugs` of length 21
**Then** the response status is 422

**When** the caller sends `POST /retrieve` with `taxonomy_node_ids` of length 51
**Then** the response status is 422

### Scenario 12: Rate limit enforced on JWT identity

**Given** `RATE_LIMIT_RPM=60` and a valid JWT for `sub=user_a`
**When** the caller sends 60 requests in 30 seconds, all returning 200
**Then** the 61st request within the same rolling minute returns HTTP 429 with a `Retry-After` header

**When** the caller waits until the sliding window clears
**Then** the next request returns 200 again

### Scenario 13: Rate limit enforced on internal-secret identity by source IP

**Given** a single source IP making many internal-secret requests
**When** the rate crosses `RATE_LIMIT_RPM` within a minute
**Then** the service returns HTTP 429 for that IP
**And** a different IP's internal-secret requests are NOT affected

### Scenario 14: Rate-limiter fails open when Redis is down

**Given** Redis is unreachable from retrieval-api
**When** a valid request arrives
**Then** the request is accepted (fail-open)
**And** a WARNING log `rate_limiter_degraded` is emitted with `reason=redis_unreachable`

### Scenario 15: End-to-end smoke test via portal-api

**Given** the deploy is complete and all four services (retrieval-api, portal-api, research-api, LiteLLM) are running with the new secrets
**When** a logged-in portal user triggers a partner_chat call
**Then** portal-api includes `X-Internal-Secret` on its retrieval-api call
**And** the call returns 200
**And** a single `request_id:<uuid>` query in VictoriaLogs shows Caddy → portal-api → retrieval-api in order

### Scenario 16: /health is unauthenticated

**Given** the service is running
**When** a caller sends `GET /health` with no credentials
**Then** the response status is 200
**And** no auth log entry is emitted (or one with `path=/health` and `skipped=true`)

---

## Edge Cases

| Case | Expected Behavior |
|---|---|
| JWT with valid signature but expired `exp` | 401 with `reason=expired_jwt` |
| JWT signed by a different key than JWKS | 401 with `reason=invalid_jwt_signature` |
| `X-Internal-Secret` present but empty string | 401 with `reason=invalid_internal_secret` |
| Both `X-Internal-Secret` and JWT present, both valid | JWT path preferred (stricter checks applied) |
| Both present but `X-Internal-Secret` invalid, JWT valid | JWT path used, request accepted |
| Body missing `org_id` but JWT present | Accept (no mismatch to check); downstream behavior unchanged |
| Body `user_id` absent but `scope=personal` and JWT present | Downstream treats as "no personal scope applied"; no new error raised by this SPEC |
| `role` claim present but not `admin` (e.g. `viewer`) | Treated as non-admin; cross-user/org checks enforced |
| JWKS endpoint unreachable at request time (cache cold) | 503 with `reason=jwks_unavailable`; retry after cache warms |
| Conversation history entry missing `content` key | 422 (Pydantic rejects) |
| Extremely large header (JWT > 8 KB) | 431 Request Header Fields Too Large (FastAPI default) |
| Redis latency > 100 ms | Request still served; log `rate_limiter_slow` at WARNING if over threshold |
| Caller updates deployed before retrieval-api image | retrieval-api (old) ignores the extra header → still works |
| retrieval-api deployed before caller updates | old caller without header → 401. Document in REQ-9 rollback that all four must deploy together |

---

## Quality Gates

- **Test coverage:** ≥ 85 % on new / changed modules: `retrieval_api/middleware/auth.py`, `retrieval_api/models.py`, `retrieval_api/config.py`
- **LSP / type errors:** 0 (pyright on retrieval-api)
- **Lint:** 0 ruff errors
- **Security:**
  - Code review confirms `hmac.compare_digest` is used in every secret-compare path; no `==` for secret comparison
  - grep-based test asserts no plaintext secret, no plaintext JWT, and no raw `user_id`/`org_id` is logged (sample 10 auth events, scan log output)
- **EARS coverage:** every EARS statement above has at least one matching test
- **Smoke test:** post-deploy, one successful call from each of the three callers (portal-api, research-api, LiteLLM), confirmed via VictoriaLogs `request_id:<uuid>` chain
- **Startup-fail:** automated test in CI asserts retrieval-api exits non-zero when `INTERNAL_SECRET` is empty
- **No regression:** the existing `tests/test_retrieve.py` (if any) passes unchanged after updating test fixtures to include `X-Internal-Secret`
