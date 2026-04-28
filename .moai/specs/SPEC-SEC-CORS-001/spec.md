---
id: SPEC-SEC-CORS-001
version: 0.5.0
status: shipped
created: 2026-04-24
updated: 2026-04-27
author: Mark Vletter
priority: critical
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-CORS-001: CORS Allowlist + CSRF-Exempt Scope Review

## HISTORY

### v0.5.0 (2026-04-27) — POST-SHIP CLOSE-OUT

PR #180 squash-merged to main as commit `65f5419d`. New portal-api,
klai-connector, klai-retrieval-api images built and deployed to core-01
the same morning. Live verification on `https://my.getklai.com`:

- `OPTIONS /api/me` with `Origin: https://my.getklai.com` returns HTTP
  200 with `Access-Control-Allow-Origin: https://my.getklai.com` +
  `Access-Control-Allow-Credentials: true` + `Vary: Origin`. Legit
  first-party traffic intact.
- `OPTIONS /api/me` with `Origin: https://evil.example` returns HTTP
  400 with **no** `Access-Control-Allow-Origin` and **no**
  `Access-Control-Allow-Credentials`. REQ-1 + REQ-1.5 enforced; the
  ACAC strip override on `KlaiCORSMiddleware.preflight_response`
  works as designed.

Round-2 simplify-pass landed as commits `58f724a1`, `d3cfa8c5`,
`6818c830`, `505ae4a1`, `3a3e8709` (all squashed into the merge):

- KlaiCORSMiddleware refactored to a thin Starlette CORSMiddleware
  subclass (~145 → ~100 lines, leans on parent for header logic).
- AC-13 observability extended to simple-request rejections (was
  preflight-only).
- Module-scoped `cors_client` fixture cuts test runtime in half.
- pytest `monkeypatch` replaces manual try/finally in AC-14 test.
- Partner CORS-header dict extracted into `_widget_cors_headers`
  helper.
- `_CSRF_EXEMPT_PREFIXES` rationale lines normalised to canonical
  `# REQ-X.Y / AC-Z` trailing format with a new mechanical lint
  (`test_csrf_exempt_rationale_format_is_canonical`).
- @MX:ANCHOR/@MX:NOTE annotations added on klai_cors.py overrides
  and _compile_first_party_regex; @MX:WARN on widget_config_preflight
  documenting the pre-existing DB-before-origin pattern.

Phase 2.8a evaluator-active (thorough harness) returned ACCEPT WITH
FOLLOW-UPS, all 4 dimensions PASS, no CRITICAL or HIGH findings. Three
LOW findings; two were fixed in `3a3e8709` (request_id truncation +
redundant case-variant Origin lookup). The third (Unicode `→` vs ASCII
`->` arrow drift between scribe and the three SPEC-modified modules)
is cosmetic and tracked as a follow-up issue.

Pre-existing portal-api Trivy CVE pattern (every recent main build
fails the same scan job) is unrelated to this SPEC and was not
introduced by it. Tracked separately.

Final scoreboard:
- 18/18 ACs PASS (server-side + lint + CI wiring)
- 70 new tests, all green; full portal-api suite 1187 / 0 fail
- ast-grep `cors_middleware_last.yml` exits 0 on all 8 in-scope
  service entry modules
- ruff format + pyright clean
- klai-infra commit `4a27983` deployed `CORS_ORIGINS=https://my.getklai.com`
  to `/opt/klai/.env` ahead of the validator change (validator-env-parity
  preflight observed)

Outstanding follow-ups (filed as separate issues, not blocking):
1. SPEC-SEC-PUBLIC-LOOKUP-001: Caddy per-IP rate limit on
   `/partner/v1/widget-config` + in-process `TTLCache` on
   `widget_id → allowed_origins` with write-through invalidation,
   plus `klai-libs/public-lookup` decorator generalising rate-limit
   + cache + origin-precheck for future public lookup endpoints.
2. portal-api Trivy CVE baseline — operational hygiene.
3. Comment arrow style alignment (`→` vs `->`) across scribe and
   the three SPEC-modified entry modules.
4. Browser-level Playwright e2e for cross-origin CORS verification —
   infra-zware setup; AC-level coverage already complete via
   server-side header assertions + ast-grep lint.

### v0.4.0 (2026-04-25)

### v0.4.0 (2026-04-25)
- Phase 1 (manager-strategy) verified all research.md findings against current
  code in the worktree. Two BLOCKING discrepancies surfaced and folded into the
  implementation plan:
  - Verification B: portal-api `app/main.py:180-203` registers CORSMiddleware
    BEFORE `LoggingContextMiddleware` (line 199) and `SessionMiddleware`
    (line 203). Per Starlette LIFO, SessionMiddleware is OUTSIDE CORSMiddleware,
    so a 401 emitted by SessionMiddleware (CSRF reject at session.py:75-77)
    bypasses CORS. This is the same failure class REQ-6 demands of every other
    service. Resolved by adding REQ-6.7 (portal-api self-fix) to scope —
    CORSMiddleware MUST be the LAST add_middleware call in portal-api's
    `app/main.py` too. Implemented as the Q1-FIX task between T-014 and T-015.
  - Verification K: production compose env block for portal-api does NOT pass
    `CORS_ORIGINS`. Combined with the in-code default `cors_origins: str =
    "http://localhost:5174"`, today's wildcard regex r".*" is the only thing
    keeping prod browsers from `my.getklai.com` working. Once REQ-1 narrows
    the regex, prod 502s on first restart unless `CORS_ORIGINS` is added to
    `klai-infra/core-01/.env.sops` AND to `deploy/docker-compose.yml`'s
    portal-api `environment:` block FIRST (validator-env-parity). Folded into
    pre-flight task T-000A.bis; documented in Cross-references as the SPEC-
    SEC-WEBHOOK-001 / SPEC-SEC-ENVFILE-SCOPE-001 same-deploy regression class.
- Added REQ-6.7: portal-api `app/main.py` CORSMiddleware order parity (treat
  portal-api the same way the lint demands of every other service).
- Updated REQ-6.2 / REQ-6.3 lint rule location: from speculative
  `.claude/lint/cors_middleware_last.yml` to `rules/cors_middleware_last.yml`,
  matching existing repo precedent (`rules/no-exec-run.yml` discovered via
  repo-root `sgconfig.yml`). acceptance.md AC-18 paths updated accordingly.
- Status bumped from `draft` to `in_progress`. Phase 2 implementation runs on
  branch `feature/SPEC-SEC-CORS-001` in worktree
  `/c/Users/markv/.moai/worktrees/klai/SPEC-SEC-CORS-001`.
- Added planning artefacts `tasks.md` (25 atomic tasks) and `progress.md`.
  Drift target: 28 files (17 modified + 11 created).

### v0.3.0 (2026-04-24)
- Internal-wave additions from the cross-service middleware audit triggered by the
  Cornelis finding #25 re-check. The original finding #25 (CORS does not wrap 401)
  remains REFUTED for klai-portal/backend, but the INVERSE bug exists in
  klai-connector: CORSMiddleware is registered FIRST (→ innermost), AuthMiddleware
  is registered LAST (→ outermost), so 401 responses from AuthMiddleware never
  touch CORSMiddleware and browsers see opaque network errors cross-origin.
- New Finding III (HIGH): klai-connector middleware order inversion — 401 bypasses
  CORS, same failure class as #25 but in a different service.
- New Finding IV (MEDIUM): klai-retrieval-api has no CORSMiddleware at all. Not
  browser-exposed today (no Caddy route), but a future exposure would immediately
  make every JWT-authenticated principal cross-origin-exploitable with no policy
  to bind against. Defense-in-depth deny-by-default starter required now.
- Added REQ-6: repo-wide middleware-order lint enforced across every klai FastAPI
  service, asserting CORSMiddleware is the LAST `add_middleware` call (= outermost
  execution).
- Added REQ-7: klai-retrieval-api SHALL register CORSMiddleware with an empty
  allowlist (deny-by-default) as a starter; any future Caddy exposure SHALL update
  the allowlist explicitly.
- Environment section extended with klai-connector `app/main.py` and
  klai-retrieval-api `retrieval_api/main.py`.
- research.md appended with "Internal-wave additions (2026-04-24)": per-service
  middleware-order audit table explaining why the same bug class exists in one
  service and not another (copy-paste drift).
- acceptance.md appended with AC-15 through AC-18 covering connector 401+CORS,
  retrieval-api CORS presence, and the ast-grep / CI lint gate.

### v0.2.0 (2026-04-24)
- Expanded stub into full EARS SPEC via `/moai plan SPEC-SEC-CORS-001`
- Added research.md (codebase analysis of current CORS + CSRF-exempt state)
- Added acceptance.md (testable scenarios for cross-origin blocks and widget exemption)
- Promoted requirement set to five EARS groups: explicit allowlist, widget CORS split,
  no-credentials for widget, CSRF-exempt inline rationale, regression tests
- Confirmed finding #25 ("CORS does not wrap 401") remains REFUTED — CORSMiddleware is
  registered last and therefore executes first per Starlette's reverse-registration rule.
  See `.claude/rules/klai/lang/python.md` → "Starlette middleware registration order".

### v0.1.0 (2026-04-24)
- Stub created from SPEC-SEC-AUDIT-2026-04 (Cornelis audit 2026-04-22)
- Priority P0 — wildcard CORS + CSRF-exempt login = cross-origin credential probing

---

## Findings addressed

| # | Finding | Severity |
|---|---|---|
| 1 | `cors_allow_origin_regex = r".*"` combined with `allow_credentials=True` in portal-api | CRITICAL |
| 17 | `/api/auth/login`, `/api/auth/totp-login`, `/api/signup` in `_CSRF_EXEMPT_PREFIXES` in portal-api | HIGH |
| III | klai-connector middleware order inversion: AuthMiddleware wraps CORSMiddleware → 401 responses do not carry CORS headers. Exact class of bug Cornelis #25 claimed for portal-api, actually present in connector. | HIGH |
| IV | klai-retrieval-api has no CORSMiddleware registered at all. No browser exposure today (no Caddy route), but zero defense-in-depth — a future route immediately opens cross-origin credential probing against every JWT-authenticated principal (including widget session tokens). | MEDIUM |

Finding 25 (CORS does not wrap 401) for klai-portal/backend remains REFUTED —
verified in v0.2.0 and re-verified in v0.3.0. The symmetric inverse of #25 is
Finding III (klai-connector), which IS real and is addressed by REQ-6.

---

## Goal

Replace the credentialed wildcard CORS regex (`r".*"`) in portal-api with an explicit
allowlist that grants credentialed cross-origin access only to first-party Klai domains.
Split widget traffic (public, cookie-less) into a separate CORS policy that never sets
`Access-Control-Allow-Credentials: true` and is enforced per-route via origin allowlists
loaded from the `widgets` table. Audit every entry in `_CSRF_EXEMPT_PREFIXES` so each
prefix carries an inline comment stating why CSRF cannot apply there, backed by a
regression test that proves cross-origin credentialed probing of that prefix is blocked.

Extend the scope in v0.3.0: every klai FastAPI service SHALL register CORSMiddleware
LAST in its `add_middleware` stack so that error responses emitted by upstream auth
middleware carry CORS headers, and klai-retrieval-api SHALL register a deny-by-default
CORSMiddleware now (even though it is not yet browser-reachable) so that a future
Caddy route does not silently inherit an implicit "no policy → no headers" stance.

Success looks like: a malicious site (`evil.example`) cannot drive a state-changing
request to portal-api using a victim's BFF session cookie, even if the endpoint is in
the CSRF-exempt list, because the browser-level CORS policy refuses to send credentials
cross-origin to any non-allowlisted origin; AND no klai service fails a browser fetch
in a cryptic opaque-response way because a 401/403 from an auth middleware stripped
the CORS envelope; AND klai-retrieval-api fails closed on any future browser traffic
until an explicit allowlist is set.

---

## Environment

- **Primary service:** `klai-portal/backend` (FastAPI, Starlette CORSMiddleware, Python 3.13)
- **In-scope services for middleware-order lint (REQ-6):**
  - `klai-portal/backend/app/main.py`
  - `klai-connector/app/main.py`
  - `klai-retrieval-api/retrieval_api/main.py`
  - `klai-scribe/scribe-api/app/main.py`
  - `klai-knowledge-ingest/knowledge_ingest/main.py` (or equivalent entry)
  - `klai-mailer/mailer/main.py` (or equivalent entry)
  - `klai-knowledge-mcp` (FastAPI entry)
  - `klai-focus/research-api/research_api/main.py` (or equivalent entry)
- **Files in scope:**
  - `klai-portal/backend/app/core/config.py` — `cors_origins`, `cors_allow_origin_regex`,
    `cors_origins_list` property (lines 211-219, 232-233)
  - `klai-portal/backend/app/main.py` — `CORSMiddleware` registration (lines 179-186)
  - `klai-portal/backend/app/middleware/session.py` — `_CSRF_EXEMPT_PREFIXES`
    (lines 35-55) and `_check_csrf` (lines 116-135)
  - `klai-portal/backend/app/api/partner.py` — `/partner/v1/widget-config` endpoint
    that already hand-rolls per-route CORS headers from `widget_config.allowed_origins`
    (lines 388-517)
  - `klai-connector/app/main.py` — middleware registration (lines 182-193). Today:
    CORSMiddleware added first (inner), AuthMiddleware added second (middle),
    RequestContextMiddleware added last (outer). Starlette LIFO execution order:
    RequestContext → Auth → CORS → route. AuthMiddleware's 401 `JSONResponse` is
    emitted BEFORE CORS wraps the response, so browsers see an opaque failure.
  - `klai-retrieval-api/retrieval_api/main.py` — middleware registration (lines 59-67).
    Today: `AuthMiddleware` then `RequestContextMiddleware` are registered. No
    `CORSMiddleware` at all. No CORS policy surface.
- **Reference service for correct order:** `klai-scribe/scribe-api/app/main.py` —
  `AuthGuardMiddleware` first (innermost), `RequestContextMiddleware` middle,
  `CORSMiddleware` last (outermost). Comment above the block explicitly documents the
  reverse-registration rationale.
- **Session model:** BFF session cookie (SPEC-AUTH-008) resolved by `SessionMiddleware`.
  Cross-origin credentialed requests rely on `Access-Control-Allow-Credentials: true`
  being echoed by CORSMiddleware.
- **Widget model:** Per-widget `allowed_origins` list stored in `widgets.widget_config`
  JSONB, validated by `origin_allowed()` in `app.services.widget_auth`. Already
  fail-closed on empty list.
- **Observability:** Caddy access logs include `Origin` header; VictoriaLogs ingests
  portal-api structlog with `request_id`. Query pattern for verification:
  `service:caddy AND status:2* AND origin:<value>` (see
  `.claude/rules/klai/infra/observability.md`).

---

## Assumptions

- All legitimate first-party browser traffic reaches portal-api via
  `https://my.getklai.com` (SPA + API proxy) or `https://{tenant}.getklai.com` (per-
  tenant hostname, resolved by Caddy wildcard + tenant matcher).
- Widget traffic does NOT use the BFF session cookie. Widget chat completions authenticate
  via partner API keys (`Authorization: Bearer pk_live_...`) or short-lived session
  tokens minted by `/partner/v1/widget-config`. Credentials-mode `include` is therefore
  never required for `/partner/v1/*` endpoints.
- The widget-config endpoint already validates `Origin` per-widget against the widget's
  stored `allowed_origins` list; this SPEC keeps that logic and adds a consistent
  non-credentialed CORS policy at the middleware level for the whole `/partner/v1/*`
  surface.
- Partner API key endpoints (`/partner/v1/chat/completions`, `/feedback`, `/knowledge`)
  are designed for server-to-server callers OR widget browsers presenting a short-lived
  JWT in the `Authorization` header. Neither case needs the BFF session cookie.
- No internal Klai service-to-service traffic uses a browser; CORS is irrelevant on
  Docker `klai-net`. The middleware-order lint (REQ-6) therefore matters for
  defense-in-depth on services that already are browser-exposed (portal-api, connector
  via portal proxy) AND as a forward-compatible stance for services that MIGHT be
  exposed later (retrieval-api).
- The klai-connector public surface today is only reachable from the portal SPA via
  portal-api's BFF proxy (same-origin), not directly from a browser. Even so, the
  middleware-order bug makes direct browser debugging sessions and future exposure
  both harder to reason about; Finding III is upgraded to HIGH on the strength of
  "this is the exact failure class that the audit mis-attributed to portal-api, and
  we would repeat the audit finding at the next pen-test if we don't fix it now."

---

## Risks

- **Breaking a legitimate origin we did not enumerate.** Mitigation: run the Caddy
  access-log audit in research.md before narrowing, roll out with a monitoring window,
  and keep one fallback regex env var (`CORS_EXTRA_ALLOWED_ORIGINS`) for the rollout
  week — removed after 7 days of zero unmatched legitimate preflights.
- **Widget origin desynchronisation.** The partner widget endpoint already owns its own
  origin allowlist in `widgets.widget_config.allowed_origins`; the new `/partner/v1/*`
  middleware CORS policy must not collide with this. Resolution: keep the per-widget
  check in the handler AND set middleware CORS to echo the request `Origin` only when
  it appears in the widget's allowlist (never the global first-party list, never `*`).
- **Browser caches a permissive preflight.** If the old `r".*"` preflight has been
  cached with a long `Access-Control-Max-Age`, browsers may keep honoring it after
  deployment. Mitigation: today's config sets no `Max-Age` header, so browsers use the
  default 5-second cache window on most engines; no explicit invalidation needed but
  add a note to the runbook.
- **CSRF-exempt inline comments drift.** Once each prefix has a rationale comment, a
  future contributor could add a new prefix without the same rigor. Mitigation: a
  lint test (see REQ-4) asserts every entry in `_CSRF_EXEMPT_PREFIXES` has a preceding
  comment line AND a matching acceptance test in the repo.
- **Middleware-order regression (new in v0.3.0).** A future contributor copies the
  connector pattern into a new service, or reverts the fix during a refactor, and
  re-introduces the "401 bypasses CORS" bug. Mitigation: REQ-6 ast-grep / CI lint rule
  mechanically rejects any `add_middleware(CORSMiddleware, ...)` call that is not the
  last `add_middleware(...)` call in the file. Runs on every PR that touches a
  `main.py` in the listed services.
- **Retrieval-api deny-by-default breaks a forgotten consumer.** If some legacy
  browser-level integration is currently calling retrieval-api directly without
  anyone noticing, REQ-7 would break it. Mitigation: retrieval-api is on Docker
  `klai-net` only (no Caddy route today), so no browser can reach it. The deny-
  by-default policy is therefore a forward-only safety measure.

---

## Requirements

### REQ-1: First-party credentialed CORS allowlist

The system SHALL allow credentialed cross-origin requests only from an explicit list of
first-party Klai origins. Wildcard regex allowlisting SHALL be removed for credentialed
traffic.

- **REQ-1.1:** WHEN a browser issues a CORS preflight or actual request to `/api/*`,
  `/internal/*` (internal is not browser-reachable but the policy must apply uniformly),
  `/app/*`, or any route other than `/partner/v1/*` AND the `Origin` header is present,
  THE service SHALL only echo `Access-Control-Allow-Origin` when the origin matches
  the compiled allowlist.
- **REQ-1.2:** The allowlist SHALL be the union of:
  - `settings.cors_origins_list` (comma-split, already supported) — used for local dev
    and static production origins such as `https://my.getklai.com`.
  - A compiled regex bound to the Klai domain: `^https://([a-z0-9][a-z0-9-]*\.)?getklai\.com$`
    which matches `https://getklai.com`, `https://my.getklai.com`, and any single-label
    tenant subdomain (`https://acme.getklai.com`). Multi-label subdomains
    (`https://evil.my.getklai.com`) SHALL NOT match.
- **REQ-1.3:** WHEN the `Origin` header is missing (same-origin request OR server-to-
  server), THE service SHALL process the request normally. CORS policy only applies to
  cross-origin requests.
- **REQ-1.4:** WHEN an origin does not match the allowlist AND the request is a CORS
  preflight (`OPTIONS` with `Access-Control-Request-Method`), THE service SHALL respond
  with a 200 OK that omits `Access-Control-Allow-Origin`. This causes the browser to
  refuse the preflight and block the subsequent request client-side.
- **REQ-1.5:** `Access-Control-Allow-Credentials: true` SHALL only be set on responses
  for allowlisted first-party origins. It SHALL NEVER be set alongside a wildcard
  origin.
- **REQ-1.6:** `settings.cors_allow_origin_regex` SHALL be removed as a user-tunable
  knob. The value used at runtime SHALL be the fixed pattern defined in REQ-1.2, with
  no env override. Ad-hoc additions go through `settings.cors_origins` (explicit list).

### REQ-2: Separate non-credentialed CORS policy for `/partner/v1/*`

The system SHALL serve `/partner/v1/*` under a CORS policy that never sets
`Access-Control-Allow-Credentials: true` and defers origin validation to the per-widget
allowlist in `widgets.widget_config.allowed_origins`.

- **REQ-2.1:** WHEN a browser issues a CORS preflight to `/partner/v1/*`, THE service
  SHALL echo `Access-Control-Allow-Origin: <request Origin>` only when the origin is
  present in the `allowed_origins` list of the widget identified by the `id` query
  parameter (for `/partner/v1/widget-config`) OR in the `allowed_origins` of the widget
  bound to the presented `session_token` (for `/partner/v1/chat/completions`,
  `/feedback`, `/knowledge`). Otherwise THE response SHALL omit `Access-Control-Allow-Origin`.
- **REQ-2.2:** Responses on `/partner/v1/*` SHALL NEVER include
  `Access-Control-Allow-Credentials: true`. Widget browser clients SHALL use
  `fetch(..., { credentials: 'omit' })`.
- **REQ-2.3:** The middleware SHALL add `Vary: Origin` to every `/partner/v1/*`
  response so intermediaries (Caddy, browser cache, CDN) key the cache on Origin.
- **REQ-2.4:** The existing per-widget origin validation in `/partner/v1/widget-config`
  and the session-token origin binding in `/partner/v1/chat/completions` SHALL remain
  in place as the authoritative security check; the CORS middleware behavior is a
  browser-side defense-in-depth layer.

### REQ-3: No credentials for widget traffic

The system SHALL ensure the BFF session cookie CANNOT be exercised from any widget
origin, even if a widget script colludes with the same-origin victim.

- **REQ-3.1:** `/partner/v1/*` endpoints SHALL NOT read `request.state.session`. They
  already authenticate via partner API keys or widget session tokens, never the BFF
  cookie. A regression test SHALL assert that `/partner/v1/chat/completions` returns
  401/403 when called with ONLY a valid BFF cookie and no `Authorization` header.
- **REQ-3.2:** WHEN a widget preflight arrives carrying `Access-Control-Request-Headers`
  that includes `cookie`, THE CORS middleware SHALL NOT advertise `cookie` in
  `Access-Control-Allow-Headers`. Browsers will therefore refuse to forward cookies on
  the subsequent request.
- **REQ-3.3:** The widget JavaScript embedded by partners SHALL be documented to use
  `credentials: 'omit'` (runbook note). This is not enforceable server-side but is a
  precondition for correct operation under REQ-2.2.

### REQ-4: Inline rationale for every CSRF-exempt prefix

Every entry in `_CSRF_EXEMPT_PREFIXES` SHALL carry an inline comment explaining why
CSRF cannot apply, AND THE CORS-audit layer defined by REQ-1/REQ-2 SHALL prevent
cross-origin credentialed probing of that prefix.

- **REQ-4.1:** Every prefix string in `_CSRF_EXEMPT_PREFIXES` SHALL be preceded by a
  comment block that states: (a) which flow uses the prefix, (b) why CSRF double-submit
  is impossible or irrelevant there (e.g. pre-session OIDC flow, sendBeacon cannot set
  headers, server-to-server internal secret), and (c) the acceptance-test ID that
  proves cross-origin credentialed probing is blocked.
- **REQ-4.2:** A new unit test `test_csrf_exempt_prefixes_have_rationale` SHALL parse
  `session.py` and fail if any entry in `_CSRF_EXEMPT_PREFIXES` lacks a preceding
  comment line within 5 lines above it.
- **REQ-4.3:** Prefixes currently exempt because they pre-date the BFF session
  (`/api/auth/login`, `/api/auth/totp-login`, `/api/auth/sso-complete`, `/api/signup`)
  SHALL remain exempt — but the rationale comment SHALL reference REQ-1 as the
  browser-side defense-in-depth that makes the exemption safe.
- **REQ-4.4:** IF a prefix can be narrowed to specific HTTP methods without breaking
  consumers (for example `/api/health` is GET-only by nature), THEN the exemption
  SHALL be expressed as the pair `(prefix, method_set)` rather than a bare prefix,
  and the rationale comment SHALL document the narrowing.
- **REQ-4.5:** `/internal/`, `/partner/`, and `/widget/` prefixes SHALL keep their
  exemption AND the rationale comment SHALL explicitly state that these paths do not
  use the BFF cookie — they have their own auth header (`X-Internal-Secret`, partner
  Bearer key, widget session token). The comment SHALL link back to REQ-3.1.

### REQ-5: Regression tests for cross-origin credentialed probing

The system SHALL ship with regression tests that would have caught the original finding
(wildcard CORS + CSRF-exempt login) before it was introduced.

- **REQ-5.1:** Test `test_cors_blocks_evil_origin_on_api_me` SHALL simulate a browser
  sending `GET /api/me` with `Origin: https://evil.example` and a valid BFF session
  cookie. The response SHALL NOT include `Access-Control-Allow-Origin: https://evil.example`
  and SHALL NOT include `Access-Control-Allow-Credentials: true`. The test SHALL fail
  if either header is echoed.
- **REQ-5.2:** Test `test_cors_blocks_evil_origin_on_auth_login` SHALL simulate a browser
  sending `POST /api/auth/login` with `Origin: https://evil.example`. The server-side
  response is permitted (the exemption pre-dates the session), but the CORS preflight
  for that POST from `evil.example` SHALL fail — the test asserts the preflight
  `OPTIONS` response omits `Access-Control-Allow-Origin` for `evil.example`.
- **REQ-5.3:** Test `test_cors_allows_first_party_on_api_me` SHALL simulate
  `GET /api/me` with `Origin: https://my.getklai.com` and assert the response echoes
  that origin AND `Access-Control-Allow-Credentials: true`.
- **REQ-5.4:** Test `test_cors_allows_tenant_subdomain_on_api_me` SHALL simulate
  `GET /api/me` with `Origin: https://acme.getklai.com` and assert the response echoes
  that origin. A second case SHALL use `Origin: https://evil.my.getklai.com`
  (multi-label) and assert the origin is NOT echoed.
- **REQ-5.5:** Test `test_partner_cors_allows_widget_origin_without_credentials` SHALL
  create a widget with `allowed_origins = ["https://customer.example"]` and assert
  `GET /partner/v1/widget-config?id=<widget_id>` from `Origin: https://customer.example`
  echoes the origin in `Access-Control-Allow-Origin` AND does NOT include
  `Access-Control-Allow-Credentials: true`.
- **REQ-5.6:** Test `test_partner_cors_blocks_unlisted_origin` SHALL assert the same
  widget endpoint returns 403 when the `Origin` is not in the widget's `allowed_origins`.
- **REQ-5.7:** Test `test_bff_cookie_rejected_on_partner_endpoint` SHALL call
  `POST /partner/v1/chat/completions` with ONLY a valid BFF cookie and no partner
  Bearer token. The response SHALL be 401 (partner auth dependency rejects missing
  Bearer).

### REQ-6: Repo-wide middleware-order enforcement

Every klai FastAPI service SHALL register `CORSMiddleware` LAST (= outermost execution
per Starlette's reverse-registration rule) so that responses emitted by auth
middleware, request-context middleware, and any other inner layer carry CORS headers
and are visible to cross-origin browsers.

- **REQ-6.1:** WHEN a klai FastAPI service registers `CORSMiddleware`, THE
  `add_middleware(CORSMiddleware, ...)` call SHALL be the LAST `add_middleware(...)`
  call in the module for the corresponding `FastAPI` app. Function-decorator-style
  middleware (`@app.middleware("http")`) does NOT count as `add_middleware` for the
  purpose of this rule but SHALL still be registered ABOVE (earlier in source) the
  `CORSMiddleware` call so the execution order places CORS outermost.
- **REQ-6.2:** An ast-grep pattern (`.claude/lint/cors_middleware_last.yml` or
  equivalent) SHALL match any file in the listed services where an
  `app.add_middleware(CORSMiddleware, ...)` call is followed — in source order — by
  another `app.add_middleware(...)` call. The pattern SHALL fail CI when it matches.
- **REQ-6.3:** THE lint SHALL run on every pull request that modifies a `main.py` (or
  equivalent FastAPI entry module) in the following services:
  `klai-portal`, `klai-connector`, `klai-retrieval-api`, `klai-scribe`,
  `klai-knowledge-ingest`, `klai-mailer`, `klai-knowledge-mcp`,
  `klai-focus/research-api`.
- **REQ-6.4:** klai-connector `app/main.py` SHALL be reordered from today's
  (CORS → Auth → RequestContext) registration sequence into
  (Auth → RequestContext → CORS) so that CORS becomes the outermost wrapper and the
  `JSONResponse(..., status_code=401)` emitted by `AuthMiddleware` on missing/invalid
  Authorization headers passes through CORSMiddleware on the way out.
- **REQ-6.5:** The rationale for REQ-6 SHALL be cross-linked from
  `.claude/rules/klai/lang/python.md` → "Starlette middleware registration order"
  (the rule already exists; add a one-line pointer to SPEC-SEC-CORS-001 REQ-6 and a
  note that the CI lint enforces it).
- **REQ-6.6:** A positive acceptance test SHALL assert that klai-connector's 401
  response (no Authorization header on an authenticated route) carries
  `Access-Control-Allow-Origin: <request-origin>` when the request Origin is in the
  connector's `cors_origins` allowlist.
- **REQ-6.7:** klai-portal `klai-portal/backend/app/main.py` SHALL be reordered so
  that `app.add_middleware(CORSMiddleware, ...)` is the LAST `add_middleware(...)`
  call in `create_app`. Today (verified during Phase 1, 2026-04-25) it is registered
  at line ~180, with `LoggingContextMiddleware` (line ~199) and `SessionMiddleware`
  (line ~203) added AFTER it in source order. Per Starlette LIFO, this places
  SessionMiddleware OUTSIDE CORSMiddleware, so a 401 emitted by SessionMiddleware
  (e.g. CSRF reject at session.py:75-77) bypasses CORS and a cross-origin browser
  sees an opaque failure. The fix preserves the desired execution order
  (SessionMiddleware -> LoggingContextMiddleware -> @http no_cache decorator ->
  CORS -> route) becomes (CORS -> LoggingContextMiddleware -> @http no_cache ->
  SessionMiddleware -> route). The reorder converts a pre-existing latent bug
  (currently masked by the wildcard regex r".*") into a properly bounded CORS
  policy. Acceptance: a positive test SHALL assert that a CSRF-rejected POST
  (e.g. cross-origin POST to a non-exempt path with mismatched X-CSRF-Token)
  carries `Access-Control-Allow-Origin` for an allowlisted first-party origin.
  Lint REQ-6.2 SHALL apply uniformly to klai-portal/backend/app/main.py.

### REQ-7: klai-retrieval-api CORS deny-by-default starter

klai-retrieval-api SHALL register `CORSMiddleware` with an empty allowlist as a
defense-in-depth deny-by-default starter so that any future Caddy exposure does not
silently inherit an implicit no-policy stance.

- **REQ-7.1:** `klai-retrieval-api/retrieval_api/main.py` SHALL call
  `app.add_middleware(CORSMiddleware, ...)` as the LAST `add_middleware` invocation
  (REQ-6 applies uniformly). Registration order SHALL become:
  (AuthMiddleware → RequestContextMiddleware → CORSMiddleware).
- **REQ-7.2:** The starter `allow_origins` list SHALL be empty (`[]`), the
  `allow_origin_regex` SHALL be `None`, `allow_credentials` SHALL be `False`, and
  `allow_methods` / `allow_headers` SHALL be empty lists. WHEN a cross-origin request
  arrives, THE response SHALL NOT echo `Access-Control-Allow-Origin`. This is the
  deny-by-default stance.
- **REQ-7.3:** WHEN a future SPEC introduces a Caddy route for retrieval-api (direct
  browser exposure), THE owning SPEC SHALL explicitly update retrieval-api's CORS
  allowlist in the same change — the deny-by-default posture SHALL NOT be silently
  loosened.
- **REQ-7.4:** A regression test SHALL assert that
  `OPTIONS /retrieve` from `Origin: https://my.getklai.com` (a currently-valid Klai
  origin) receives a response WITHOUT `Access-Control-Allow-Origin`, confirming that
  the retrieval-api CORS policy is deny-by-default until explicitly loosened.

---

## Non-Functional Requirements

- **Performance:** The origin-match regex compiles once at module load. Per-request
  cost SHALL be O(1) regex match against a bounded pattern. No additional DB lookup
  per request for first-party origins. Widget origin lookup continues to happen once
  per `/partner/v1/widget-config` call, same as today. The ast-grep lint runs only
  in CI on changed files; no runtime impact.
- **Observability:** Every rejected preflight SHALL emit a structlog entry at `info`
  level with `event="cors_origin_rejected"`, the rejected origin (string-sanitised),
  the request path, and the `request_id`. VictoriaLogs query
  `event:"cors_origin_rejected"` SHALL return these events for the 7-day monitoring
  window (see Success Criteria).
- **Backward compatibility:** All first-party portal traffic SHALL continue to work
  without client-side changes. Widget partners SHALL NOT need to change their embed
  code provided they already use `credentials: 'omit'`; a runbook note SHALL be added
  to the widget integration docs. Reordering middleware in klai-connector does NOT
  change the response shape for same-origin callers (portal-api BFF proxy).
- **Fail mode:** IF the origin-match regex fails to compile at startup (programmer
  error), THE portal-api process SHALL refuse to start with a clear log line —
  fail-closed, matching the pattern used by `_require_vexa_webhook_secret`.

---

## Success Criteria

- `cors_allow_origin_regex = r".*"` is removed from `config.py`. The runtime CORS
  origin check is the fixed regex in REQ-1.2 combined with `cors_origins_list`.
- A second CORS middleware (or router-level middleware) handles `/partner/v1/*` with
  credentials disabled and origin matching driven by the widget's stored allowlist.
- Every entry in `_CSRF_EXEMPT_PREFIXES` has a preceding rationale comment AND a
  matching acceptance test referenced in the comment.
- All regression tests in REQ-5, AC-15, AC-16, AC-17, AC-18 pass.
- klai-connector `app/main.py` registers CORSMiddleware LAST and a 401 from
  AuthMiddleware carries `Access-Control-Allow-Origin` on allowed origins.
- klai-retrieval-api `retrieval_api/main.py` registers CORSMiddleware (deny-by-default)
  as the LAST `add_middleware` call.
- The ast-grep / CI lint for REQ-6 catches a synthetic regression in a feature-branch
  test (a commit that re-orders CORSMiddleware to the middle) before merge.
- Deploy-week monitoring: VictoriaLogs query
  `event:"cors_origin_rejected" AND NOT origin:/.*getklai\.com/` returns zero hits
  after the first 24 hours of traffic; confirm legitimate origins did not get trapped
  by the new allowlist. After 7 consecutive zero-hit days, the monitoring alert is
  demoted to dashboard-only.
- Cross-origin POST to `/api/auth/login` from `evil.example` is blocked by the browser
  (no `Access-Control-Allow-Origin` echoed on preflight).
- Cross-origin GET to `/api/me` from `evil.example` with a valid cookie is blocked by
  the browser (no credentialed response).

---

## Out of scope

- Migrating portal-api cookies to `SameSite=Strict`. That is a separate SPEC with
  broader compatibility impact (OAuth callback flows, cross-subdomain SSO).
- Per-widget CORS signing or CSRF tokens. Current partner auth (Bearer key, widget
  JWT) is sufficient; CSRF is a cookie-based threat.
- Partner API rate limiting changes. Covered elsewhere (`partner_dependencies.py`).
- Changes to Caddy-level CORS (Caddy does not add CORS headers today; portal-api is
  the single source of truth).
- mTLS or origin-pinning for internal Klai-to-Klai traffic. Out of browser scope.
- Expanding retrieval-api's allowlist to actually permit browser traffic — REQ-7 is
  deliberately a deny-by-default starter; any positive allowlist is a future SPEC
  that owns the exposure decision.

---

## Cross-references

- Tracker: `.moai/specs/SPEC-SEC-AUDIT-2026-04/spec.md`
- Research: `.moai/specs/SPEC-SEC-CORS-001/research.md`
- Acceptance: `.moai/specs/SPEC-SEC-CORS-001/acceptance.md`
- Middleware order rule: `.claude/rules/klai/lang/python.md` (Starlette middleware
  registration order, HIGH)
- Portal security patterns: `.claude/rules/klai/projects/portal-security.md`
- Observability runbook: `.claude/rules/klai/infra/observability.md`
- Reference (correct order): `klai-scribe/scribe-api/app/main.py`
