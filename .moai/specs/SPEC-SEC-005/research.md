# Research — SPEC-SEC-005

## Finding context: F-007

From `.moai/audit/04-tenant-isolation.md` (Phase 3 audit, 2026-04-19):

The portal-api internal surface (`/internal/*`, defined in `klai-portal/backend/app/api/internal.py`) is used by klai-mailer, the LiteLLM knowledge hook, klai-docs (page-saved notifications), the LibreChat feedback patch (KB-015), and at least one Zitadel Action. Every endpoint trusts an `org_id` passed in the query string or body and gates access with a single shared secret (`INTERNAL_SECRET`) validated by `_require_internal_token` at `internal.py:48`. The audit produced three concerns, all marked MEDIUM (P2):

1. No rate limiting. A leaked secret or a compromised sibling container can drive unbounded traffic.
2. No audit trail. There is no persistent record of who called which internal endpoint with which payload — forensics rely entirely on Alloy → VictoriaLogs retention, which is 30 days and may not capture the necessary detail.
3. No rotation schedule. `INTERNAL_SECRET` has no documented quarterly rotation. The current ops posture is "rotate when you remember".

All three concerns are addressed by this SPEC as REQ-1, REQ-2, REQ-3 respectively.

## Scope boundary: mTLS is Out of Scope

The obvious long-term mitigation is mTLS with per-service certificates, replacing the shared secret entirely. It is explicitly Out of Scope here for three reasons:

- **Infra prerequisite, not an app change.** mTLS requires a PKI (Caddy internal CA or cert-manager), certificate distribution to every container (knowledge-ingest, retrieval-api, connector, scribe, mailer, research-api, LibreChat patch, LiteLLM hook), and rotation tooling. None of this exists today on Docker `klai-net`.
- **Non-trivial cross-service change.** Every internal caller would have to mount its cert bundle and present it via httpx (for Python services) or a Node TLS context (for the LibreChat patch and LiteLLM hook). That is an infra SPEC candidate on its own (working name: SPEC-INFRA-TLS-001), with a much larger blast radius than F-007 warrants today.
- **Shared secret is good enough once hardened.** With rate limit + full audit trail + quarterly rotation, the residual risk of a shared secret on an internal Docker network drops to the level where mTLS becomes a nice-to-have, not a must-have. Defence in depth, not total replacement.

This SPEC therefore strengthens the shared-secret model rather than replacing it.

## Reference: existing rate-limit pattern

The partner API already runs a Redis sliding-window rate limiter that is the obvious template for the internal limiter:

- Implementation: `klai-portal/backend/app/api/partner_dependencies.py:191-199`
- Function: `check_rate_limit(redis_pool, key_id, rpm)`
- Pattern: sliding window in Redis, returns `(allowed: bool, retry_after_seconds: int)` and callers raise `HTTPException(429, ..., headers={"Retry-After": ...})`.
- Graceful degrade: if Redis is not available, the partner auth dependency at `partner_dependencies.py:191` skips the check. The internal limiter SHALL mirror this fail-open behaviour (REQ-1.3) so that a Redis outage does not take down every internal call — availability of internal traffic is more important than rate-limit coverage during an outage. This matches the existing system's stance.

Reusing this pattern (rather than introducing a new primitive) keeps operational cognitive load low and makes Grafana dashboards on rate-limit hit rate symmetric across the partner and internal surfaces.

## Reference: raw-SQL INSERT for RLS split-policy tables

`portal_audit_log` is listed in `.claude/rules/klai/projects/portal-security.md` under the "Split (SELECT scoped, INSERT permissive)" RLS category, alongside `product_events` and `vexa_meetings`. The portal-api DB role writes via the INSERT policy but reads via the org-scoped SELECT policy. SQLAlchemy ORM inserts silently emit `RETURNING id`, which triggers the SELECT policy at INSERT time and fails on these tables.

The existing fix for that class of table — raw `text()` INSERT — is visible at `klai-portal/backend/app/api/internal.py:537` for `portal_feedback_events`. SPEC-SEC-005 audit writes MUST follow that same pattern.

```text
# Reference shape from internal.py:537
await db.execute(
    text("""INSERT INTO portal_feedback_events (...) VALUES (:..., ...)"""),
    {...},
)
await db.commit()
```

This is not optional; it is the project convention for this class of table.

## Reference: fire-and-forget audit pattern

From `.claude/rules/klai/projects/portal-backend.md`:

> Request-scoped session rolls back on any exception — audit entries are lost. Use an independent `AsyncSessionLocal()` session for writes that must survive caller exceptions.

SPEC-SEC-005 audit writes are forensic: they MUST survive primary-endpoint rollbacks and exceptions. The implementation therefore opens an independent `AsyncSessionLocal()` per audit write. Combined with `asyncio.create_task` (pattern at `partner_dependencies.py:202-204` with a module-level `_pending` set), the audit write does not block the primary response and survives primary-request failure.

## Why 100 req/min

The chosen ceiling (REQ-1.1: 100 req/60 s per caller IP) is a pragmatic starting point that:

- Fits normal traffic with headroom. The LiteLLM hook's `get_knowledge_feature` is the hottest internal endpoint and is called once per chat turn; real tenants are nowhere near 100 chat turns per minute per container.
- Makes opportunistic abuse visibly painful. A leaked-secret scraper would hit the ceiling in seconds and produce a loud signal in both the audit table and VictoriaLogs.
- Is tunable per REQ-1.7 without a code change.

If in practice the LibreChat patch bursts above 100 req/min during a fan-out event, the ceiling is raised via env var and the SPEC remains satisfied (AC-7 covers configurability).

## Why per caller IP rather than per service identity

Per-service identity would require each caller to present a distinct credential — which is exactly the mTLS Out-of-Scope work. Until then, caller IP on `klai-net` is the best-available identifier: the Docker bridge assigns stable per-container IPs within a deployment, and Caddy preserves the original client IP via `X-Forwarded-For` when an internal call transits it. Imperfect (IPs rotate on container restart), but adequate for rate-limit partitioning during the shared-secret era.

## Trust boundary on X-Forwarded-For

REQ-1.6 pins the caller-IP resolution order to (a) right-most `X-Forwarded-For` from the immediate trusted upstream, else (b) `request.client.host`. The "right-most" choice is deliberate: when Caddy adds a hop, it appends to `X-Forwarded-For`, so the right-most entry is the IP Caddy itself saw — the one we trust. Taking the left-most would accept an attacker-supplied header value and defeat the limiter.

## Existing audit model — reuse, no migration

`klai-portal/backend/app/models/audit.py` already defines `PortalAuditLog` with the fields this SPEC needs:

- `org_id: int` (nullable=False, but we can write 0 when unresolved)
- `actor_user_id: str(64)`
- `action: str(64)`
- `resource_type: str(32)`
- `resource_id: str(128)`
- `details: JSONB | None`
- `created_at: DateTime` (server default NOW())
- Index: `ix_portal_audit_log_org_created` on `(org_id, created_at)`

No schema migration is required. Column semantics for internal calls are codified in REQ-2.1.

One minor accommodation: `org_id` is `nullable=False`. For internal calls that don't resolve an org (e.g. `get_user_language` with an unknown email), the SPEC writes `org_id=0` rather than NULL. `0` is already used elsewhere in the portal to mean "system / unresolved" and keeps the index usable.

## Caller inventory for rotation runbook

The rotation runbook (REQ-3.2) must list every container that reads `INTERNAL_SECRET`. Current inventory (from searching `INTERNAL_SECRET` and `X-Internal-Secret` across the monorepo — verify at rotation time, this list drifts):

- portal-api (reader of the secret, gatekeeper)
- knowledge-ingest (caller via `get_trace_headers()` pattern)
- retrieval-api (caller)
- connector (caller)
- scribe (caller)
- mailer (caller — `user-language` endpoint)
- research-api (caller)
- LibreChat patch env (caller — kb-feedback endpoint)
- LiteLLM knowledge hook env (caller — feature/knowledge + gap-events)
- Zitadel Action env (caller — JWT enrichment via `users/{id}/products`)

If a new internal caller is added after this SPEC lands, adding it to the runbook consumer list is a hard prerequisite of the PR that introduces the caller.

## Integration with the existing observability stack

From `.claude/rules/klai/infra/observability.md`:

- All services emit JSON to stdout; Alloy ships to VictoriaLogs with 30-day retention.
- Caddy generates `X-Request-ID`; portal-api reads it (or generates a UUID) and binds it via `RequestContextMiddleware`. Every structlog line therefore carries `request_id`.

SPEC-SEC-005 leans on this directly: audit structlog entries (REQ-2.7) are tagged with `event="internal_call_audited"` and the existing `request_id` context. A LogsQL query `event:"internal_call_audited" AND request_id:<uuid>` gives a full trace of the internal hop inline with the rest of the cross-service trace, no extra instrumentation.

## Open questions (tracked, not blocking)

- Should `resource_id` include the matched method as well (e.g. `POST /internal/v1/gap-events`) or keep method in `details`? This SPEC keeps method in `details` (REQ-2.1) so `resource_id` stays grep-stable. Revisit if Grafana queries become awkward.
- Should the rotation runbook prescribe a specific tooling (GitHub issue template vs. calendar tickler)? Left to the infra team in REQ-3.3; both satisfy the requirement.
- Should `portal_audit_log` retention be extended specifically for internal calls? Out of scope; see the Non-Functional note in `spec.md`.
