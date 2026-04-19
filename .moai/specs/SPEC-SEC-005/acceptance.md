# Acceptance Criteria — SPEC-SEC-005

EARS-format acceptance tests that MUST pass before SPEC-SEC-005 is considered complete. Each item is verifiable against `klai-portal/backend/app/api/internal.py`, the `portal_audit_log` table, Redis state, and the `klai-infra/` documentation tree.

## AC-1: Audit entry written on every internal call

- **WHEN** an internal endpoint is called with a valid `INTERNAL_SECRET` Bearer token **THE** portal-api service **SHALL** write exactly one entry to `portal_audit_log` with `org_id` (0 if unresolved), `actor_user_id` set to `internal:<caller_ip>`, `action="internal_call"`, `resource_type="internal_endpoint"`, `resource_id` equal to the matched route template (not the raw URL), and `created_at` equal to the request-handling timestamp.
- **WHEN** the same internal endpoint is called 10 times in sequence **THE** `portal_audit_log` table **SHALL** contain exactly 10 new rows for that endpoint, one per call.
- **WHEN** the audit insert itself fails (simulated by temporarily breaking DB connectivity for the audit session) **THE** primary request **SHALL** still complete successfully AND **THE** service **SHALL** emit a structlog entry at level `exception` with `event="internal_audit_write_failed"`.

## AC-2: Audit row uses independent session

- **WHEN** the primary endpoint (for example `create_gap_event`) raises an exception after the audit row was enqueued **THE** audit row **SHALL** still be persisted (independent `AsyncSessionLocal()` session, fire-and-forget). Verification: force an exception after audit enqueue, inspect `portal_audit_log` for the row.

## AC-3: Audit row carries resolved org_id where available

- **WHEN** `notify_page_saved`, `get_knowledge_feature`, `create_gap_event`, or `post_kb_feedback` resolves the integer `org_id` as part of its processing **THE** corresponding audit row **SHALL** have that integer `org_id` populated (not 0).
- **WHEN** `get_user_language` runs and the email does not match any portal user **THE** corresponding audit row **SHALL** record `org_id=0` and not fail the request.

## AC-4: Rate limit enforces 100 req/min per caller IP

- **WHILE** internal endpoints are enabled AND Redis is reachable **THE** rate-limiter **SHALL** enforce a maximum of 100 requests per 60-second sliding window per caller IP across all `/internal/*` endpoints combined.
- **WHEN** the rate limit is exceeded **THE** service **SHALL** return HTTP 429 with JSON body `{"detail": "Internal rate limit exceeded"}` AND **SHALL** include a `Retry-After` response header whose value is the integer number of seconds until the next request would be permitted.
- **WHEN** 100 requests are spread across distinct caller IPs (for example 100 different sibling containers) **THE** service **SHALL NOT** return HTTP 429; rate limiting is per caller IP, not global.

## AC-5: Rate limit ignores unauthenticated traffic

- **WHEN** a request arrives at `/internal/*` without a valid `INTERNAL_SECRET` **THE** service **SHALL** reject with HTTP 401 BEFORE the rate limiter consumes a token. Verification: 200 sequential 401s from one caller IP MUST NOT cause a 429 on a subsequent authenticated request from the same IP.

## AC-6: Rate limit fails open when Redis is down

- **WHEN** Redis is unreachable **THE** rate limiter **SHALL** allow the request AND **SHALL** emit a structlog warning with `event="internal_rate_limit_redis_unavailable"`. Verification: stop the local Redis instance and confirm internal endpoints continue to respond with 200 while the warning appears in logs.

## AC-7: Rate limit ceiling is configurable

- **WHEN** `INTERNAL_RATE_LIMIT_RPM` env var is set to `50` **AND** the portal-api process is restarted **THE** ceiling **SHALL** be 50 req/min per caller IP, not 100.

## AC-8: Token check remains the first gate

- **WHEN** a request arrives with a wrong `Authorization` header **THE** service **SHALL** return HTTP 401 **AND SHALL NOT** write an audit row **AND SHALL NOT** consume rate-limit budget. Audit and rate limit run only for requests that have already passed the shared-secret check.

## AC-9: VictoriaLogs cross-correlation

- **WHEN** an internal call is audited **THE** service **SHALL** also emit a structlog entry at level `info` with key `event="internal_call_audited"` containing the same fields as the audit row AND the request's `request_id`. A LogsQL query `event:"internal_call_audited" AND request_id:<uuid>` SHALL return exactly one match per call.

## AC-10: Rotation runbook exists and is linked

- **WHEN** an operator looks up secret rotation for Klai infrastructure **THE** file `klai-infra/INTERNAL_SECRET_ROTATION.md` **SHALL** exist AND describe the quarterly rotation procedure using SOPS decrypt → modify → encrypt-in-place → mv.
- **WHEN** the runbook is read **THE** document **SHALL** list every consumer of `INTERNAL_SECRET` (portal-api, knowledge-ingest, retrieval-api, connector, scribe, mailer, research-api, LibreChat patch, LiteLLM hook, plus Zitadel Action if applicable).
- **WHEN** the runbook is read **THE** document **SHALL** name a rotation owner (by role), specify how the quarterly cadence is tracked, and document the rollback procedure.
- **WHEN** `klai-infra/README.md` (or the canonical infra index) is read **THE** index **SHALL** link to `INTERNAL_SECRET_ROTATION.md` so the procedure is discoverable.

## AC-11: Existing callers keep working

- **WHEN** klai-mailer, the LiteLLM knowledge hook, klai-docs, the LibreChat feedback patch, and the Zitadel Action are exercised against the hardened portal-api with their current payloads AND correct `INTERNAL_SECRET` **THE** responses **SHALL** be identical in status code and body to pre-SPEC behaviour (within the 100 req/min budget).

## AC-12: No PII leakage into audit table

- **WHEN** the audit row is written **THE** `details` JSONB column **SHALL** contain only `caller_ip` and `method`. It SHALL NOT contain request bodies, query-string values (such as `email=`), or headers other than the caller IP.
