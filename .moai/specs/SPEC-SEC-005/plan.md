# Implementation Plan — SPEC-SEC-005

Ordered work units for implementing internal endpoint hardening on top of the existing `_require_internal_token` gate in `klai-portal/backend/app/api/internal.py`. All changes preserve the current payload shape and behaviour of internal endpoints. Backward compatibility is a hard constraint (AC-11).

## 1. Helper: fire-and-forget audit log writer

**Target:** `klai-portal/backend/app/api/internal.py` (new private helper, near the top of the module next to `_require_internal_token`)

Add `async def _log_internal_call(org_id: int | None, caller_ip: str, endpoint_path: str, method: str) -> None:`.

- Opens an independent `AsyncSessionLocal()` session (imported from `app.core.database`) — required because the calling endpoint may roll back and we must not lose the audit row (see `.claude/rules/klai/projects/portal-backend.md` "Fire-and-forget writes").
- Executes a raw `text()` `INSERT INTO portal_audit_log (...)` statement in the style of the existing `portal_feedback_events` insert at `internal.py:537`. Raw SQL is required because `portal_audit_log` is an RLS split-policy table (SELECT org-scoped, INSERT permissive) per `portal-security.md`, and the SQLAlchemy ORM emits implicit `RETURNING` on insert which triggers the SELECT policy and fails.
- Column mapping:
  - `org_id` → `COALESCE(:org_id, 0)`
  - `actor_user_id` → `:actor_user_id` (format: `internal:<caller_ip>`)
  - `action` → `'internal_call'`
  - `resource_type` → `'internal_endpoint'`
  - `resource_id` → `:endpoint_path` (matched route template, not the raw URL)
  - `details` → `CAST(:details AS jsonb)` (JSON-encoded `{"caller_ip": ..., "method": ...}`)
  - `created_at` → `NOW()`
- Wraps the execute+commit in `try/except Exception` that calls `logger.exception("internal_audit_write_failed", caller_ip=..., endpoint_path=...)` and swallows the exception. Audit failure MUST NOT fail the primary request (REQ-2.4 / AC-1).
- Also emits a structlog `info` entry with key `event="internal_call_audited"` plus all audit fields, before the DB write, so VictoriaLogs cross-correlation works even if the DB insert later fails (REQ-2.7 / AC-9).

## 2. Helper: per-caller-IP sliding-window rate limiter

**Target:** `klai-portal/backend/app/api/internal.py` (new private helper, placed alongside `_log_internal_call`)

Add `async def _check_rate_limit_internal(caller_ip: str) -> None:`.

- Calls `get_redis_pool()` (imported from `app.services.redis_client`, already in use at `internal.py:39`).
- If the pool is `None` or the Redis call raises: log `logger.warning("internal_rate_limit_redis_unavailable", caller_ip=caller_ip)` and return (fail open per REQ-1.3 / AC-6).
- Otherwise reuses the sliding-window algorithm from `klai-portal/backend/app/api/partner_dependencies.py` `check_rate_limit` (lines 191–199). Adapt the key namespace to `internal_rl:<caller_ip>` to avoid collisions with partner keys (REQ-1.5).
- Ceiling: `settings.internal_rate_limit_rpm` (new pydantic-settings field, default `100`). Window: 60 seconds.
- When the limit is exceeded, raise `HTTPException(status_code=429, detail={"detail": "Internal rate limit exceeded"}, headers={"Retry-After": str(retry_after_seconds)})` (REQ-1.2 / AC-4).

### 2a. Resolve caller IP

Add `def _resolve_caller_ip(request: Request) -> str:` that returns the right-most entry of `X-Forwarded-For` when present (stripping whitespace), else `request.client.host`, else `"unknown"`. This helper is called once by `_require_internal_token` and the resolved value is reused for both the audit row and the rate-limit key (REQ-1.6).

## 3. Wire helpers into `_require_internal_token`

**Target:** `klai-portal/backend/app/api/internal.py:48` (`_require_internal_token`)

Order of operations (must stay in this order):

1. Existing token validation (unchanged): reject with 401/503 BEFORE any other work. This guarantees audit and rate limit ignore unauthenticated traffic (REQ-1.4, REQ-2 precondition, AC-5, AC-8).
2. `caller_ip = _resolve_caller_ip(request)`.
3. `await _check_rate_limit_internal(caller_ip)` — raises 429 if over the ceiling, before any per-endpoint work.
4. `endpoint_path = request.scope.get("route").path if request.scope.get("route") else request.url.path` — matched route template, never the raw URL (REQ-2.5 / AC-1).
5. `method = request.method`.
6. Fire-and-forget audit: wrap `_log_internal_call(org_id=None, caller_ip=caller_ip, endpoint_path=endpoint_path, method=method)` in `asyncio.create_task(...)` with a reference held in a module-level `set()` (matching the `_pending` pattern at `partner_dependencies.py:202-204` so the task isn't garbage-collected mid-flight).

### 3a. org_id enrichment

For the four endpoints where org_id is resolved during normal handling (REQ-2.6 / AC-3), add a small post-resolution call:

- `notify_page_saved` (`internal.py:408`): after the `org_id` path param is known (it is already `int`), call `_log_internal_call(org_id=org_id, ...)` as an explicit enrichment, *overwriting* the earlier `org_id=None` audit by inserting an additional row is wrong — instead the simpler approach is:
  - `_require_internal_token` does NOT audit directly. It stores `caller_ip`, `endpoint_path`, `method` on `request.state`.
  - Each handler is responsible for calling `await _audit_internal_call(request, db, org_id=...)` once per request, using the values from `request.state` and its own resolved org_id (0 when unresolved).

This keeps the audit row's `org_id` accurate without double-writing. The rate-limit check stays in `_require_internal_token` so it runs before any endpoint logic regardless of org_id resolution.

Concretely: `_require_internal_token` handles auth + rate-limit + stashes context on `request.state`. Each of the five endpoints listed in REQ-2.6 calls `await _audit_internal_call(request, org_id=<resolved or 0>)` at the end of its handler (success path). For endpoints that don't resolve an org_id (`get_user_language` when email is unknown), the handler still calls `_audit_internal_call(request, org_id=0)` so every authenticated call is audited exactly once (AC-1, AC-3).

## 4. Settings field

**Target:** `klai-portal/backend/app/core/config.py` (pydantic-settings `Settings` class)

Add `internal_rate_limit_rpm: int = 100` with a matching env alias `INTERNAL_RATE_LIMIT_RPM`. Document the default in the field doc-string (REQ-1.7 / AC-7).

## 5. Rotation runbook

**Target:** `klai-infra/INTERNAL_SECRET_ROTATION.md` (new file)

Sections:

- Purpose and scope
- Rotation cadence: quarterly, owner = infra lead role
- Consumers of `INTERNAL_SECRET` (exhaustive list per REQ-3.2 / AC-10): portal-api, knowledge-ingest, retrieval-api, connector, scribe, mailer, research-api, LibreChat patch env, LiteLLM hook env, Zitadel Action env (verify at rotation time whether still in use)
- Procedure: SOPS decrypt → modify → encrypt-in-place → mv (exact command sequence, per `.claude/rules/klai/pitfalls/process-rules.md` rule `follow-loaded-procedures`)
- Deploy sequence: which services to restart and in which order to avoid a window where one caller still uses the old secret while portal-api expects the new one
- Rollback: re-encrypt the previous secret, re-deploy, documented time-to-restore target
- Tracking: where the quarterly reminder lives (calendar / issue tracker)

Then edit `klai-infra/README.md` (or the canonical infra index) to link the new runbook (REQ-3.5 / AC-10).

## 6. Tests

**Target:** `klai-portal/backend/tests/api/test_internal.py` (extend existing test module)

Required test cases — mapped to acceptance criteria:

1. **AC-1 / AC-8** — Successful call to `get_user_language` writes exactly one `portal_audit_log` row; a call with a wrong Bearer token returns 401 and writes zero rows.
2. **AC-3** — `notify_page_saved` with a known `org_id` writes the audit row with the correct integer `org_id`; `get_user_language` with an unknown email writes the row with `org_id=0`.
3. **AC-4** — Fire 101 requests from the same caller IP within 60 s; the 101st returns 429 with a numeric `Retry-After` header. Spread 200 requests across 100 distinct caller IPs; all return 200 (per-IP not global).
4. **AC-5** — Fire 200 failed-auth requests from one IP, then one valid request from the same IP — the valid request returns 200 (unauthenticated traffic does not consume budget).
5. **AC-6** — With Redis mocked to raise on every call, internal endpoints return 200 and a `internal_rate_limit_redis_unavailable` warning is logged.
6. **AC-7** — With `INTERNAL_RATE_LIMIT_RPM=50`, the 51st request returns 429.
7. **AC-2** — Force an exception in the primary handler after the audit task has been scheduled; verify the audit row still exists (independent session). Use a handler wrapper that intentionally raises after `_audit_internal_call`.
8. **AC-12** — Inspect the `details` JSONB column of a freshly-written audit row and assert it contains only `caller_ip` and `method` keys.
9. **Token still primary** — Token check is evaluated before either audit or rate-limit by construction (single function); a code-level test that calls `_require_internal_token` with a bad token and asserts neither `_log_internal_call` nor `_check_rate_limit_internal` was invoked (patch + assert-not-called).
10. **AC-11 smoke tests** — Reuse existing internal endpoint tests under the new hardening; they must all still pass unchanged (the presence of this suite is the regression guard).

Pytest fixtures:

- Reuse the existing `async_client` fixture.
- Add a `rate_limit_redis` fixture that resets the `internal_rl:*` keyspace between tests.
- Add a `patched_audit_log` fixture that captures rows written to `portal_audit_log` for assertion without coupling to production data.

## 7. Observability follow-ups (non-blocking)

Not required for SPEC completion but documented for the Grafana team:

- Stable log keys to alert on: `internal_rate_limit_exceeded`, `internal_rate_limit_redis_unavailable`, `internal_audit_write_failed`.
- Candidate Grafana panel: `SELECT action, COUNT(*) FROM portal_audit_log WHERE action='internal_call' AND created_at > now() - interval '24 hours' GROUP BY resource_id ORDER BY 2 DESC`.

## Execution order and safety

1. Add settings field (item 4). Low-risk.
2. Add helpers `_resolve_caller_ip`, `_check_rate_limit_internal`, `_log_internal_call`, `_audit_internal_call` to `internal.py` (items 1, 2, 3). Helpers are unused until wired.
3. Wire `_require_internal_token` for rate limit + stash context (item 3). Token check precedes new logic by construction.
4. Wire per-endpoint `_audit_internal_call` calls (item 3a).
5. Add tests (item 6) — run the suite; expect AC-1 through AC-12 to pass.
6. Add rotation runbook (item 5). Documentation only; no code impact.
7. Deploy and verify one round of real traffic produces audit rows and that Grafana/VictoriaLogs sees the expected log keys.

Rollback plan: all code changes are additive. Reverting the PR returns `/internal/*` to the pre-SPEC state without schema migrations or config removals (the new settings field has a safe default).
