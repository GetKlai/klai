# Acceptance Criteria: SPEC-SEC-011 — Knowledge-Ingest Fail-Closed Auth

All criteria use EARS (Easy Approach to Requirements Syntax) form: WHEN / WHILE / IF / WHERE ... THE system SHALL ...

## Startup validation

- **WHEN** `klai-knowledge-ingest` starts **IF** `KNOWLEDGE_INGEST_SECRET` is empty **THE** service **SHALL** log an error message naming the `KNOWLEDGE_INGEST_SECRET` env var and **SHALL** exit with a non-zero exit code before accepting any HTTP traffic.

- **WHEN** `klai-knowledge-ingest` starts **IF** `KNOWLEDGE_INGEST_SECRET` is not set at all (not present in the process environment) **THE** service **SHALL** behave identically to the empty-string case in the criterion above.

- **WHEN** the `Settings` class is instantiated under pytest with an empty or missing `KNOWLEDGE_INGEST_SECRET` **THE** instantiation **SHALL** raise `pydantic.ValidationError` (or `ValueError`) with a message referencing `KNOWLEDGE_INGEST_SECRET`.

- **WHEN** `klai-knowledge-ingest` starts **IF** `KNOWLEDGE_INGEST_SECRET` is set to a non-empty string **THE** service **SHALL** start normally and **SHALL** respond to `GET /health` with HTTP 200.

- **WHILE** logging the startup failure **THE** service **SHALL NOT** emit the secret value itself (empty or otherwise); only the env-var name.

## Middleware enforcement (`InternalSecretMiddleware`)

- **WHILE** the service is running **THE** middleware **SHALL NOT** contain any branch that calls `await call_next(request)` on a non-`/health` path without first verifying the `X-Internal-Secret` header.

- **WHEN** a request arrives at any path other than `/health` **IF** the `X-Internal-Secret` header is absent **THE** middleware **SHALL** return HTTP 401 with body `{"detail": "Invalid or missing X-Internal-Secret"}`.

- **WHEN** a request arrives at any path other than `/health` **IF** the `X-Internal-Secret` header is an empty string **THE** middleware **SHALL** return HTTP 401.

- **WHEN** a request arrives at any path other than `/health` **IF** the `X-Internal-Secret` header value does not match the configured secret **THE** middleware **SHALL** compare using `hmac.compare_digest` and **SHALL** return HTTP 401.

- **WHEN** a request arrives at `/health` **IF** no `X-Internal-Secret` header is present **THE** middleware **SHALL** allow the request through so that Docker healthchecks and liveness probes continue to work.

## Route-helper enforcement (`_verify_internal_secret` in `routes/ingest.py`)

- **WHILE** `_verify_internal_secret` exists in `routes/ingest.py` **THE** helper **SHALL NOT** contain any branch that returns early without verifying the header (the `if not settings.knowledge_ingest_secret: return` branch at the current lines 56-57 SHALL be removed).

- **WHEN** `_verify_internal_secret(request)` is called **IF** the `x-internal-secret` header is missing or empty **THE** helper **SHALL** raise `HTTPException(status_code=401, detail="Unauthorized")`.

- **WHEN** `_verify_internal_secret(request)` is called **IF** the header value does not match `settings.knowledge_ingest_secret` **THE** helper **SHALL** compare using `hmac.compare_digest` and **SHALL** raise `HTTPException(status_code=401, detail="Unauthorized")`.

## Constant-time comparison

- **WHEN** the middleware OR the route helper compares the provided header value to the configured secret **THE** service **SHALL** use `hmac.compare_digest` (not Python `==` or `!=`) so that timing-based secret extraction remains infeasible.

- **WHEN** a header value of a different length than the configured secret is compared **THE** comparison **SHALL** complete without raising and **SHALL** produce an HTTP 401 (i.e., `hmac.compare_digest` tolerates differing lengths without crashing).

## Per-route test matrix — route-level 401

Each endpoint in `routes/ingest.py` that today calls `_verify_internal_secret` SHALL be covered by its own test asserting HTTP 401 on missing and invalid header, independent of middleware behavior.

- **WHEN** `DELETE /ingest/v1/kb?org_id=...&kb_slug=...` is called **IF** no valid `X-Internal-Secret` header is provided **THE** handler (after bypassing or stubbing middleware) **SHALL** return HTTP 401.

- **WHEN** `DELETE /ingest/v1/connector?org_id=...&kb_slug=...&connector_id=...` is called **IF** no valid header is provided **THE** handler **SHALL** return HTTP 401.

- **WHEN** `PATCH /ingest/v1/kb/visibility` is called **IF** no valid header is provided **THE** handler **SHALL** return HTTP 401.

- **WHEN** `POST /ingest/v1/kb/webhook` is called **IF** no valid header is provided **THE** handler **SHALL** return HTTP 401.

- **WHEN** `DELETE /ingest/v1/kb/webhook` is called **IF** no valid header is provided **THE** handler **SHALL** return HTTP 401.

- **WHEN** `POST /ingest/v1/kb/sync` is called **IF** no valid header is provided **THE** handler **SHALL** return HTTP 401.

## Audit of other route modules

Per REQ-4, each other route module in `klai-knowledge-ingest/knowledge_ingest/routes/` SHALL be confirmed to contain no F-012-style fail-open guard. The acceptance criterion below is stated per module so the implementation phase produces an explicit per-file verdict.

- **WHEN** `knowledge_ingest/routes/crawl.py` is inspected **THE** file **SHALL** contain no route-level `if not settings.knowledge_ingest_secret: return` branch.

- **WHEN** `knowledge_ingest/routes/knowledge.py` is inspected **THE** file **SHALL** contain no route-level fail-open guard.

- **WHEN** `knowledge_ingest/routes/personal.py` is inspected **THE** file **SHALL** contain no route-level fail-open guard.

- **WHEN** `knowledge_ingest/routes/stats.py` is inspected **THE** file **SHALL** contain no route-level fail-open guard.

- **WHEN** `knowledge_ingest/routes/taxonomy.py` is inspected **THE** file **SHALL** be noted as gating the opposite direction (`_verify_internal_token` / `settings.portal_internal_token`). **IF** the file contains an analogous fail-open branch for that helper **THE** implementer **SHALL** record this as a follow-up finding in `HISTORY` but **SHALL NOT** fix it under this SPEC. (A separate audit line item will cover `portal_internal_token` fail-open behavior.)

- **WHEN** `knowledge_ingest/routes/ingest.py` is inspected **THE** file **SHALL** contain exactly one updated `_verify_internal_secret` helper with the fail-open branch removed and constant-time comparison preserved.

## Happy-path regression

- **WHEN** `klai-knowledge-ingest` is running with a valid `KNOWLEDGE_INGEST_SECRET` **AND** a caller sends a request with the correct `X-Internal-Secret` header **THE** service **SHALL** process the request normally (HTTP 2xx for valid payloads, existing 4xx semantics for invalid payloads).

- **WHEN** portal-api makes an internal call to any of the six gated `ingest.py` routes with the correct secret **THE** call **SHALL** succeed with the same response shape as before this SPEC (no change to response bodies or status codes on the happy path).

## Observability

- **WHEN** a 401 is returned by either layer **THE** service **SHALL** emit a structured log entry containing at least `path`, `status_code=401`, and `request_id` (if present), and **SHALL NOT** emit the provided header value.

- **WHEN** startup aborts due to missing secret **THE** error log entry **SHALL** be emitted to stdout in a format Docker captures (no swallowed stderr, no bare `print`).

---

## Quality Gates

- **Test coverage:** `>=` 85% on changed files (`config.py`, `middleware/auth.py`, `routes/ingest.py`).
- **Test file:** new `klai-knowledge-ingest/tests/test_middleware_auth.py` covering startup + middleware + each route-level 401 scenario.
- **Ruff:** 0 errors on `uv run ruff check klai-knowledge-ingest`.
- **Pyright / mypy:** 0 errors on the changed files if the service configures a type checker; otherwise a clean `python -c "from knowledge_ingest import config, middleware.auth, routes.ingest"` import.
- **Startup smoke test:** `docker compose up knowledge-ingest` with empty `KNOWLEDGE_INGEST_SECRET` must exit non-zero and print the named error within 5 seconds; with a valid secret, `/health` must return 200 within 5 seconds.
- **Negative runtime smoke test:** `curl http://knowledge-ingest:8000/ingest/v1/document -d '{}' -H 'content-type: application/json'` (no header) MUST return 401.
