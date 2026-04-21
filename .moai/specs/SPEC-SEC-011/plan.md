# Implementation Plan: SPEC-SEC-011 — Knowledge-Ingest Fail-Closed Auth

## Overview

Three small source-file edits plus one new test file. No new dependencies, no schema changes, no caller changes.

The change set:

1. Add a `model_validator(mode="after")` in `knowledge_ingest/config.py` that rejects an empty `knowledge_ingest_secret`.
2. Remove the fail-open branch at `knowledge_ingest/middleware/auth.py:19-21`.
3. Remove the fail-open branch at `knowledge_ingest/routes/ingest.py:54-60` (`_verify_internal_secret`).
4. Audit other route modules in `knowledge_ingest/routes/` for the same pattern; fix any additional occurrences.
5. Add `klai-knowledge-ingest/tests/test_middleware_auth.py` covering startup, middleware 401, and per-route 401.

Rollout is a single deploy. Any existing production deploy already has `KNOWLEDGE_INGEST_SECRET` set via SOPS so no operational surprise is expected. Dev/CI environments that relied on the fail-open behavior SHALL be updated to set a dummy secret.

---

## Task Decomposition

### TASK-001: Config validator for required secret

**Files:** `klai-knowledge-ingest/knowledge_ingest/config.py`

**Requirement:** REQ-1.1, REQ-1.2, REQ-1.3, REQ-1.4

**Approach:**

- Import `model_validator` from `pydantic`.
- Add a `@model_validator(mode="after")` method on `Settings` that checks `self.knowledge_ingest_secret`. If falsy, raise `ValueError("KNOWLEDGE_INGEST_SECRET must be set")`.
- Do NOT remove the `= ""` default from the field declaration — keeping the default lets pydantic-settings construct the object and run the validator, which produces a clean `ValidationError` with the env var name instead of a less-helpful "field required" message.
- Because `settings = Settings()` is instantiated at module import time, the validator fires during import and any importer (including FastAPI app bootstrap) crashes before binding to a port.

**Test:** Verify `Settings(knowledge_ingest_secret="")` raises `ValidationError` mentioning `KNOWLEDGE_INGEST_SECRET`. Verify `Settings(knowledge_ingest_secret="valid")` constructs normally.

**Size:** S (~10 LOC impl + 20 LOC test)

**Dependencies:** none

---

### TASK-002: Middleware fail-open removal

**Files:** `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py`

**Requirement:** REQ-2.1, REQ-2.2, REQ-2.3, REQ-2.4

**Approach:**

- Delete lines 19-21:
  ```
  if not settings.knowledge_ingest_secret:
      return await call_next(request)
  ```
- Update the module docstring to remove the obsolete "backward compat" note; replace with "Secret is required — validated at config load time".
- Leave the `/health` exemption (lines 24-25) intact.
- Leave the `hmac.compare_digest` comparison (line 28) intact.

**Test:** Covered in TASK-005 (`test_middleware_auth.py`). Assert 401 for: no header, empty header, wrong header, wrong length header. Assert 200 for: valid header, `/health` without header.

**Size:** S (~3 LOC deleted + 2 LOC docstring tweak)

**Dependencies:** TASK-001 (the middleware precondition — secret always set — is only guaranteed after TASK-001 lands)

---

### TASK-003: Route-helper fail-open removal

**Files:** `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py`

**Requirement:** REQ-3.1, REQ-3.2, REQ-3.3

**Approach:**

- In `_verify_internal_secret` (lines 54-60), delete lines 56-57:
  ```
  if not settings.knowledge_ingest_secret:
      return
  ```
- Resulting helper:
  ```
  def _verify_internal_secret(request: Request) -> None:
      """Verify X-Internal-Secret header for service-to-service calls."""
      secret = request.headers.get("x-internal-secret", "")
      if not hmac.compare_digest(secret, settings.knowledge_ingest_secret):
          raise HTTPException(status_code=401, detail="Unauthorized")
  ```
- Do not modify the six callsites (`delete_kb_route`, `delete_connector_route`, `update_kb_visibility_route`, `register_kb_webhook`, `deregister_kb_webhook`, `bulk_sync_kb_route`).

**Test:** Covered in TASK-005 — one 401 test per gated route.

**Size:** S (~3 LOC deleted)

**Dependencies:** TASK-001

---

### TASK-004: Audit other route modules for the same pattern

**Files:** `klai-knowledge-ingest/knowledge_ingest/routes/crawl.py`, `knowledge.py`, `personal.py`, `stats.py`, `taxonomy.py`

**Requirement:** REQ-4.1, REQ-4.2, REQ-4.3

**Approach:**

1. Grep each file for `if not settings.knowledge_ingest_secret` and `if not settings.*_secret`.
2. Grep for `_verify_internal_` to catch any other route-level guard helpers.
3. Record per-file verdict in this SPEC's HISTORY section at commit time.
4. Initial inspection (from research.md) confirms:
   - `crawl.py`, `knowledge.py`, `personal.py`, `stats.py` — no route-level guards; rely on middleware. No code change needed.
   - `taxonomy.py` — has its own `_verify_internal_token` (reverse direction, `portal_internal_token`). Out of scope per "Out of Scope" in `spec.md`, but spot-check for analogous fail-open and log a follow-up if found.
   - `ingest.py` — covered by TASK-003.
5. IF an additional occurrence of the F-012 pattern is found, apply the same fix as TASK-003 and extend TASK-005 with a per-route 401 test.

**Test:** The grep-based audit is the test. Document the grep commands and results in the commit message and in `HISTORY` of `spec.md`.

**Size:** S (audit only; likely 0 code changes)

**Dependencies:** none (can run in parallel with TASK-001/002/003)

---

### TASK-005: Tests — startup + middleware + per-route 401

**Files:** `klai-knowledge-ingest/tests/test_middleware_auth.py` (new)

**Requirement:** REQ-5.1, REQ-5.2, REQ-5.3, REQ-5.4, REQ-5.5

**Approach:**

Test groups (each a pytest function or class):

1. **Startup validation** (REQ-5.1, REQ-5.2)
   - `test_settings_raises_on_empty_secret` — monkeypatch env to empty, assert `ValidationError` on `Settings()`; assert message contains `KNOWLEDGE_INGEST_SECRET`.
   - `test_settings_raises_on_missing_secret` — monkeypatch env to delete var, assert same.
   - `test_settings_constructs_with_valid_secret` — set env, assert `Settings().knowledge_ingest_secret == "valid"`.

2. **Middleware enforcement** (REQ-5.3, REQ-5.5)
   - Build a FastAPI app with `InternalSecretMiddleware` and a dummy `/protected` route.
   - `test_middleware_rejects_missing_header` — no header → 401, body `{"detail": "Invalid or missing X-Internal-Secret"}`.
   - `test_middleware_rejects_empty_header` — header `""` → 401.
   - `test_middleware_rejects_wrong_header` — header `"wrong"` → 401.
   - `test_middleware_rejects_wrong_length_header` — header of different length than secret → 401 (no crash — confirms `hmac.compare_digest`).
   - `test_middleware_allows_valid_header` — correct header → 200.
   - `test_middleware_allows_health_without_header` — `/health` with no header → 200.

3. **Route-helper enforcement** (REQ-5.4)
   - For each of the six gated routes in `routes/ingest.py`, call the route handler directly (or via `TestClient` with middleware stubbed to pass-through) with missing / invalid header and assert `HTTPException(401)`.
   - Parametrize: `@pytest.mark.parametrize("method,path,body_or_params", [ ... ])` covering:
     - `DELETE /ingest/v1/kb?org_id=X&kb_slug=Y`
     - `DELETE /ingest/v1/connector?org_id=X&kb_slug=Y&connector_id=Z`
     - `PATCH /ingest/v1/kb/visibility` with `UpdateKBVisibilityRequest` body
     - `POST /ingest/v1/kb/webhook` with `KBWebhookRequest` body
     - `DELETE /ingest/v1/kb/webhook` with `KBWebhookRequest` body
     - `POST /ingest/v1/kb/sync` with `BulkSyncRequest` body
   - Each asserts `status_code == 401` when header is absent/wrong, and `status_code != 401` when header is valid (so the guard is proven to be the denial source, not an unrelated handler error).
   - Downstream calls (pg_store, qdrant_store, graph_module) MUST be mocked to avoid real I/O; the test concerns are auth-layer only.

4. **Happy path regression** (REQ-5.2 continued)
   - `test_health_endpoint_ok_with_valid_secret` — boots app with valid secret, asserts `/health` returns 200 and a standard health body.

**Size:** M (~250-350 LOC test code including fixtures)

**Dependencies:** TASK-001, TASK-002, TASK-003, TASK-004

---

## Execution Order

Serial dependencies are minimal. Recommended order:

1. **TASK-001** (config validator) — prerequisite for the rest; landing this first makes it impossible to boot without a secret in CI.
2. **TASK-002** (middleware) and **TASK-003** (route helper) — can run in parallel after TASK-001. Both are one-branch deletions.
3. **TASK-004** (audit other routes) — can run in parallel with 002/003; produces either "no change" commit or additional fixes.
4. **TASK-005** (tests) — written alongside 002/003 under TDD, committed together.

Single PR covering all five tasks is recommended because the changes are interdependent (the tests only make sense after the code changes).

---

## Deployment Considerations

- **Pre-deploy check:** Confirm `KNOWLEDGE_INGEST_SECRET` is set in production env via SOPS. Source: `klai-infra/core-01/*.sops`. Use `docker exec knowledge-ingest printenv KNOWLEDGE_INGEST_SECRET | wc -c` — must be > 1.
- **Dev/CI:** Ensure `KNOWLEDGE_INGEST_SECRET=dev-secret` (or similar) is set in `.env.example` and CI pipeline config. Any dev environment that was relying on fail-open will now fail startup.
- **Rollback:** Single-commit revert is safe. The bug being fixed is a correctness tightening; reverting re-introduces the audit findings but does not break service callers.

---

## Dependencies

No new runtime or test dependencies. The change uses `pydantic.model_validator` (already available via pydantic v2.9+ which is the pinned version in `klai-knowledge-ingest/pyproject.toml`) and `hmac.compare_digest` (stdlib).

---

## Known Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | A CI pipeline or dev `.env` silently relied on the fail-open behavior → breaks on first run after merge. | Grep repo for `KNOWLEDGE_INGEST_SECRET` to find configs. Update `.env.example` and CI env in the same PR. |
| 2 | Local Docker compose files miss the env var. | Verify `docker-compose.yml` in `klai-infra` sets the var for the `knowledge-ingest` service. |
| 3 | Import-time raise breaks module-scoped mocking in some tests. | Use `monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-secret")` in a pytest `conftest.py` fixture before importing `knowledge_ingest` modules. |
| 4 | A route module discovered in TASK-004 contains an additional fail-open guard and adds scope creep. | Acceptable — fix it in-place with the same pattern as TASK-003. Scope risk is minimal because the five other route files are small (< 300 LOC each per grep). |

---

## Estimated Scope

- **Modified files:** 3 (`config.py`, `middleware/auth.py`, `routes/ingest.py`)
- **New files:** 1 (`tests/test_middleware_auth.py`)
- **Audited files:** 5 route modules (crawl, knowledge, personal, stats, taxonomy)
- **Total LOC touched:** ~50 impl + ~300 test
- **Total tasks:** 5 atomic cycles
