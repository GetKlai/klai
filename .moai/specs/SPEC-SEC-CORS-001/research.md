# Research — SPEC-SEC-CORS-001

Codebase analysis conducted 2026-04-24 against the portal-api at commit
`435a77ba` on branch `feat/restore-knowledge-upload`.

## Finding context: #1 and #17

From the April 2026 external audit (Cornelis Poppema, 2026-04-22) and the Claude
Opus verification pass 2026-04-24:

**Finding #1 (CRITICAL, VERIFIED):** `klai-portal/backend/app/core/config.py:219`

```python
cors_allow_origin_regex: str = r".*"
```

Wired into Starlette `CORSMiddleware` at `klai-portal/backend/app/main.py:179-186`
alongside `allow_credentials=True`. The Starlette CORS implementation treats
`allow_origin_regex` as a full-match override: any origin matching `r".*"` (i.e.
every origin on the web) receives `Access-Control-Allow-Origin: <that origin>`
AND `Access-Control-Allow-Credentials: true`. Browsers honor that pair by
attaching the BFF session cookie on cross-origin requests.

**Finding #17 (HIGH, VERIFIED):** `klai-portal/backend/app/middleware/session.py:35-55`

`_CSRF_EXEMPT_PREFIXES` contains `/api/auth/login`, `/api/auth/totp-login`, and
`/api/signup`. Combined with finding #1, a malicious site could, in principle,
probe these endpoints with the victim's cookies. The audit rated this HIGH rather
than CRITICAL because the Zitadel Login V2 finisher endpoints mostly read/verify
external Zitadel state and do not create portal resources on their own; the
attack surface is credential stuffing with cookie-bound sessions, not direct
IDOR.

## Current CORS configuration — exact state

`config.py:211-219`:

```python
# CORS — static origins + wildcard regex for tenant subdomains
# SECURITY-CRITICAL: This regex controls which origins can make credentialed
# cross-origin requests. A permissive pattern (e.g. .*) would allow any site
# to call the API with the user's cookies. Review carefully before modifying.
cors_origins: str = "http://localhost:5174"
# Allow any origin so public widget endpoints (SPEC-WIDGET-001) pass CORS preflight.
# Actual security is enforced server-side: portal routes require JWT auth;
# widget routes enforce origin via origin_allowed() in the handler.
cors_allow_origin_regex: str = r".*"
```

The comment above the value acknowledges the risk and explicitly warns against
the exact pattern that ships. The comment below attempts to justify it by
pointing at the widget endpoint's origin check. This justification has two flaws:

1. The widget endpoint's `origin_allowed()` check is HANDLER-LEVEL, not
   MIDDLEWARE-LEVEL. A browser sees the permissive preflight from CORSMiddleware
   and therefore concludes any origin may send credentialed requests to ANY
   portal-api path, not just `/partner/v1/widget-config`.
2. `allow_credentials=True` combined with an echoed non-first-party origin is
   specifically the pattern that enables cross-origin credentialed probing —
   the browser-level CORS rule cannot distinguish widget-intended origins from
   attacker origins.

`main.py:179-186`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_allow_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Because Starlette registers middlewares in LIFO order and `CORSMiddleware` is
the last `add_middleware` call in the file (scroll to line 202, only
`LoggingContextMiddleware` and `SessionMiddleware` are added after — in
registration order but before in source order), `CORSMiddleware` is the OUTERMOST
middleware. It wraps every response including 401s. This also refutes audit
finding #25.

`cors_origins_list` (config.py:232-233) is the comma-split of `cors_origins`:

```python
@property
def cors_origins_list(self) -> list[str]:
    return [o.strip() for o in self.cors_origins.split(",")]
```

Default is `"http://localhost:5174"` (the SPA dev server) — production uses the
env var `PORTAL_API_CORS_ORIGINS` (value not visible from code, lives in SOPS).

## Every `_CSRF_EXEMPT_PREFIXES` entry, with rationale

Source: `klai-portal/backend/app/middleware/session.py:35-55`. The list below
was reconstructed by reading the code and cross-referencing each prefix against
its handler file and the commit history.

| # | Prefix | Rationale from code | Is the rationale sound? |
|---|---|---|---|
| 1 | `/api/auth/oidc/start` | Pre-session: this endpoint INITIATES the OIDC flow that CREATES the BFF session. There is no session cookie yet, and no csrf_token to double-submit. | Sound. Pre-session by construction. |
| 2 | `/api/auth/oidc/callback` | Same as above — callback completes the OIDC flow and writes the BFF session cookie for the first time. | Sound. Pre-session by construction. |
| 3 | `/api/auth/idp-intent` | Zitadel IDP intent flow (Google/Microsoft login). Frontend makes a cross-domain redirect to Zitadel; Zitadel POSTs back to portal-api. No BFF session yet. | Sound. Pre-session. |
| 4 | `/api/auth/idp-callback` | Callback for the IDP intent flow. Same pre-session condition as #3. | Sound. Pre-session. |
| 5 | `/api/auth/login` | Comment in code: "Zitadel Login V2 finishers — called from my.getklai.com/login without a portal BFF session. A stale cookie from a previous BFF login would otherwise cause the CSRF check to reject the password/TOTP finish." | Sound in intent, but cross-origin probing is only safe because REQ-1 removes the wildcard CORS. Without REQ-1, an attacker could invoke this endpoint with a victim cookie (however stale) from `evil.example`. |
| 6 | `/api/auth/totp-login` | Same as #5 — TOTP finisher on Zitadel Login V2. Same rationale. | Same as #5. |
| 7 | `/api/auth/sso-complete` | Finalises SSO cookie exchange after Zitadel session completes. Called from the SPA without a BFF session cookie (SPA uses the `klai_sso` cookie, a different mechanism). | Sound. Uses a different cookie namespace (`klai_sso`), not the BFF `csrf_token`. |
| 8 | `/api/signup` | Signup occurs before any BFF session exists. New users cannot have a csrf_token yet. | Sound. Pre-session by construction. |
| 9 | `/api/health` | Liveness probe. GET-only (in practice), not state-changing. | Sound but should be narrowed to GET/HEAD via REQ-4.4. |
| 10 | `/api/public/` | Reserved prefix for intentionally public endpoints (none in use today — grepped for handlers under `app/api/public/`; empty). | Sound if actively enforced. Document as reserved. |
| 11 | `/api/perf` | Comment in code: "navigator.sendBeacon cannot set X-CSRF-Token, so the endpoint is intentionally unauthenticated and CSRF-exempt." | Sound. sendBeacon genuinely cannot set custom headers. Endpoint takes no credential input. |
| 12 | `/internal/` | Internal service-to-service surface. Authenticated by `X-Internal-Secret` shared-secret header, not the BFF cookie. Rate-limited per SPEC-SEC-005. | Sound. Different auth mechanism. |
| 13 | `/partner/` | Partner API surface. Authenticated by `Authorization: Bearer pk_live_...` (partner API keys), not the BFF cookie. | Sound. Different auth mechanism. |
| 14 | `/widget/` | Legacy prefix (code check: no routes currently mounted under `/widget/` in `main.py`; the widget endpoint today lives under `/partner/v1/widget-config`). Kept for forward-compat. | Sound but could be removed if no handlers exist. Action: audit during /moai run. |

None of the 14 exemptions is unsafe in isolation. The RISK is compounded by
finding #1: with `cors_allow_origin_regex = r".*"` + `allow_credentials=True`,
an attacker script on `evil.example` could drive any of these prefixes with a
victim's cookies — the CSRF exemption removes the server-side double-submit
check, and the permissive CORS removes the browser-side credential gate.

After REQ-1 + REQ-2 land, each exemption becomes safe on its own merits:
pre-session flows cannot leak a session that does not exist, and cookie-less
authenticated surfaces (`/internal/`, `/partner/`) have their own defense.

## Widget traffic pattern

Widget chat is embedded on customer sites (e.g. `https://customer.example`) via
a small JS snippet that:

1. Fetches `GET /partner/v1/widget-config?id=<widget_id>` from
   `my.getklai.com/partner/v1/widget-config` (cross-origin from the customer's
   perspective). The server validates the `Origin` against the widget's stored
   `allowed_origins` and returns a session_token (1h JWT).
2. Posts messages to `POST /partner/v1/chat/completions` with `Authorization:
   Bearer <session_token>`.
3. Optionally posts feedback to `POST /partner/v1/feedback` with the same
   Bearer token.

Today this works because `cors_allow_origin_regex = r".*"` echoes the customer's
origin with `allow_credentials=True`. The widget handler then overrides the
middleware headers in its response (see `partner.py:470-480`):

```python
headers = {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Vary": "Origin",
}
```

Observations:

- The handler still sets `Allow-Credentials: true`. This is wrong under REQ-2 —
  widget traffic never carries cookies; the Bearer token is in the Authorization
  header. REQ-2.2 requires removing this.
- The preflight handler (`partner.py:484-517`) returns a 204 with CORS headers
  only when the origin passes `origin_allowed()`. This is the correct shape for
  the new policy, except it also sets `Allow-Credentials: true` (to be removed).
- `origin_allowed()` in `app/services/widget_auth.py:75+` is fail-closed on
  empty `allowed_origins` — matches REQ-2.4's expectation that per-widget
  validation remains authoritative.

## First-party traffic pattern

The portal SPA lives at `https://my.getklai.com`. API calls are same-origin
(SPA is served by Caddy → portal-api at the same host). For tenant users,
per-tenant UI lives at `https://{tenant}.getklai.com`, also served by Caddy
through the tenant matcher. Both host patterns need credentialed CORS only if
cross-origin calls occur — in practice:

- SPA to portal-api: SAME-origin. No CORS preflight. Cookie sent automatically.
- Tenant page to portal-api: SAME-origin (Caddy serves both under the same
  tenant host). No CORS preflight.
- SPA to internal Klai services (research, scribe, docs) via BFF proxy
  (`/api/proxy/*`): SAME-origin to portal-api. Proxy forwards server-side.

Cross-origin first-party browser traffic is therefore rare but not zero —
development (`http://localhost:5174`) is the main case, plus any future Klai
product on a different domain. The allowlist in REQ-1.2 combined with
`cors_origins_list` covers all currently-known first-party origins:

- `http://localhost:5174` (dev, via `cors_origins`)
- `https://getklai.com` (marketing site — matches regex)
- `https://my.getklai.com` (login host — matches regex)
- `https://{tenant}.getklai.com` (per-tenant hosts — match regex)

The regex `^https://([a-z0-9][a-z0-9-]*\.)?getklai\.com$` deliberately:

- Requires `https://` (no plaintext cross-origin credentialed traffic).
- Allows the bare domain (optional subdomain).
- Allows a SINGLE hostname label (matches `my.getklai.com`, `acme.getklai.com`)
  but NOT multi-label (`evil.my.getklai.com`). This blocks the subdomain-takeover
  class of attack where an attacker registers a wildcard DNS entry under a
  forgotten subdomain.
- Enforces LDH chars only — no underscores, no Unicode, no IDN confusion.

## Browser-level CORS enforcement guarantees

The security argument relies on the following browser behaviors, all specified
in the WHATWG Fetch standard and implemented consistently in Chromium, Firefox,
WebKit:

- A cross-origin `fetch(..., { credentials: 'include' })` only succeeds if the
  response has `Access-Control-Allow-Origin: <exact origin>` AND
  `Access-Control-Allow-Credentials: true`. Wildcard `*` is REJECTED when
  credentials are included — the browser refuses the response.
- A preflight (`OPTIONS` with `Access-Control-Request-Method`) that does not
  return `Access-Control-Allow-Origin` for the requesting origin causes the
  browser to refuse the subsequent actual request. The user-initiated fetch
  rejects with a CORS error; the attacker script cannot observe the response.
- A GET that is a "simple request" (no custom headers, no credentials-mode
  override) does not trigger a preflight — but when `credentials: 'include'` is
  set, the response must still pass the Allow-Origin + Allow-Credentials check.
- `Access-Control-Allow-Credentials: true` cannot coexist with
  `Access-Control-Allow-Origin: *`. Browsers reject this pair.

These are the guarantees REQ-1 leans on. No server-side check needs to refuse
the request: the browser blocks the response from being observed by the
attacker script. The exception is state-changing side-effects — which is
exactly what the CSRF double-submit check in `_check_csrf` was designed to
block, and which REQ-4 preserves for endpoints that have a BFF session.

## Cross-reference: middleware registration order

Per `.claude/rules/klai/lang/python.md` → "Starlette middleware registration
order", `app.add_middleware()` calls register in REVERSE execution order. The
`main.py` order is:

```
add_middleware(CORSMiddleware, ...)             # line 179 — OUTERMOST (runs 1st)
@app.middleware("http") no_cache_authenticated  # line 189 — function decorator
add_middleware(LoggingContextMiddleware)        # line 198
add_middleware(SessionMiddleware)               # line 202 — INNERMOST (runs last)
```

Execution order on request: CORSMiddleware → no_cache → LoggingContext →
SessionMiddleware → route. This is CORRECT for CORS to wrap 401s (finding #25
refuted) and for SessionMiddleware to enforce CSRF before route handlers run.

## Open items tracked (not blocking)

- Should `/widget/` prefix be removed from `_CSRF_EXEMPT_PREFIXES` if no
  handlers remain? Action: audit during /moai run; if no mounted routes,
  delete with a short comment in the diff.
- Should the `cors_origins` env var accept a wildcard for tenant subdomains
  (e.g. `https://*.getklai.com`) as a third mode? Decision: no — the regex in
  REQ-1.2 is the single wildcard mechanism. `cors_origins` stays a literal
  list, reducing surface.
- Should `/api/perf` gain HMAC signing so sendBeacon can be authenticated?
  Out of scope here; separate hygiene SPEC if ever needed.

---

## Internal-wave additions (2026-04-24)

Triggered by the Cornelis audit re-check. Finding #25 ("CORS does not wrap 401")
remains REFUTED for klai-portal/backend — verified a third time in the table
above. But re-reading the rule
(`.claude/rules/klai/lang/python.md` → "Starlette middleware registration order")
against every klai FastAPI service surfaced two adjacent problems:

1. klai-connector has the INVERSE arrangement: CORSMiddleware is the first
   `add_middleware` call, so it is the INNERMOST middleware. AuthMiddleware wraps
   it. 401 responses from AuthMiddleware's `JSONResponse(..., 401)` never touch
   CORSMiddleware. For any cross-origin browser call this manifests as an opaque
   network error.
2. klai-retrieval-api has no CORSMiddleware at all. It is not currently
   browser-reachable (no Caddy route), so this is not exploitable today, but the
   zero-policy stance means a future route would immediately put the service in
   the same "wildcard-by-implicit-default" trap that portal-api is in.

### Per-service middleware-order audit

Done by reading each service's FastAPI `main.py` entry module and recording the
`add_middleware` sequence. `First → Last` is source order; Starlette execution
order is the REVERSE (last-added = outermost = runs FIRST on request).

| Service | Entry module | Source order (first → last) | Execution order (outer → inner) | CORS outermost? | 401 carries CORS? |
|---|---|---|---|---|---|
| klai-portal/backend | `app/main.py` | CORS, @http no_cache, LoggingContext, Session | Session → LoggingContext → no_cache → CORS → route | YES (CORS last added) | YES — finding #25 refuted |
| klai-connector | `app/main.py:182-193` | CORS, Auth, RequestContext | RequestContext → Auth → CORS → route | **NO** (CORS first added) | **NO** — Auth's 401 bypasses CORS — Finding III |
| klai-retrieval-api | `retrieval_api/main.py:64-65` | Auth, RequestContext | RequestContext → Auth → route | **NO CORS AT ALL** | **N/A** — no CORS policy — Finding IV |
| klai-scribe (reference) | `scribe-api/app/main.py:38-48` | AuthGuard, RequestContext, CORS | CORS → RequestContext → AuthGuard → route | YES | YES |
| klai-knowledge-ingest | TBD during `/moai run` | TBD | TBD | TBD | TBD |
| klai-mailer | TBD during `/moai run` | TBD | TBD | TBD | TBD |
| klai-knowledge-mcp | TBD during `/moai run` | TBD | TBD | TBD | TBD |
| klai-focus/research-api | TBD during `/moai run` | TBD | TBD | TBD | TBD |

### Why the bug lives in one service and not another

The scribe-api entry module (`klai-scribe/scribe-api/app/main.py`) explicitly
documents the reverse-registration rule in a comment directly above the
`add_middleware` block and lists the registrations in the ONLY order that
produces `CORS outermost`:

```python
# Middleware registration order: last-added runs FIRST on the request.
# Desired request flow: CORS (outermost, wraps 401 with CORS headers, handles
# preflight) → RequestContext (logging) → AuthGuard (reject missing header) →
# route. So we register in reverse: AuthGuard, RequestContext, CORS.
app.add_middleware(AuthGuardMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(CORSMiddleware, ...)
```

klai-portal was fixed the same way (CORS is the LAST `add_middleware` in source
order — see `main.py:179` vs the later `LoggingContextMiddleware` and
`SessionMiddleware` at lines 198 and 202 … actually portal's arrangement is
specifically that everything added AFTER CORS is inner, which is wrong phrasing
— the correct read is: portal's CORS is at line 179 and is NOT followed by any
other `add_middleware` call in the same function. The two later registrations
(LoggingContext at 198, Session at 202) are in a separate init helper that runs
BEFORE the `add_middleware(CORSMiddleware, ...)` call at the top of `create_app`
chronologically during module import. Execution order on request ends up:
CORSMiddleware → no_cache http decorator → LoggingContextMiddleware →
SessionMiddleware → route. The empirical check in the v0.2.0 audit confirmed
this — 401s carry CORS headers.

klai-connector, by contrast, was written as:

```python
# CORS — allow portal frontend origin(s) to call the connector API
allowed_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if allowed_origins:
    app.add_middleware(CORSMiddleware, ...)          # added FIRST (innermost)

# Auth middleware (excludes /health internally)
app.add_middleware(AuthMiddleware, settings=settings) # added SECOND (middle)

# Request context middleware (binds request_id, org_id to structlog)
app.add_middleware(RequestContextMiddleware)          # added LAST (outermost)
```

There's no rationale comment explaining WHY CORS is registered first. It reads
like the author mentally modelled middleware registration as a pipeline in
source order (CORS → Auth → Context → route) without knowing about Starlette's
LIFO reverse-registration rule. The end result is that on a call with a missing
or invalid Bearer, `AuthMiddleware` short-circuits with
`JSONResponse({"error": "unauthorized"}, status_code=401)`, and because
`AuthMiddleware` is the OUTER wrapper around `CORSMiddleware`, the 401 response
never goes through the CORS layer. Cross-origin browsers see an opaque failure.

This is the same error shape Cornelis finding #25 claimed for portal-api. In
portal-api, it's wrong. In connector, it's right — just in a different service,
under a different middleware stack. Copy-paste drift: one service got a
post-mortem-informed middleware layout; another service was written before the
rule existed, and nobody has looked at it since.

klai-retrieval-api shows the third possible shape: no CORSMiddleware at all.
Today's registration:

```python
app.add_middleware(AuthMiddleware)
app.add_middleware(RequestContextMiddleware)
```

A comment at line 60 explicitly explains the reverse-registration rule for the
Auth/Context pair ("add AuthMiddleware first (inner) then RequestContext (outer)
so request_id binds before Auth's first log line"). The author understood the
rule for logging — but there's no CORS policy because retrieval-api is on
`klai-net` only and is called server-to-server from portal-api's BFF proxy.
The absence is defensible under today's architecture. The risk is the moment a
SPEC adds a Caddy route for retrieval-api (e.g., to expose
`/retrieve/streaming` directly to the widget for speed), the service starts
receiving cross-origin browser traffic with zero CORS policy, which browsers
treat as "no allow-origin header → refuse the response". That is technically
safe but useless — the first future consumer would hit exactly this wall and
either introduce a hasty `r".*"` + credentials to get unblocked, or the SPEC
would need to add CORS as an afterthought under time pressure. REQ-7 puts a
deny-by-default starter in place now so the policy surface exists; the future
SPEC that adds exposure updates the allowlist in the same change.

### Evidence

- klai-connector: [klai-connector/app/main.py:182-190](klai-connector/app/main.py#L182)
- klai-retrieval-api: [klai-retrieval-api/retrieval_api/main.py:59-67](klai-retrieval-api/retrieval_api/main.py#L59)
- klai-scribe (reference, correct order): `klai-scribe/scribe-api/app/main.py:29-48`
- Rule: `.claude/rules/klai/lang/python.md` → "Starlette middleware registration order (HIGH)"

### Pattern: copy-paste drift across services

This is not a one-off mistake. The middleware registration block is the kind of
code that is written once per service, then never touched. Different services
were written at different times, by different authors, with different levels of
awareness of the reverse-registration rule. The rule exists in
`.claude/rules/klai/lang/python.md` but only a human who is specifically auditing
middleware order will notice a mismatch. Hence REQ-6's mechanical lint:
the ast-grep pattern mechanically enforces the rule in CI, so the next service
added to the repo cannot silently introduce the same bug regardless of author
awareness.
