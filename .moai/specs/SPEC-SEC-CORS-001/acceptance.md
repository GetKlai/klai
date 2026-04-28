# Acceptance Criteria — SPEC-SEC-CORS-001

EARS-format acceptance tests that MUST pass before SPEC-SEC-CORS-001 is
considered complete. Each test is runnable against the portal-api test
harness (pytest + httpx TestClient) OR verifiable by a curl probe against
a deployed portal-api with `Origin:` header overrides.

Test files MUST live at:
- `klai-portal/backend/tests/test_cors_allowlist.py` (AC-1 through AC-8, AC-11)
- `klai-portal/backend/tests/test_partner_cors.py` (AC-9 through AC-10)
- `klai-portal/backend/tests/test_csrf_exempt_rationale.py` (AC-12)
- `klai-connector/tests/test_cors_middleware_order.py` (AC-15)
- `klai-retrieval-api/tests/test_cors_presence.py` (AC-16, AC-17)
- `rules/tests/test_cors_middleware_last_lint.py` (AC-18)

## AC-1: Cross-origin GET /api/me from evil.example is blocked

- **WHEN** a browser sends `GET /api/me` with header `Origin: https://evil.example`
  AND a valid BFF session cookie attached
  **THE** portal-api response **SHALL NOT** include the header
  `Access-Control-Allow-Origin: https://evil.example`
  **AND SHALL NOT** include the header
  `Access-Control-Allow-Credentials: true` for the origin `https://evil.example`.
- **Verification:** Attacker-origin preflight (`OPTIONS /api/me` with
  `Origin: https://evil.example` and
  `Access-Control-Request-Method: GET`) returns 200 with no `Access-Control-Allow-Origin`
  header set, OR returns a non-2xx. The browser refuses to issue the actual GET.
- **Code under test:** `CORSMiddleware` configured per REQ-1.

## AC-2: Cross-origin POST /api/auth/login preflight from evil.example is blocked

- **WHEN** a browser sends a CORS preflight `OPTIONS /api/auth/login` with
  `Origin: https://evil.example` and `Access-Control-Request-Method: POST`
  **THE** response **SHALL NOT** echo `Access-Control-Allow-Origin: https://evil.example`.
- **Note:** Server-side `POST /api/auth/login` may still return 401/422 on its
  own — that is the existing Zitadel Login V2 finisher path. The acceptance
  criterion is that the BROWSER-level CORS check fails first, so a victim's
  cookies are never attached to the actual request from `evil.example`.
- **Code under test:** `CORSMiddleware` per REQ-1.1 + CSRF exemption rationale
  per REQ-4.3 (explicit linkage).

## AC-3: First-party GET /api/me from my.getklai.com is allowed with credentials

- **WHEN** a browser sends `GET /api/me` with `Origin: https://my.getklai.com`
  and a valid BFF session cookie
  **THE** portal-api response **SHALL** include:
  - `Access-Control-Allow-Origin: https://my.getklai.com` (exact match, not `*`)
  - `Access-Control-Allow-Credentials: true`
  - `Vary: Origin`
  AND the response body **SHALL** be the authenticated user representation
  (normal 200 response).
- **Code under test:** Allowlist regex in REQ-1.2.

## AC-4: First-party tenant subdomain GET /api/me is allowed

- **WHEN** a browser sends `GET /api/me` with `Origin: https://acme.getklai.com`
  **THE** response **SHALL** include `Access-Control-Allow-Origin: https://acme.getklai.com`.
- **AND WHEN** a browser sends `GET /api/me` with
  `Origin: https://evil.my.getklai.com` (multi-label subdomain)
  **THE** response **SHALL NOT** include `Access-Control-Allow-Origin`.
- **Code under test:** The single-label subdomain constraint in the REQ-1.2
  regex `^https://([a-z0-9][a-z0-9-]*\.)?getklai\.com$`.

## AC-5: Plaintext http://getklai.com is rejected

- **WHEN** a browser sends `GET /api/me` with `Origin: http://getklai.com`
  (note: `http`, not `https`)
  **THE** response **SHALL NOT** include `Access-Control-Allow-Origin`.
- **Code under test:** The `https://` prefix requirement in REQ-1.2.

## AC-6: Dev origin `http://localhost:5174` is allowed when configured

- **GIVEN** `cors_origins="http://localhost:5174"` (default)
- **WHEN** a browser sends `GET /api/me` with `Origin: http://localhost:5174`
  **THE** response **SHALL** include `Access-Control-Allow-Origin: http://localhost:5174`
  AND `Access-Control-Allow-Credentials: true`.
- **Code under test:** `cors_origins_list` union with the regex per REQ-1.2.

## AC-7: Preflight response never echoes an unlisted origin

- **WHEN** a browser sends a CORS preflight to ANY path
  (`/api/me`, `/api/auth/login`, `/api/signup`, `/internal/anything`)
  with `Origin: https://evil.example`
  **THE** response **SHALL NOT** include `Access-Control-Allow-Origin: https://evil.example`
  **AND SHALL NOT** include `Access-Control-Allow-Origin: *`.
- **Verification:** Programmatic table test that iterates the representative
  paths × a list of attacker origins (`evil.example`, `evil.getklai.com.attacker.tld`,
  `http://getklai.com`, `https://evil.my.getklai.com`) and asserts no allow-origin
  echo in any cell.
- **Code under test:** Global CORS policy per REQ-1.

## AC-8: `Access-Control-Allow-Credentials: true` never coexists with wildcard origin

- **WHEN** any response from `/api/*` includes `Access-Control-Allow-Credentials: true`
  **THE** response **SHALL** also include a concrete origin in
  `Access-Control-Allow-Origin` (not `*`, not missing).
- **Verification:** The response-header-scanner test walks every response from the
  other AC tests and asserts this invariant holds.
- **Code under test:** REQ-1.5.

## AC-9: Widget POST /partner/v1/chat/completions without cookies is allowed

- **GIVEN** a widget created with `allowed_origins = ["https://customer.example"]`
  AND a valid widget session_token for that widget
- **WHEN** a browser sends `POST /partner/v1/chat/completions` with:
  - `Origin: https://customer.example`
  - `Authorization: Bearer <session_token>`
  - `credentials: 'omit'` (NO cookies)
  **THE** response **SHALL** include:
  - `Access-Control-Allow-Origin: https://customer.example` (exact match)
  - **NO** `Access-Control-Allow-Credentials: true` header
  - `Vary: Origin`
  AND the response body **SHALL** be the normal chat-completions payload.
- **Code under test:** Partner CORS policy per REQ-2.

## AC-10: Widget endpoint rejects unlisted origin

- **GIVEN** a widget created with `allowed_origins = ["https://customer.example"]`
- **WHEN** a browser sends `GET /partner/v1/widget-config?id=<widget_id>` with
  `Origin: https://evil.example`
  **THE** response **SHALL** return HTTP 403 with body containing
  `"Origin not allowed"`
  AND **SHALL NOT** echo `Access-Control-Allow-Origin: https://evil.example`.
- **Code under test:** `origin_allowed()` in the handler (pre-existing) AND
  REQ-2.1 at the middleware level.

## AC-11: BFF session cookie is rejected on partner endpoint

- **WHEN** a request is sent to `POST /partner/v1/chat/completions` with ONLY
  a valid BFF session cookie AND no `Authorization` header
  **THE** response **SHALL** be HTTP 401 (partner auth dependency rejects
  missing Bearer token)
  AND **SHALL NOT** produce a chat completion.
- **Purpose:** Proves that even if an attacker could abuse cookies cross-origin
  (which REQ-1 blocks), the partner auth layer is the second defense and refuses
  cookie-only requests.
- **Code under test:** `get_partner_key` dependency in `partner_dependencies.py`.

## AC-12: Every `_CSRF_EXEMPT_PREFIXES` entry has inline rationale

- **WHEN** the test parses `klai-portal/backend/app/middleware/session.py`
  and locates the tuple `_CSRF_EXEMPT_PREFIXES`
  **THE** test **SHALL** assert that every string literal in the tuple is
  preceded — within the 5 lines above it in source order — by at least one
  comment line that mentions AT LEAST ONE of the following keywords:
  `pre-session`, `no session`, `sendBeacon`, `internal`, `partner`, `widget`,
  `Zitadel`, `signup`, or `health probe`.
- **AND** the test **SHALL** assert the comment references either REQ-1,
  REQ-2, REQ-3, or a specific acceptance test ID (AC-1 through AC-11).
- **Code under test:** `_CSRF_EXEMPT_PREFIXES` tuple and REQ-4.1 / REQ-4.2.

## AC-13: Observability — rejected preflights are logged

- **WHEN** a preflight from a non-allowlisted origin is received
  **THE** portal-api **SHALL** emit a structlog entry at level `info` with:
  - `event="cors_origin_rejected"`
  - `origin=<sanitised origin string, truncated to 256 chars>`
  - `path=<request path>`
  - `request_id=<X-Request-ID from Caddy>`
- **Verification:** LogsQL query `event:"cors_origin_rejected" AND origin:"https://evil.example"`
  returns exactly one entry per rejected preflight in the test harness's captured logs.
- **Code under test:** Custom CORS middleware hook per REQ-1 Non-Functional.

## AC-14: Startup fail-closed on broken regex

- **WHEN** the compiled origin-match regex fails (programmer error) to compile
  at startup
  **THE** portal-api process **SHALL** log a critical-level message
  `"CORS origin regex failed to compile"` AND **SHALL** exit non-zero BEFORE
  accepting traffic.
- **Verification:** Unit test that monkeypatches the regex source to an invalid
  pattern and asserts startup raises `SystemExit` (matching the
  `_require_vexa_webhook_secret` pattern in `config.py`).
- **Code under test:** Startup hook per Non-Functional "Fail mode".

---

## Internal-wave additions (v0.3.0, 2026-04-24)

The following acceptance criteria cover the cross-service middleware-order
findings (Finding III, Finding IV) and the repo-wide lint gate (REQ-6, REQ-7).

## AC-15: klai-connector 401 response carries CORS headers

- **GIVEN** `cors_origins="http://localhost:5174"` configured in klai-connector
  AND klai-connector's middleware stack reordered per REQ-6.4 so that
  `CORSMiddleware` is the LAST `add_middleware` call (i.e. outermost on the
  response)
- **WHEN** a browser sends `GET /api/v1/connectors` (an authenticated route)
  with `Origin: http://localhost:5174` AND no `Authorization` header
  **THE** klai-connector response **SHALL** be HTTP 401
  **AND SHALL** include the header
  `Access-Control-Allow-Origin: http://localhost:5174`
  **AND SHALL** include `Vary: Origin`.
- **Negative case:** WHEN the same request is sent with `Origin: https://evil.example`
  (not in `cors_origins`) **THE** 401 response **SHALL NOT** include
  `Access-Control-Allow-Origin: https://evil.example`.
- **Purpose:** Proves Finding III is fixed — the `JSONResponse(..., 401)` emitted
  by `AuthMiddleware` passes through `CORSMiddleware` on the way out, so
  cross-origin browsers see a clean 401 with CORS headers instead of an opaque
  network error.
- **Code under test:** `klai-connector/app/main.py:182-193` middleware registration
  order after REQ-6.4 is applied.

## AC-16: klai-retrieval-api has CORSMiddleware registered

- **WHEN** the test imports `retrieval_api.main` AND inspects `app.user_middleware`
  (Starlette's registered-middleware list)
  **THE** test **SHALL** assert that exactly one entry in `app.user_middleware`
  is `CORSMiddleware`
  **AND** the test **SHALL** assert that `CORSMiddleware` is the LAST entry
  added (i.e. appears FIRST in `app.user_middleware` since Starlette stores the
  stack in reverse-registration order).
- **Static-check variant:** A grep-based test SHALL scan
  `klai-retrieval-api/retrieval_api/main.py` for the literal
  `app.add_middleware(CORSMiddleware` and assert:
  1. The string is present exactly once.
  2. No other `app.add_middleware(` call appears AFTER it in source order.
- **Purpose:** Proves Finding IV is fixed — retrieval-api has a CORS policy
  surface (deny-by-default) per REQ-7.
- **Code under test:** `klai-retrieval-api/retrieval_api/main.py` after REQ-7.1.

## AC-17: klai-retrieval-api CORS is deny-by-default

- **GIVEN** klai-retrieval-api with `CORSMiddleware` registered per REQ-7 (empty
  allowlist, `allow_credentials=False`)
- **WHEN** a browser sends `OPTIONS /retrieve` with
  `Origin: https://my.getklai.com` AND `Access-Control-Request-Method: POST`
  **THE** response **SHALL NOT** include `Access-Control-Allow-Origin`
  **AND SHALL NOT** include `Access-Control-Allow-Credentials: true`.
- **AND WHEN** a browser sends the same preflight from any origin
  (`http://localhost:5174`, `https://customer.example`, `https://evil.example`)
  **THE** response **SHALL NOT** echo `Access-Control-Allow-Origin` for any of
  them.
- **Purpose:** Proves REQ-7.2 (empty allowlist) and REQ-7.4 (deny-by-default
  verified by a positive-origin probe).
- **Code under test:** `klai-retrieval-api/retrieval_api/main.py` CORS
  configuration after REQ-7.2.

## AC-18: ast-grep / CI lint catches CORSMiddleware-not-last regressions

- **GIVEN** an ast-grep pattern rule (`rules/cors_middleware_last.yml`,
  matching the existing repo precedent `rules/no-exec-run.yml` discovered via
  repo-root `sgconfig.yml`) that matches any file containing
  `app.add_middleware(CORSMiddleware, ...)` followed — in source order — by
  another `app.add_middleware(...)` call for the same `app` binding
- **WHEN** the CI pipeline runs the lint on a pull request that modifies any
  `main.py` (or equivalent FastAPI entry module) in the services listed in
  REQ-6.3
  **THE** lint **SHALL** exit non-zero AND report the offending file and line
  when a regression is introduced.
- **Synthetic regression test:** A fixture file in
  `rules/tests/fixtures/bad_middleware_order.py` SHALL register
  CORSMiddleware first and Auth middleware after. A pytest case SHALL invoke
  the lint on this fixture and assert a non-zero exit code AND an error message
  naming the fixture file.
- **Positive test:** A fixture file in
  `rules/tests/fixtures/good_middleware_order.py` SHALL register Auth
  first and CORSMiddleware last. A pytest case SHALL invoke the lint on this
  fixture and assert exit code 0.
- **CI wiring:** The lint SHALL be wired into the repo's CI workflow
  (.github/workflows or equivalent) for each listed service so that a PR
  touching that service's entry module runs the lint automatically. The wiring
  itself SHALL be asserted by a test that reads the workflow file and verifies
  the presence of the lint step for each service.
- **Purpose:** Mechanically prevents the copy-paste drift that produced Finding
  III. A future contributor cannot re-introduce a CORSMiddleware-first
  arrangement in any listed service without the PR failing CI.
- **Code under test:** REQ-6.2, REQ-6.3; the lint rule file itself.

---

## Test matrix summary

| AC | Path | Origin | Method | Credentials | Expected |
|---|---|---|---|---|---|
| AC-1 | /api/me | evil.example | GET | cookie attached | no ACAO echo |
| AC-2 | /api/auth/login | evil.example | OPTIONS preflight for POST | n/a | no ACAO echo |
| AC-3 | /api/me | my.getklai.com | GET | cookie attached | ACAO echoed + ACAC=true |
| AC-4 | /api/me | acme.getklai.com / evil.my.getklai.com | GET | cookie | echo / no-echo |
| AC-5 | /api/me | http://getklai.com | GET | cookie | no ACAO echo |
| AC-6 | /api/me | http://localhost:5174 | GET | cookie | ACAO echoed + ACAC=true |
| AC-7 | * | multiple attacker origins | OPTIONS | n/a | no ACAO echo anywhere |
| AC-8 | /api/* | any | any | credentials=true | origin is concrete, not `*` |
| AC-9 | /partner/v1/chat/completions | customer.example | POST | omit + Bearer | ACAO echoed, NO ACAC |
| AC-10 | /partner/v1/widget-config | evil.example | GET | n/a | 403 + no ACAO |
| AC-11 | /partner/v1/chat/completions | same-origin | POST | cookie only, no Bearer | 401 |
| AC-12 | static source | n/a | n/a | n/a | every prefix has rationale |
| AC-13 | log stream | any rejected | OPTIONS | n/a | structlog event emitted |
| AC-14 | startup | n/a | n/a | n/a | fail-closed on bad regex |
| AC-15 | connector /api/v1/connectors | localhost:5174 / evil.example | GET | no Authorization | 401 carries ACAO for allowed origin, not for evil |
| AC-16 | retrieval-api static import | n/a | n/a | n/a | CORSMiddleware present, last added |
| AC-17 | retrieval-api /retrieve | my.getklai.com (and others) | OPTIONS | n/a | no ACAO echo for any origin (deny-by-default) |
| AC-18 | lint fixtures + CI | n/a | n/a | n/a | lint fails on bad, passes on good, wired in CI |

`ACAO` = `Access-Control-Allow-Origin`.
`ACAC` = `Access-Control-Allow-Credentials`.

All AC tests MUST be runnable as `uv run pytest klai-portal/backend/tests/test_cors_allowlist.py
klai-portal/backend/tests/test_partner_cors.py
klai-portal/backend/tests/test_csrf_exempt_rationale.py
klai-connector/tests/test_cors_middleware_order.py
klai-retrieval-api/tests/test_cors_presence.py
rules/tests/test_cors_middleware_last_lint.py` and pass in CI before
this SPEC can be marked `status: done`.
