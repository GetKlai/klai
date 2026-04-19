---
id: SPEC-SEC-005
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: medium
---

# SPEC-SEC-005: Internal Endpoint Hardening

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft following Phase 3 security audit (`.moai/audit/`)
- Addresses F-007 from `.moai/audit/04-tenant-isolation.md`
- Roadmap reference: `.moai/audit/99-fix-roadmap.md` section SEC-005

---

## Goal

Harden the portal-api internal endpoints (`/internal/*`) by adding three defence-in-depth layers on top of the existing `INTERNAL_SECRET` shared-secret authentication: per-caller-IP rate limiting, a full audit trail of every internal call, and a documented quarterly secret rotation schedule. The shared-secret model remains the primary authentication mechanism; this SPEC strengthens its blast-radius characteristics and forensics posture without replacing it.

## Success Criteria

- Every successful internal call produces exactly one row in `portal_audit_log` containing `org_id` (where resolvable), `caller_ip`, `endpoint_path`, and `created_at`.
- Every internal endpoint is rate-limited per caller IP; calls beyond the configured ceiling are rejected with HTTP 429 and a `Retry-After` header.
- A `klai-infra/INTERNAL_SECRET_ROTATION.md` runbook documents the quarterly SOPS-based rotation procedure and a named owner.
- All existing internal callers (klai-mailer, LiteLLM knowledge hook, klai-docs, LibreChat patch, Zitadel Action) continue to function after the hardening lands.
- The `INTERNAL_SECRET` check at `_require_internal_token` remains the first gate and is evaluated before audit logging or rate limiting.

---

## Environment

- **Portal backend:** Python 3.13, FastAPI, SQLAlchemy async, Alembic, PostgreSQL, uv (module under change: `klai-portal/backend/app/api/internal.py`)
- **Rate limiting backend:** Redis sliding-window counters via the existing `get_redis_pool()` helper and the reference implementation at `klai-portal/backend/app/api/partner_dependencies.py:191-199` (`check_rate_limit`)
- **Audit storage:** existing `portal_audit_log` table (see `klai-portal/backend/app/models/audit.py`) — RLS split-policy table (SELECT org-scoped, INSERT permissive) per `.claude/rules/klai/projects/portal-security.md`
- **Secret storage:** SOPS-encrypted `.env` files in `klai-infra/` (keys `INTERNAL_SECRET`, propagated to portal-api, knowledge-ingest, retrieval-api, connector, scribe, mailer, research-api, LibreChat patch env, LiteLLM hook env)
- **Network:** internal endpoints exposed only on Docker `klai-net`; callers are other containers, identified by their container IP on that bridge network
- **Observability:** structlog JSON logs shipped via Alloy to VictoriaLogs; every audit row is also reflected as a structured log line for cross-service correlation

## Assumptions

- `INTERNAL_SECRET` remains the primary authentication mechanism for the lifetime of this SPEC. mTLS replacement is explicitly Out of Scope and lives in a future infra SPEC.
- All internal callers either run inside the Docker bridge network (`klai-net`) or reach portal-api via a trusted reverse proxy hop that preserves a reliable client IP header (`X-Forwarded-For`). The rate limiter is IP-based and therefore relies on truthful caller-IP values from the immediate upstream.
- `portal_audit_log` is already deployed with the split SELECT/INSERT RLS policy documented in `portal-security.md`; this SPEC reuses it and does not migrate the schema.
- Redis is available and already initialised via `get_redis_pool()`; the rate limiter degrades safely (fail-open) when Redis is unavailable, matching the existing partner API behaviour.
- Raw-SQL INSERT is required for `portal_audit_log` because portal-api's DB role writes but reads via a different policy set (see `internal.py:537` precedent for `portal_feedback_events`).

---

## Out of Scope

- mTLS or mutual TLS certificate-based authentication as a replacement for `INTERNAL_SECRET` — this will be a separate infra SPEC (candidate: SPEC-INFRA-TLS-001). The shared-secret model is strengthened here, not replaced.
- Per-caller-service identity (distinguishing klai-mailer from knowledge-ingest by identity rather than IP). This would require per-service credentials and is a prerequisite-driven change tied to the future mTLS SPEC.
- Retention policy or automatic purging of `portal_audit_log`. Existing retention rules apply unchanged.
- Alerting or anomaly detection on the audit log. Downstream Grafana/VictoriaLogs work can layer on top without changes here.
- Rewriting internal endpoint payloads (e.g. removing `org_id` from query string in favour of a signed header). Payload shape is preserved to keep callers unchanged.
- Rotation automation. The quarterly rotation is a manual runbook procedure in this SPEC; automation is a later improvement.

---

## Security Findings Addressed

- **F-007** (portal internal endpoints trust query/body `org_id` gated only by a single shared `INTERNAL_SECRET`, no rate limit, no audit trail, no rotation schedule) — see `.moai/audit/04-tenant-isolation.md`. Severity: MEDIUM (P2). Roadmap entry: `.moai/audit/99-fix-roadmap.md` section SEC-005.

Sub-aspects of F-007 addressed by each requirement group:
- No rate limiting on `/internal/*` → REQ-1
- No audit trail of internal calls → REQ-2
- No rotation schedule for `INTERNAL_SECRET` → REQ-3

---

## Threat Model

The primary threat this SPEC mitigates is the blast radius of a leaked or compromised `INTERNAL_SECRET`. Because the secret currently gates every internal endpoint with no secondary controls, a single leak grants unlimited, untraceable, tenant-crossing read/write access for as long as the secret remains valid.

Adversary scenarios considered:

1. **Opportunistic scraper with leaked secret.** An attacker obtains the secret (leaked env file, compromised container image, insider). Without this SPEC they can call `/internal/v1/users/*/feature/knowledge` or `/internal/v1/gap-events` at arbitrary volume and spoof arbitrary `org_id` values, with no record left behind. After this SPEC: rate limit caps traffic per caller IP, audit log retains a forensic trail of every call keyed on caller IP + endpoint, and the quarterly rotation reduces the window of validity for any single leaked secret.
2. **Compromised sibling container.** A different Klai service on `klai-net` is compromised (e.g. via a dependency vulnerability). Attacker pivots to internal endpoints. Without this SPEC the pivot is invisible. After this SPEC the audit log records caller IP and endpoint, making container-level compromise triage tractable against VictoriaLogs.
3. **Accidental replay / broken client.** A buggy internal caller loops on a failing request. Without this SPEC it can DoS downstream resources (PostgreSQL, Mongo lookup in `get_knowledge_feature`). After this SPEC the rate limit acts as a circuit breaker.

Explicit non-goals for the threat model:

- Defeating a determined attacker with valid secret **and** the ability to spoof the caller IP on the bridge network. mTLS is the correct mitigation for that class; see Out of Scope.
- Protecting against misuse by a legitimate internal service. Internal services are trusted for the scope of their documented contract.

Blast-radius reduction summary: a leaked `INTERNAL_SECRET` changes from "silent unlimited access until manually noticed and rotated" to "rate-capped, fully logged, and subject to a scheduled rotation at most one quarter away".

---

## Requirements

### REQ-1: Per-Caller-IP Rate Limiting on Internal Endpoints

The system SHALL enforce a Redis-backed sliding-window rate limit on every `/internal/*` endpoint, keyed by caller IP.

- **REQ-1.1:** WHILE the portal-api process is running AND Redis is reachable, THE service SHALL enforce a ceiling of 100 requests per 60-second sliding window per caller IP across all `/internal/*` endpoints combined.
- **REQ-1.2:** WHEN the rate limit is exceeded for a caller IP, THE service SHALL return HTTP 429 with an error body `{"detail": "Internal rate limit exceeded"}` and a `Retry-After` header containing the integer number of seconds until the next request would be permitted.
- **REQ-1.3:** WHEN Redis is unreachable, THE rate limiter SHALL fail open (allow the request) AND log a warning at level `warning` with `event="internal_rate_limit_redis_unavailable"` so monitoring can alert on degraded protection without breaking live traffic.
- **REQ-1.4:** The rate-limit check SHALL run AFTER `_require_internal_token` validates the shared secret. Unauthenticated traffic SHALL NOT contribute to or deplete the rate-limit budget.
- **REQ-1.5:** The implementation SHALL reuse the sliding-window pattern from `klai-portal/backend/app/api/partner_dependencies.py:check_rate_limit` rather than introducing a new primitive. The key namespace SHALL be distinct (`internal_rl:<caller_ip>`) to avoid collisions with partner-key limits.
- **REQ-1.6:** The caller IP SHALL be resolved in the following priority order: the right-most entry of the `X-Forwarded-For` header from the immediate trusted upstream (Caddy) when present, else `request.client.host`. The implementation SHALL document this order in code comments.
- **REQ-1.7:** The 100 req/min ceiling SHALL be configurable via a `settings.internal_rate_limit_rpm` pydantic-settings field with a default of `100`, so the value can be tuned without a code change.

### REQ-2: Audit Log for Every Internal Call

The system SHALL write one row to `portal_audit_log` for every internal endpoint call that passes the shared-secret check.

- **REQ-2.1:** WHEN an internal endpoint is called AND `_require_internal_token` passes, THE service SHALL insert a row into `portal_audit_log` with at minimum: `org_id` (when resolvable from request context, else `0` as "unknown"), `actor_user_id="internal:<caller_ip>"`, `action="internal_call"`, `resource_type="internal_endpoint"`, `resource_id=<endpoint_path>`, `details={"caller_ip": "<ip>", "method": "<http_method>"}`, `created_at=NOW()`.
- **REQ-2.2:** The audit insert SHALL use raw `text()` SQL in the style of the existing `portal_feedback_events` insert at `klai-portal/backend/app/api/internal.py:537`, because `portal_audit_log` is an RLS split-policy table (SELECT org-scoped, INSERT permissive) and the SQLAlchemy ORM would emit an implicit `RETURNING` that violates the split policy.
- **REQ-2.3:** The audit insert SHALL use an independent `AsyncSessionLocal()` session (fire-and-forget pattern documented in `.claude/rules/klai/projects/portal-backend.md`), so a downstream failure or rollback in the calling endpoint does not erase the audit row.
- **REQ-2.4:** IF the audit insert itself fails, THEN the service SHALL log at level `exception` with `event="internal_audit_write_failed"` and SHALL NOT fail the primary request. Audit is forensic, not a hard gate.
- **REQ-2.5:** The `endpoint_path` captured in `resource_id` SHALL be the matched route template (e.g. `/internal/v1/gap-events`), not the raw URL including query string, to avoid leaking query-string PII into the audit table.
- **REQ-2.6:** WHERE the endpoint resolves an `org_id` as part of its normal processing (for example `notify_page_saved` at `internal.py:408`, `get_knowledge_feature` at `internal.py:307`, `create_gap_event` at `internal.py:597`, `post_kb_feedback` at `internal.py:493`), THE audit row SHALL record that resolved integer `org_id`. Otherwise `org_id=0` SHALL be used to denote "not applicable / unresolved".
- **REQ-2.7:** Audit rows SHALL also be emitted as a structlog entry at level `info` with the same fields and the stable key `event="internal_call_audited"`, so VictoriaLogs can cross-correlate with the DB row via `request_id`.

### REQ-3: Documented Quarterly Rotation Schedule

The system SHALL have a runbook that documents the `INTERNAL_SECRET` rotation procedure and schedule.

- **REQ-3.1:** A new file `klai-infra/INTERNAL_SECRET_ROTATION.md` SHALL exist and SHALL describe the quarterly rotation procedure end-to-end, using the SOPS decrypt → modify → encrypt-in-place → mv sequence per `.claude/rules/klai/pitfalls/process-rules.md` rule `follow-loaded-procedures`.
- **REQ-3.2:** The runbook SHALL list every consumer of `INTERNAL_SECRET` (portal-api, knowledge-ingest, retrieval-api, connector, scribe, mailer, research-api, LibreChat patch, LiteLLM hook, Zitadel Action if applicable) so rotations cannot forget a container.
- **REQ-3.3:** The runbook SHALL name a rotation owner (role, not person) AND SHALL describe how the quarterly cadence is tracked (calendar entry, GitHub issue template, or tickler — whichever the infra team uses).
- **REQ-3.4:** The runbook SHALL document the rollback procedure: if a rotation breaks a consumer, how to re-issue the previous secret with the shortest possible downtime.
- **REQ-3.5:** The runbook SHALL be linked from `klai-infra/README.md` (or the canonical infra index) so the procedure is discoverable without this SPEC.

---

## Non-Functional Requirements

- **Performance:** Audit insert and rate-limit check combined SHALL add no more than 5 ms p95 overhead to internal endpoints. The audit insert being fire-and-forget on an independent session MUST NOT block the primary response.
- **Observability:** Both rate-limit rejections and audit writes SHALL be queryable in VictoriaLogs via stable `event` keys (`internal_rate_limit_exceeded`, `internal_call_audited`, `internal_audit_write_failed`, `internal_rate_limit_redis_unavailable`).
- **Privacy:** The audit `details` JSONB SHALL NOT contain request bodies, headers other than the caller IP, or any query-string values.
- **Backward compatibility:** Existing internal callers (klai-mailer, LiteLLM hook, LibreChat patch, klai-docs, Zitadel Action) SHALL continue to function without code changes on their side. Any breakage is a regression.
- **Fail modes:** Rate limiter failing open (REQ-1.3) and audit failing silently (REQ-2.4) are deliberate; both preserve availability while the forensic and protective signals remain as observability.
