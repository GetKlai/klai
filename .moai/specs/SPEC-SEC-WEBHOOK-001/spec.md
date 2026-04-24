---
id: SPEC-SEC-WEBHOOK-001
version: 0.3.0
status: draft
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: critical
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-WEBHOOK-001: Webhook Authentication Hardening

## HISTORY

### v0.3.0 (2026-04-24)
- Scope expanded from portal-api webhook surface to every klai FastAPI service.
  During the internal audit wave it was found that NO klai FastAPI service runs
  uvicorn with `--proxy-headers` -- retrieval-api, knowledge-ingest, and scribe-api
  all share portal-api's original defect. In retrieval-api the consequence is not
  just a bypassable auth gate (that service uses `hmac.compare_digest` on
  `X-Internal-Secret` and JWT, both of which are header-based and unaffected by
  `request.client.host`) but a collapsible rate-limit bucket: the
  internal-secret rate-limit key is derived from `_source_ip(request)` at
  `retrieval_api/middleware/auth.py:233-245`, which trusts
  `X-Forwarded-For` unconditionally. Any klai-net peer can forge XFF to bypass
  the 600 rpm ceiling or collapse all external identities into its own container IP.
- New finding added to the Findings table.
- REQ-1 reframed from "portal-api uvicorn" to "every klai FastAPI service's uvicorn".
- New REQ-6 added: shared uvicorn launch wrapper so every service Dockerfile uses
  the same trusted-proxy flags consistently.
- New sub-requirement REQ-1.5 added: at the rate-limiter/source-IP derivation layer,
  trust in `X-Forwarded-For` MUST be gated on the same trusted-proxy allowlist that
  uvicorn applies -- i.e. only honour the header when the TCP peer is in the
  allowlist.
- Environment section expanded to list all klai FastAPI service Dockerfiles and
  entrypoints in scope.
- Assumptions expanded: Caddy internal IP may differ per network and per service;
  the `--forwarded-allow-ips` allowlist must be derived per service deploy.

### v0.2.0 (2026-04-24)
- Expanded from stub into full EARS SPEC by manager-spec
- Added research.md (codebase analysis) and acceptance.md (verifiable scenarios)
- EARS requirements REQ-1 through REQ-5 with sub-requirements
- Success criteria mapped to AC-1..AC-10 in acceptance.md
- Out of scope: mTLS, replay protection, broader /internal auth refactor

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P0 -- Vexa webhook auth is effectively bypassed for every external caller
- Expand via `/moai plan SPEC-SEC-WEBHOOK-001`

---

## Findings addressed

| # | Finding | Severity | Source |
|---|---|---|---|
| 2 | Vexa webhook trusts 172/10/192.168 IP ranges; uvicorn lacks `--proxy-headers` | CRITICAL | `meetings.py:46-58` |
| 3 | Moneybird webhook fails open when token env var is empty | HIGH | `webhooks.py:24-28` |
| 4 | Moneybird webhook uses `!=` instead of `hmac.compare_digest` | HIGH | `webhooks.py:26` |
| W1 | XFF-spoof + container-IP rate-limit-bucket collapse across klai FastAPI services. Every klai FastAPI service runs uvicorn WITHOUT `--proxy-headers`. portal-api is the headline case (see finding #2), but retrieval-api has the same pattern AND its rate limiter trusts `X-Forwarded-For` as ground-truth for the source-IP bucket. Any klai-net peer (litellm, portal-api forwarder, research-api) can set arbitrary XFF to bypass the 600 rpm ceiling or collapse all external users into the caller's container IP. Same pattern applies to klai-scribe and klai-knowledge-ingest. | HIGH | `klai-retrieval-api/retrieval_api/middleware/auth.py:233-245`, `klai-retrieval-api/Dockerfile:15`, `klai-scribe/scribe-api/Dockerfile:14` (CMD uvicorn without `--proxy-headers`), `klai-knowledge-ingest/Dockerfile:41` (same) |

---

## Goal

Ensure every webhook endpoint in klai-portal authenticates the caller with a cryptographically
strong, fail-closed mechanism that cannot be bypassed by Docker-network IP position, empty
configuration, or timing side-channel.

The Vexa webhook (`POST /api/bots/internal/webhook`) currently treats any caller with a source
IP starting with `172.`, `10.`, or `192.168.` as authenticated -- and because uvicorn runs
without `--proxy-headers`, every request from Caddy arrives with `request.client.host` set to
the Caddy container's Docker IP, which always matches that prefix. The Bearer-token gate is
therefore unreachable for any real external caller; the endpoint is effectively open. Moneybird
has a parallel set of defects (fail-open on empty secret, non-constant-time compare,
authentication failure returning HTTP 200).

This SPEC closes both. It does NOT migrate webhooks to mTLS (future SPEC) and does NOT add
replay protection (future SPEC).

---

## Environment

- Services in scope (all FastAPI + uvicorn on `klai-net`):
  - `klai-portal/backend` -- original scope (v0.2.0)
  - `klai-retrieval-api` -- added in v0.3.0 (XFF-spoofed rate-limit bucket)
  - `klai-knowledge-ingest` -- added in v0.3.0 (same uvicorn defect; low-severity today because the service's only public-ish surface is internal-secret guarded, but the pattern must not spread)
  - `klai-scribe/scribe-api` -- added in v0.3.0 (same uvicorn defect)
- Runtime: Python 3.12/3.13 across services, pydantic-settings `Settings` class per service
- Request path (portal-api example): Caddy (TLS termination, `X-Request-ID` injection) → portal-api:8010 → route handler. Internal services (retrieval-api, knowledge-ingest, scribe) are called service-to-service on `klai-net` and do NOT route through Caddy, but every sibling container on `klai-net` can open a TCP connection to them.
- Files in scope (v0.2.0, portal-api):
  - [klai-portal/backend/app/api/meetings.py](../../../klai-portal/backend/app/api/meetings.py) -- `_require_webhook_secret` (L46-58), Vexa handler (L644)
  - [klai-portal/backend/app/api/webhooks.py](../../../klai-portal/backend/app/api/webhooks.py) -- Moneybird handler (L17-81)
  - [klai-portal/backend/entrypoint.sh](../../../klai-portal/backend/entrypoint.sh) -- uvicorn launch (L16)
  - [klai-portal/backend/Dockerfile](../../../klai-portal/backend/Dockerfile) -- image layout
  - [deploy/caddy/Caddyfile](../../../deploy/caddy/Caddyfile) -- `X-Forwarded-For` / `X-Request-ID` injection
  - [klai-portal/backend/app/core/config.py](../../../klai-portal/backend/app/core/config.py) -- `vexa_webhook_secret`, `moneybird_webhook_token`, existing `_require_vexa_webhook_secret` validator
  - [klai-portal/backend/tests/test_meetings_webhook_auth.py](../../../klai-portal/backend/tests/test_meetings_webhook_auth.py) -- existing coverage (must be updated: IP-bypass test is deleted, fail-closed 401 test is added)
- Files in scope (v0.3.0 additions, internal services):
  - [klai-retrieval-api/Dockerfile](../../../klai-retrieval-api/Dockerfile) L15 -- `CMD ["uvicorn", "retrieval_api.main:app", "--host", "0.0.0.0", "--port", "8040"]` -- no `--proxy-headers`, no `--forwarded-allow-ips`
  - [klai-retrieval-api/retrieval_api/middleware/auth.py](../../../klai-retrieval-api/retrieval_api/middleware/auth.py) L233-245 -- `_source_ip` + `_rate_limit_key`, XFF trusted unconditionally
  - [klai-knowledge-ingest/Dockerfile](../../../klai-knowledge-ingest/Dockerfile) L41 -- `CMD ["uvicorn", "knowledge_ingest.app:app", "--host", "0.0.0.0", "--port", "8000"]` -- no `--proxy-headers`
  - [klai-scribe/scribe-api/Dockerfile](../../../klai-scribe/scribe-api/Dockerfile) L25 -- `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8020"]` -- no `--proxy-headers` (note: referenced as L14 in the task input; actual line is L25 post-ffmpeg install, see research.md for the full inventory)
- Webhook endpoints inventoried (see research.md):
  - `POST /api/bots/internal/webhook` (Vexa -- `meetings.py:644`)
  - `POST /api/webhooks/moneybird` (Moneybird -- `webhooks.py:17`)
  - No Zitadel Actions webhook is currently defined inside `klai-portal/backend/app/api/` (Zitadel Actions reach portal-api via `/internal/*`, which is SPEC-SEC-INTERNAL-001's scope, not this one -- see research.md).
- Non-webhook rate-limit surfaces inventoried (v0.3.0):
  - `retrieval-api` internal-secret path, key = `retrieval:rl:internal:<source_ip>` at `middleware/auth.py:245`, source = first hop of `X-Forwarded-For` when present else `request.client.host` (L233-239)
  - See research.md "Internal-wave additions" for the full per-service table

---

## Assumptions

- Caddy already sets `X-Forwarded-For` with the real client IP and `X-Request-ID` with a per-request UUID on the upstream REQUEST (not response) at `deploy/caddy/Caddyfile:30-35`. This SPEC relies on that behaviour; it is verified in research.md.
- All legitimate external callers of `/api/bots/internal/webhook` are Vexa `api-gateway` (same Docker network) and the in-cluster `api-gateway` configured via `POST_MEETING_HOOKS` at `docker-compose.yml:886`. Neither relies on IP-range trust -- both can present a Bearer token from a shared secret.
- Vexa's `POST_MEETING_HOOKS` can be (re)configured to include an `Authorization: Bearer <secret>` header, OR Vexa can call a URL variant that embeds the secret. Research.md confirms the current config uses a plain URL with no Bearer; SPEC-SEC-INTERNAL-001's sibling work on `POST_MEETING_HOOKS URL secret embedding` (tracker appendix A7) establishes the header-based pattern as the agreed direction.
- Caddy container's Docker IP on `klai-net` is known and stable enough per deploy to be used in a trusted-proxy allowlist. If Caddy is ever restarted with a new IP, uvicorn's `--forwarded-allow-ips` will need to be updated (documented in the runbook).
- The existing pydantic validator `_require_vexa_webhook_secret` at `config.py:235-248` already enforces fail-closed startup for Vexa. An analogous validator must be added for Moneybird.
- (v0.3.0) The Caddy internal IP used for `--forwarded-allow-ips` may differ per service context. portal-api is reached directly by Caddy on `klai-net` and uses Caddy's IP. retrieval-api, knowledge-ingest, and scribe-api are NOT reached through Caddy for their primary traffic -- they are reached from portal-api and/or LiteLLM. For those services the allowlist is EITHER the set of trusted internal-service container IPs (portal-api, litellm) that have a legitimate reason to set XFF, OR the empty set (i.e. `--forwarded-allow-ips=127.0.0.1` so NO upstream is trusted to set XFF and `X-Forwarded-For` is completely ignored). The empty-set option is safer and is the default choice for internal services that do not need to surface a real external caller IP; `_source_ip` then falls back to the TCP peer which is the actual service-to-service caller. Per-service choice is documented in research.md.
- (v0.3.0) `_source_ip` at `retrieval_api/middleware/auth.py:233-245` reads `X-Forwarded-For` directly from `request.headers` at the application layer, independent of uvicorn's `--proxy-headers` behaviour. Fixing uvicorn alone is NOT sufficient for retrieval-api -- the application-level XFF trust must also be gated or removed. This is what REQ-1.5 addresses.

---

## Requirements

### REQ-1: Trusted-Proxy Headers at uvicorn Startup (every klai FastAPI service)

The system SHALL configure uvicorn so that `request.client.host` reflects the real external caller IP, not the immediate TCP peer's container IP, for EVERY klai FastAPI service -- portal-api, retrieval-api, knowledge-ingest, and scribe-api.

- **REQ-1.1:** WHEN any klai FastAPI service container starts, THE container's CMD or entrypoint SHALL invoke uvicorn with `--proxy-headers` AND `--forwarded-allow-ips=<trusted-peer-ip-or-127.0.0.1>`. The specific services in scope are portal-api (`klai-portal/backend/entrypoint.sh:16`), retrieval-api (`klai-retrieval-api/Dockerfile:15`), knowledge-ingest (`klai-knowledge-ingest/Dockerfile:41`), and scribe-api (`klai-scribe/scribe-api/Dockerfile:25`).
- **REQ-1.2:** WHERE `--forwarded-allow-ips` is set for portal-api, THE allowlist SHALL contain ONLY the Caddy container's IP on `klai-net` (or a `/32` CIDR equivalent). It SHALL NOT contain `*`, `0.0.0.0/0`, or a broad private-range prefix.
- **REQ-1.3:** IF the Caddy container IP changes between deploys, THEN the allowlist SHALL be updated in the same deploy -- this constraint SHALL be documented inline in `entrypoint.sh` (portal-api) or in the per-service launch wrapper from REQ-6 (internal services) and referenced from `deploy.md`.
- **REQ-1.4:** WHEN a webhook or service request arrives at any klai FastAPI service from a TCP peer that is NOT in the trusted-proxy allowlist, THE uvicorn layer SHALL treat `X-Forwarded-For` as untrusted AND `request.client.host` SHALL reflect the raw TCP peer (preventing header spoofing).
- **REQ-1.5:** WHERE the application layer reads `X-Forwarded-For` directly (bypassing uvicorn's proxy-headers handling) -- specifically `_source_ip` at `klai-retrieval-api/retrieval_api/middleware/auth.py:233-239` -- THE application-level read SHALL be gated on the same trusted-proxy allowlist: `request.headers.get("x-forwarded-for")` SHALL be honoured ONLY when the TCP peer (`request.client.host`, observed AFTER uvicorn proxy-header processing) is in the trusted-proxy allowlist. Equivalently: the rate-limit key derivation SHALL use `request.client.host` after uvicorn has applied `--proxy-headers`, and SHALL NOT re-read the raw `X-Forwarded-For` header. Either implementation is acceptable; both produce the same result (spoofed XFF from an untrusted peer is ignored).
- **REQ-1.6:** WHERE `--forwarded-allow-ips` for retrieval-api, knowledge-ingest, or scribe-api is set to `127.0.0.1` (the safe default for internal services that do not need to surface a real external caller IP), THE resulting `request.client.host` SHALL equal the TCP peer's container IP on `klai-net`. This is the intended behaviour for identity-based rate-limiting against a fixed set of known internal callers.

### REQ-2: Remove IP-Range Early-Return from Vexa Webhook Auth

The system SHALL NOT treat Docker-network source IPs as implicitly authenticated.

- **REQ-2.1:** WHEN `_require_webhook_secret` in `klai-portal/backend/app/api/meetings.py` executes, THE function SHALL NOT short-circuit on `client_host.startswith(("172.", "10.", "192.168."))` or on any other IP/CIDR check. Authentication SHALL be determined solely by the Bearer token comparison.
- **REQ-2.2:** WHEN a request arrives at `POST /api/bots/internal/webhook` without a valid `Authorization: Bearer <vexa_webhook_secret>` header, THE service SHALL return HTTP 401 with body `{"detail": "Unauthorized"}` regardless of the caller's source IP.
- **REQ-2.3:** WHILE the request is being authenticated, THE comparison SHALL use `hmac.compare_digest(auth_header.encode("utf-8"), expected.encode("utf-8"))` against the full `Bearer <secret>` string (current behaviour at `meetings.py:57`, which SHALL be preserved).

### REQ-3: Fail-Closed Startup on Missing Moneybird Secret

The system SHALL refuse to start if a required webhook secret is not configured.

- **REQ-3.1:** WHEN `Settings` is instantiated at app startup AND `moneybird_webhook_token` is empty or contains only whitespace, THE `Settings` model validator SHALL raise `ValueError("Missing required: MONEYBIRD_WEBHOOK_TOKEN")`, aborting the uvicorn process before any request is served. This SHALL mirror the existing `_require_vexa_webhook_secret` validator pattern at `config.py:235-248`.
- **REQ-3.2:** WHEN `moneybird_webhook_token` is populated, THE service SHALL treat it as required for every call to `POST /api/webhooks/moneybird`. The runtime handler SHALL NOT contain a `if settings.moneybird_webhook_token:` guard that makes the check optional.
- **REQ-3.3:** IF a future deployment wants to disable Moneybird webhook processing, THEN the operator SHALL unregister the router rather than leave the secret empty -- this SHALL be documented in the runbook. Empty-secret is never a valid runtime state.

### REQ-4: Constant-Time Compare and 401 on Moneybird Auth Failure

The system SHALL reject Moneybird webhook requests with the wrong token using a timing-safe comparison and HTTP 401.

- **REQ-4.1:** WHEN a request arrives at `POST /api/webhooks/moneybird`, THE handler SHALL extract `payload.get("webhook_token", "")` AND SHALL compare it to `settings.moneybird_webhook_token` via `hmac.compare_digest(token.encode("utf-8"), settings.moneybird_webhook_token.encode("utf-8"))`. The `!=` comparison at current `webhooks.py:26` SHALL be replaced.
- **REQ-4.2:** WHEN the Moneybird token comparison fails, THE handler SHALL raise `HTTPException(status_code=401, detail="Unauthorized")`. It SHALL NOT return `Response(status_code=200)` as at current `webhooks.py:28`.
- **REQ-4.3:** WHEN the Moneybird webhook auth fails, THE handler SHALL emit a structlog warning with `event="moneybird_webhook_auth_failed"` AND SHALL include the request's `X-Request-ID` for cross-service correlation (propagated via `RequestContextMiddleware`).

### REQ-5: Regression Tests

The system SHALL carry pytest coverage for each regression class closed by this SPEC.

- **REQ-5.1:** A test SHALL send a POST to `/api/bots/internal/webhook` from a simulated 172.x source IP with no `Authorization` header AND SHALL assert the response status is 401. The existing passing test `test_require_webhook_secret_docker_network_trusted_without_bearer` at `tests/test_meetings_webhook_auth.py:117` SHALL be deleted or inverted as part of this SPEC.
- **REQ-5.2:** A test SHALL instantiate `Settings()` with `MONEYBIRD_WEBHOOK_TOKEN=""` AND SHALL assert that a `ValueError` is raised (covering REQ-3.1).
- **REQ-5.3:** A test SHALL send a POST to `/api/webhooks/moneybird` with a wrong token AND SHALL assert response status is 401 (not 200, covering REQ-4.2).
- **REQ-5.4:** A pytest microbenchmark SHALL call `_require_webhook_secret` with wrong tokens of length 1, 16, and 64 and SHALL assert the mean wall-clock difference is below 50 microseconds per call -- this documents (not enforces) the constant-time property. The benchmark SHALL live under `tests/` but be marked `@pytest.mark.slow` so CI can skip it on the fast path.
- **REQ-5.5:** A test SHALL send a legitimate POST to `/api/bots/internal/webhook` with a correct `Authorization: Bearer <vexa_webhook_secret>` header AND from a Caddy-forwarded source (X-Forwarded-For set) AND SHALL assert response status is 200. This covers the happy path after IP-bypass removal.
- **REQ-5.6:** (v0.3.0) A test SHALL send a POST to a retrieval-api endpoint from a simulated klai-net peer with `X-Forwarded-For: 1.2.3.4` AND a valid `X-Internal-Secret` header AND SHALL assert that the rate-limit key derived for that request is `retrieval:rl:internal:<tcp-peer-ip>`, NOT `retrieval:rl:internal:1.2.3.4`. This proves XFF spoofing no longer affects bucket identity.

### REQ-6: Shared uvicorn Launch Wrapper

The system SHALL provide a single shared mechanism for launching uvicorn with the trusted-proxy flags, so every klai FastAPI service Dockerfile uses it consistently and future services inherit the hardening automatically.

- **REQ-6.1:** THE repository SHALL provide a shared launch wrapper -- either a script (e.g. `scripts/uvicorn-launch.sh` mounted/copied into each image) OR a Makefile target (e.g. `make run-service SERVICE=<name>`) OR a common base Dockerfile layer -- that accepts the application path, host, port, AND the trusted-proxy allowlist as inputs, and invokes uvicorn with `--proxy-headers` and `--forwarded-allow-ips=<allowlist>` injected unconditionally.
- **REQ-6.2:** WHERE REQ-6.1's wrapper is adopted, THE Dockerfile CMD for portal-api, retrieval-api, knowledge-ingest, and scribe-api SHALL call the wrapper. A static grep for `uvicorn ... --host ... --port ...` without `--proxy-headers` in any service Dockerfile SHALL return zero matches after this SPEC lands.
- **REQ-6.3:** THE wrapper SHALL fail-closed: if the trusted-proxy allowlist env var is unset, the wrapper SHALL exit non-zero BEFORE uvicorn binds. Services that legitimately want "no upstream is trusted" (the internal-service default) SHALL set the allowlist to `127.0.0.1` explicitly -- silent fallback to "unset" is forbidden.
- **REQ-6.4:** THE wrapper SHALL be documented in `.claude/rules/klai/lang/python.md` OR a sibling service-rules file so future services adding a Dockerfile inherit the expectation by reading the rules. The documentation SHALL include the exact wrapper invocation line and the rationale (this SPEC).

---

## Non-Functional Requirements

- **Observability:** Both 401 outcomes SHALL be queryable in VictoriaLogs via stable event keys (`vexa_webhook_auth_failed`, `moneybird_webhook_auth_failed`).
- **Backward compatibility:** The Vexa `api-gateway` SHALL continue to function after this SPEC lands, provided `POST_MEETING_HOOKS` is configured with the Bearer header (see Assumptions). If `POST_MEETING_HOOKS` is still a plain URL at deploy time, the Vexa webhook WILL start failing with 401 -- this is the intentional forcing function.
- **Performance:** `hmac.compare_digest` vs `!=` adds nanoseconds per request. No perceivable impact.
- **Backward compatibility (Moneybird):** The Moneybird dashboard SHALL continue to POST with `webhook_token` in the payload (Moneybird's own format). No change to the Moneybird side.
- **Fail modes:** Both webhooks fail CLOSED after this SPEC. A misconfigured secret means 401 for every caller, loudly visible. This is the intended property; silent fail-open is the defect being fixed.

---

## Success Criteria

- Uvicorn runs with `--proxy-headers` and a narrow `--forwarded-allow-ips` allowlist; `request.client.host` for a webhook POST reflects the real external caller IP, not the Caddy container IP (covered by AC-4 in acceptance.md).
- `_require_webhook_secret` contains no IP-range early-return path; every request is authenticated solely by `hmac.compare_digest` against the Bearer token (AC-1, AC-2).
- Moneybird webhook raises 503 at startup (not at request time) if `MONEYBIRD_WEBHOOK_TOKEN` is not configured; no conditional `if settings.X:` guard remains (AC-5).
- Moneybird token comparison uses `hmac.compare_digest`; auth failure returns HTTP 401, not 200 (AC-6, AC-7).
- A regression test sends a webhook request from a 172.x source IP with no Bearer header and asserts 401 (AC-3).
- A regression test instantiates `Settings` with an empty `MONEYBIRD_WEBHOOK_TOKEN` and asserts `ValueError` (AC-5).
- A timing benchmark exists and demonstrates near-constant-time comparison (AC-8).
- A regression test sends a legitimate Caddy-forwarded request with a correct Bearer and asserts 200 (AC-9).
- (v0.3.0) Every klai FastAPI service Dockerfile CMD invokes uvicorn with `--proxy-headers` either directly or via the shared wrapper -- a static grep of all service Dockerfiles for `uvicorn` lines without `--proxy-headers` returns zero matches (AC-11, AC-12).
- (v0.3.0) retrieval-api rate-limit key derivation no longer trusts untrusted-peer `X-Forwarded-For` -- a forged XFF from a klai-net peer produces a rate-limit key based on the TCP peer IP, not the spoofed header value (AC-13).

---

## Out of scope

- Migrating webhooks to mTLS (candidate future SPEC -- SPEC-INFRA-TLS-001)
- Adding replay protection via nonce / timestamp for webhooks (future SPEC)
- Broader /internal auth refactor -- see SPEC-SEC-INTERNAL-001 (appendix findings A2-A4)
- Removing secrets embedded in URL query parameters -- tracker item A7, handled in a pitfalls-rule update, not a code SPEC
- Adding an audit log for webhook calls -- analogous to SPEC-SEC-005 REQ-2 for `/internal/*`, but webhooks are lower-volume and can be added later if needed

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md) (findings #2, #3, #4)
- Related: [SPEC-SEC-005](../SPEC-SEC-005/spec.md) -- existing internal-endpoint hardening (different endpoint class; some patterns reused)
- Related: SPEC-SEC-INTERNAL-001 -- planned broader `/internal/*` surface hardening
- Audit source: `SECURITY.md` by Cornelis Poppema, 2026-04-22 (findings #2, #3, #4)
- Existing Vexa fail-closed validator: `klai-portal/backend/app/core/config.py:235-248` (`_require_vexa_webhook_secret`)
- Reference uvicorn proxy-headers docs: https://www.uvicorn.org/settings/#http (Context7 lookup at implementation time if clarification needed)
