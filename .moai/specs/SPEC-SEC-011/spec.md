---
id: SPEC-SEC-011
version: 0.1.0
status: draft
created: 2026-04-19
updated: 2026-04-19
author: Mark Vletter
priority: high
---

# SPEC-SEC-011: Knowledge-Ingest Fail-Closed Auth

## HISTORY

### v0.1.0 (2026-04-19)
- Initial draft produced from Phase 3 security audit findings F-003 (middleware fail-open) and F-012 (route-level helper fail-open).
- Source documents: `.moai/audit/04-tenant-isolation.md`, `.moai/audit/04-2-query-inventory.md`, `.moai/audit/99-fix-roadmap.md` (SEC-011 section).
- Note: the SPEC-SEC-011 identifier previously referred to an ISO 27001 compliance SPEC (status COMPLETE, created 2026-03-27). That earlier document is superseded by this one for the SEC-011 ID slot; the prior content remains retrievable via git history. The audit roadmap in `.moai/audit/99-fix-roadmap.md` assigns SEC-011 to this knowledge-ingest fail-closed scope.

---

## Goal

Remove both fail-open auth layers in `klai-knowledge-ingest` and turn the missing-secret case into a startup failure so a misconfigured deploy can never quietly serve unauthenticated traffic.

Today, `klai-knowledge-ingest` has two independent auth layers — `InternalSecretMiddleware` (applied in `main.py`) and `_verify_internal_secret()` (called by individual route handlers in `routes/ingest.py`). Both layers short-circuit and accept the request when `settings.knowledge_ingest_secret` is an empty string. A deploy that forgets to set `KNOWLEDGE_INGEST_SECRET` is therefore served with zero authentication at both layers simultaneously. This SPEC makes the secret a required configuration value, validated at Pydantic settings load time, and removes the fail-open branches from both layers so that the only possible outcomes at runtime are "valid secret → allow" or "invalid / missing secret → 401".

## Success Criteria

- `klai-knowledge-ingest` refuses to start when `KNOWLEDGE_INGEST_SECRET` is empty or unset (process exits with a non-zero exit code and a clear error log).
- `InternalSecretMiddleware.dispatch()` contains no branch that returns `await call_next(request)` without verifying the header; the only exemption is the `/health` path.
- `_verify_internal_secret()` in `routes/ingest.py` contains no `if not settings.knowledge_ingest_secret: return` branch; when called it always verifies the header or raises `HTTPException(401)`.
- All existing callers of knowledge-ingest (portal-api, klai-connector, klai-knowledge-mcp) continue to work unchanged because they already send the `X-Internal-Secret` header when the secret is configured in production.
- Pytest suite contains explicit tests for (1) startup failure on empty secret, (2) middleware 401 on missing/invalid header, and (3) route-helper 401 on missing/invalid header for each endpoint that calls `_verify_internal_secret`.
- Constant-time comparison (`hmac.compare_digest`) is used in both layers (middleware already does; route helper already does — verify unchanged).
- No other route module in `klai-knowledge-ingest/knowledge_ingest/routes/` still contains a route-level fail-open guard (audit result documented in this SPEC; see Requirements section REQ-4).

---

## Environment

- **Service:** `klai-knowledge-ingest`
- **Language / runtime:** Python 3.13, FastAPI, Starlette middleware
- **Config framework:** `pydantic-settings` (`Settings(BaseSettings)` in `knowledge_ingest/config.py`)
- **Deployment:** Docker container on core-01, internal Docker network only, not exposed via Caddy
- **Callers:** portal-api (`X-Internal-Secret` via httpx), klai-connector, klai-knowledge-mcp, operator ad-hoc `curl`
- **Test framework:** pytest (async), FastAPI `TestClient` / `httpx.AsyncClient`
- **Secret source:** SOPS-encrypted env file on core-01 (`klai-infra/core-01/*.sops`), decrypted into container env at boot.

## Assumptions

- The knowledge-ingest service is only ever reachable over the Docker-internal network, never through the public Caddy reverse proxy. The audit confirms this (`SERVERS.md`, section "Niet publiek bereikbaar").
- All production deploys already populate `KNOWLEDGE_INGEST_SECRET` via SOPS — making it required is a correctness tightening, not a breaking change for prod.
- Local development and CI pipelines either set a dummy secret (`dev-secret` / test fixture) or are expected to be updated as part of this SPEC's rollout.
- Callers already send the `X-Internal-Secret` header. This SPEC does not require caller-side changes.
- The `/health` endpoint remains the only auth-exempt path and requires no secret.
- `hmac.compare_digest` is the correct constant-time comparator for the Python 3.13 runtime; both layers already use it for the header match.

## Out of Scope

- Replacing the shared-secret scheme with mTLS or JWT. That is a larger architectural change tracked separately.
- Secret rotation tooling. Rotation mechanics are out of scope here; this SPEC only ensures presence.
- Adding auth to Gitea webhook validation (already uses a distinct `GITEA_WEBHOOK_SECRET` + HMAC signature and is not fail-open).
- Changes to `_verify_internal_token()` in `routes/taxonomy.py` — that helper gates calls going in the opposite direction (ingest → portal) and is covered by a separate audit line item, not by F-003 / F-012.
- Any changes to `klai-retrieval-api`, `klai-connector`, or other services. SEC-010 and SEC-004 track those.

---

## Security Findings Addressed

This SPEC closes two HIGH-severity findings from the Phase 3 security audit:

- **F-003 — knowledge-ingest middleware fail-open.** Source: `.moai/audit/04-tenant-isolation.md`, section "F-003". Location: `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py:19-21`. The middleware returns `await call_next(request)` without auth when `settings.knowledge_ingest_secret` is falsy.
- **F-012 — knowledge-ingest route-level helper ALSO fail-open.** Source: `.moai/audit/04-2-query-inventory.md`, section "F-012". Location: `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:54-60`. The helper `_verify_internal_secret()` returns early (no raise) when the secret is empty, so every `@router` endpoint that calls it also fails open.

The combined effect is that a single missing env var disables both defense layers simultaneously. The roadmap entry is in `.moai/audit/99-fix-roadmap.md`, section "SEC-011 — Knowledge-ingest fail-closed auth".

The relevant pitfall rule `.claude/rules/klai/projects/knowledge.md` ("Portal→ingest auth header: always X-Internal-Secret") documents that knowledge-ingest has two separate internal-auth mechanisms that look similar. This SPEC tightens the first (middleware + route helper for `X-Internal-Secret`) and explicitly does not touch the second (`_verify_internal_token` for ingest→portal calls).

---

## Requirements

### REQ-1: Startup-time configuration validation

The system SHALL refuse to start when `KNOWLEDGE_INGEST_SECRET` is unset or empty.

**REQ-1.1:** The `Settings` class in `knowledge_ingest/config.py` SHALL declare `knowledge_ingest_secret: str` with no default value (remove the current `= ""` default) OR retain the empty-string default and add a `@model_validator(mode="after")` that raises `ValueError` when the field is falsy.

**REQ-1.2:** WHEN the service starts AND `KNOWLEDGE_INGEST_SECRET` resolves to an empty string, the service SHALL log an error message clearly identifying the missing setting (e.g. `"KNOWLEDGE_INGEST_SECRET must be set — refusing to start"`) and SHALL exit with a non-zero process exit code.

**REQ-1.3:** The startup validation SHALL run before the FastAPI app object is constructed, so no worker ever enters the request loop with an empty secret. In practice this means the validation runs at module import time of `knowledge_ingest/config.py`, because `settings = Settings()` is instantiated there and will raise during import if the validator fails.

**REQ-1.4:** The validator SHALL NOT leak the secret value itself to logs in either the success or failure path. The error message SHALL reference only the env var name.

### REQ-2: Middleware hardening

The `InternalSecretMiddleware` SHALL never allow unauthenticated requests to reach route handlers, with the sole exception of the `/health` probe.

**REQ-2.1:** The fail-open branch at `knowledge_ingest/middleware/auth.py:19-21` (`if not settings.knowledge_ingest_secret: return await call_next(request)`) SHALL be removed.

**REQ-2.2:** WHILE the service is running AND the request path is not `/health`, the middleware SHALL require the `X-Internal-Secret` header and SHALL compare it to `settings.knowledge_ingest_secret` using `hmac.compare_digest`. IF the header is missing, empty, or does not match, THEN the middleware SHALL return HTTP 401 with body `{"detail": "Invalid or missing X-Internal-Secret"}` and content-type `application/json`.

**REQ-2.3:** The `/health` path SHALL remain exempt from the header check so that Docker healthchecks continue to work.

**REQ-2.4:** The middleware SHALL NOT log the header value on failure; only the fact of the 401 and the request path.

### REQ-3: Route-helper hardening

`_verify_internal_secret()` in `knowledge_ingest/routes/ingest.py` SHALL never return without verifying the header.

**REQ-3.1:** The fail-open branch at lines 54-60 (`if not settings.knowledge_ingest_secret: return`) SHALL be removed. With REQ-1 in place, reaching this helper with an empty secret is impossible, so the branch is dead code that today hides a critical bug.

**REQ-3.2:** WHEN `_verify_internal_secret(request)` is called, THE helper SHALL read the `x-internal-secret` header and compare it to `settings.knowledge_ingest_secret` using `hmac.compare_digest`. IF the header is missing or does not match, THEN the helper SHALL raise `HTTPException(status_code=401, detail="Unauthorized")`.

**REQ-3.3:** All existing callers of `_verify_internal_secret` (`delete_kb_route`, `delete_connector_route`, `update_kb_visibility_route`, `register_kb_webhook`, `deregister_kb_webhook`, `bulk_sync_kb_route`) SHALL continue to call the helper as the first statement of their handler body, unchanged.

### REQ-4: Other-route audit and cleanup

The system SHALL contain no other route-level fail-open auth guards in the knowledge-ingest routes tree.

**REQ-4.1:** As part of this SPEC the implementer SHALL verify (via grep for `if not settings.knowledge_ingest_secret` and `if not settings.*_secret` across `knowledge_ingest/routes/*.py`) that no additional route module replicates the F-012 fail-open pattern.

**REQ-4.2:** Initial inspection (documented in `research.md`) confirms:
- `routes/crawl.py`, `routes/knowledge.py`, `routes/personal.py`, `routes/stats.py` — do NOT define or call `_verify_internal_secret`. They rely entirely on `InternalSecretMiddleware` for auth. REQ-2 is therefore sufficient protection for these routes.
- `routes/taxonomy.py` — defines a separate helper `_verify_internal_token` that gates the reverse direction (ingest → portal auth via `settings.portal_internal_token`). That helper is out of scope per the "Out of Scope" section but SHALL be spot-checked for an analogous fail-open branch. If one exists, it SHALL be logged as a follow-up finding, not fixed in this SPEC.

**REQ-4.3:** IF any other route module is found with the F-012 pattern during implementation, THE implementer SHALL fix it identically to REQ-3 and document the change in this SPEC's HISTORY.

### REQ-5: Test coverage

New tests SHALL cover startup failure, middleware enforcement, and route-helper enforcement.

**REQ-5.1:** A test SHALL assert that constructing `Settings()` with `KNOWLEDGE_INGEST_SECRET=""` (or unset) raises a `ValidationError` / `ValueError` with a message referencing the env var name.

**REQ-5.2:** A test SHALL assert that a valid secret allows startup and the app responds normally to `/health`.

**REQ-5.3:** Tests SHALL assert that the middleware returns 401 when the request lacks `X-Internal-Secret`, when the header is empty, and when the header does not match the configured secret. Each assertion SHALL cover a non-`/health` path.

**REQ-5.4:** Tests SHALL assert that each route in `routes/ingest.py` guarded by `_verify_internal_secret` (`DELETE /ingest/v1/kb`, `DELETE /ingest/v1/connector`, `PATCH /ingest/v1/kb/visibility`, `POST /ingest/v1/kb/webhook`, `DELETE /ingest/v1/kb/webhook`, `POST /ingest/v1/kb/sync`) returns 401 when the header is missing or invalid, even when the middleware is stubbed out (proves the route-level guard is independently enforcing auth).

**REQ-5.5:** Tests SHALL assert constant-time comparison by using `hmac.compare_digest` behavior (i.e., a wrong-length secret also returns 401, not a crash).

---

## Non-Functional Requirements

- **Security:** No plaintext secret values in logs or exceptions. Constant-time comparison on both layers. Fail-closed is the only runtime mode.
- **Operational:** Startup failure SHALL be clearly diagnosable from container logs (`docker logs knowledge-ingest`) — a single-line structured error naming the missing env var.
- **Backwards compatibility:** None broken. Any deploy that was fail-open due to missing secret was already violating the intent of the auth layer; tightening this is a correctness fix, not a contract change.
- **Performance:** Negligible. A single Pydantic validator at boot plus removal of dead branches.
- **Observability:** Keep existing structlog context on 401 responses (path, request_id). Do not add new fields.
