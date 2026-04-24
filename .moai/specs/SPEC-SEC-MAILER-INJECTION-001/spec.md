---
id: SPEC-SEC-MAILER-INJECTION-001
version: 0.2.0
status: draft
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: critical
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-MAILER-INJECTION-001: klai-mailer Template Injection + SMTP Relay Hardening

## HISTORY

### v0.2.0 (2026-04-24)
- Expanded from stub into full EARS SPEC by manager-spec
- Added research.md (source-file walkthrough of `str.format` attack surface + Jinja2
  sandbox design) and acceptance.md (verifiable scenarios AC-1..AC-9)
- EARS requirements REQ-1 through REQ-10 with sub-requirements
- Threat model expanded with 4 adversary scenarios (str.format introspection RCE,
  SMTP-relay phishing, /debug log-bomb, webhook replay)
- Cross-references to SPEC-SEC-WEBHOOK-001 (fail-closed config validator pattern),
  SPEC-SEC-INTERNAL-001 (`hmac.compare_digest` for internal-secret gate), and
  SPEC-SEC-HYGIENE-001 (double-gate pattern for `/debug`)

### v0.1.0 (2026-04-24)
- Stub created from internal-audit wave on klai-mailer
- Priority P0 -- `str.format` introspection RCE + open-relay chain is the single worst
  finding in the internal audit set
- Expand via `/moai plan SPEC-SEC-MAILER-INJECTION-001`

---

## Findings addressed

| # | Finding | Severity | Evidence |
|---|---|---|---|
| mailer-2 | `/internal/send` renders templates with `str.format(**variables)` where `variables` is attacker-controlled JSON. Introspection via `{brand_url.__class__.__mro__[1].__subclasses__}` leaks `settings.smtp_password`, `webhook_secret`, `internal_secret` | CRITICAL | [main.py:200-201](../../../klai-mailer/app/main.py#L200) |
| mailer-3 | `/internal/send` has no allowlist on `to_address`; Klai SMTP usable as SPF/DKIM-aligned phishing relay | HIGH | [main.py:209-210](../../../klai-mailer/app/main.py#L209) |
| mailer-4 | `/debug` endpoint logs raw payload; gated only by `DEBUG=true` | MEDIUM (HIGH under drift) | [main.py:218-237](../../../klai-mailer/app/main.py#L218) |
| mailer-5 | `_validate_incoming_secret` at `/internal/send` uses `!=` (not `hmac.compare_digest`) -- timing oracle on the internal secret | HIGH | [main.py:182](../../../klai-mailer/app/main.py#L182) |
| mailer-6 | 5-min window webhook replay without nonce -- OTP code re-emit | MEDIUM | [main.py:69-83](../../../klai-mailer/app/main.py#L69) |
| mailer-7 | `_verify_zitadel_signature` returns distinct error messages per verification stage ("Missing header" / "Malformed header" / "Webhook timestamp too old" / "Invalid signature") -- verification-phase oracle | LOW-MEDIUM | [main.py:57-83](../../../klai-mailer/app/main.py#L57) |
| mailer-8 | `ZITADEL-Signature` parser silently ignores unknown `vN=` fields; if a future Zitadel release emits `v2=` with a weaker algorithm the parser will accept the record as valid if `v1=` is still present, and will silently accept rogue entries otherwise | LOW | [main.py:61-63](../../../klai-mailer/app/main.py#L61) |
| mailer-9 | `WEBHOOK_SECRET` is typed `str` (required at `config.py:18`), but pydantic-settings accepts empty-string env vars. With `WEBHOOK_SECRET=""` the HMAC still computes over the empty key and `hmac.compare_digest` silently permits any signature produced with the empty key -- startup does NOT refuse | HIGH | [config.py:18](../../../klai-mailer/app/config.py#L18) |

Chain A (Cornelis + internal): any `klai-net` foothold with `INTERNAL_SECRET` reaches
`/internal/send`, mounts `str.format`-introspection, dumps `settings` including all secrets,
and exfiltrates via SMTP to an attacker-chosen inbox. Severity CRITICAL.

Chain B: attacker with the same `klai-net` foothold sends arbitrary-content email to
arbitrary recipients using Klai's SPF/DKIM-aligned sender domain, bypassing recipient-side
spam filters that would otherwise block the same body from a fresh domain.

---

## Goal

Eliminate the template-injection, open-relay, and log-replay paths in klai-mailer. The service
SHALL accept only: (a) well-formed Zitadel webhooks with valid signature and a fresh nonce, or
(b) `/internal/send` calls whose template, recipient, and variables are validated against an
explicit Pydantic schema -- no format-string introspection primitive, no arbitrary recipient,
no raw variable passthrough to Jinja's unsandboxed environment, no raw-payload log endpoint in
production.

---

## Success Criteria

- `/internal/send` renders templates via Jinja2's `SandboxedEnvironment.from_string(...).render(**vars)`
  instead of `str.format(**variables)`. Autoescape is ON. `StrictUndefined` raises on unknown
  variables. Sandbox blocks access to dunder attributes (`__class__`, `__mro__`,
  `__subclasses__`, `__globals__`, etc.).
- `variables` is validated against a per-template Pydantic v2 model BEFORE rendering. Unknown
  keys are rejected (`extra="forbid"`); missing required keys cause 400.
- `to_address` is bound to an expected value derived from org data per template:
  `join_request_admin` -> the org admin's email resolved via portal-api callback,
  `join_request_approved` -> the `email` field of the approved request's submitter.
  Attacker-supplied `to_address` that does not match the template-derived expectation is
  rejected with 400.
- Per-recipient-email rate limit (Redis-backed, 10 sends / 24h per recipient). 11th send in
  the window returns 429 with `Retry-After`.
- `/debug` endpoint is double-gated on `DEBUG=true` AND `PORTAL_ENV != "production"`.
  Same pattern as SPEC-SEC-HYGIENE-001 REQ-28. Under production env the endpoint returns 404
  regardless of the `DEBUG` flag. Removed entirely in production Docker builds is the
  preferred implementation; the double-gate is the fallback that MUST be in place either way.
- Zitadel webhook signatures are nonce-tracked in Redis (SET NX + EXPIRE 300s). A signature
  whose `(timestamp, v1)` pair has been seen within the 5-minute window is rejected with 401.
- `_verify_zitadel_signature` produces a single "invalid signature" body for every verification
  failure (removes the "timestamp too old" / "malformed header" / "missing header" oracles).
- `_validate_incoming_secret` on `/internal/send` uses `hmac.compare_digest` against the
  `X-Internal-Secret` header (replaces the `!=` comparison at `main.py:182`). Duplicates the
  same fix landed by SPEC-SEC-INTERNAL-001 REQ-1 for `taxonomy.py:382-388`; both SPECs MUST
  converge on the same compare_digest pattern.
- Empty `WEBHOOK_SECRET` refuses startup. Pydantic field validator on the `Settings` model
  raises `ValueError("Missing required: WEBHOOK_SECRET")` when the value is empty or
  whitespace-only. Mirrors SPEC-SEC-WEBHOOK-001 REQ-9's `_require_vexa_webhook_secret` pattern.
  Applies to `INTERNAL_SECRET` as well (both are hard gates for this service).
- `ZITADEL-Signature` parser explicitly rejects unknown `vN=` fields rather than silently
  ignoring them. Any field other than `t=` and `v1=` causes a parse rejection.
- Regression tests (see acceptance.md):
  - AC-1: `variables={"name":"{__class__.__mro__[1].__subclasses__}"}` -> 400 or literal
  - AC-2: `to_address` outside template allowlist -> 400
  - AC-3: 11th send to same recipient in 24h -> 429
  - AC-4: `/debug` returns 404 when `PORTAL_ENV=production`
  - AC-5: Zitadel webhook replayed within 5-min window -> 401 "replay"
  - AC-6: Every signature-verification failure returns identical 401 body
  - AC-7: `WEBHOOK_SECRET=""` -> container refuses to start
  - AC-8: `ZITADEL-Signature` with extra `v2=` field -> rejected with log event
  - AC-9: Legitimate `join_request_admin` / `join_request_approved` emails still render
    correctly (golden-output regression)

---

## Environment

- Service: `klai-mailer` (FastAPI, Jinja2, aiosmtplib, Python 3.13)
- Files in scope:
  - [klai-mailer/app/main.py](../../../klai-mailer/app/main.py) -- `/internal/send` (L176-215),
    `/notify` (L96-128), `/debug` (L218-237), `_verify_zitadel_signature` (L49-83),
    `_INTERNAL_TEMPLATES` dict (L136-173)
  - [klai-mailer/app/config.py](../../../klai-mailer/app/config.py) -- `webhook_secret`
    (L18), `internal_secret` (L35), `debug` (L38); new: `portal_env` field, validators
  - [klai-mailer/app/renderer.py](../../../klai-mailer/app/renderer.py) -- existing Jinja2
    `Environment` at L79-82 (autoescape ON, sandbox OFF); `/notify` flow is already Jinja2
    and already safe -- scope expansion adds sandbox for defence-in-depth
  - [klai-mailer/theme/email.html.j2](../../../klai-mailer/theme/email.html.j2) -- existing
    wrapper template; new: `klai-mailer/theme/internal/<template-name>.html.j2` per internal
    template (replaces the inline strings in `_INTERNAL_TEMPLATES`)
  - [klai-mailer/app/portal_client.py](../../../klai-mailer/app/portal_client.py) -- existing
    thin httpx client for portal-api; new: `resolve_org_admin_email(org_id)` or equivalent
    callback for the `join_request_admin` recipient binding
  - New: `klai-mailer/app/schemas.py` -- Pydantic v2 models, one per internal template name
  - New: `klai-mailer/app/rate_limit.py` -- Redis-backed sliding-window per-recipient limiter
  - New: `klai-mailer/app/nonce.py` -- Redis nonce store for `_verify_zitadel_signature`
- Redis: same pool shared with portal-api for audit (see SPEC-SEC-005 Environment). A new
  `REDIS_URL` env var is added to klai-mailer (currently absent). Key namespaces:
  - `mailer:nonce:<timestamp>:<v1>` (TTL 300s)
  - `mailer:rl:<sha256(lowercase-recipient-email)>` (sliding window, TTL 24h)
- Runtime: uvicorn on `klai-net`. Service is reached only by portal-api (for `/internal/send`)
  and by Zitadel (for `/notify`, via Caddy). Not publicly exposed.

---

## Assumptions

- Existing two internal templates (`join_request_admin`, `join_request_approved`) can be
  migrated to Jinja2 files without visible output changes, subject to AC-9's golden-output
  regression.
- Legitimate callers of `/internal/send` (portal-api admin flows) can conform to the
  new recipient-binding rules -- they already send to org-member addresses. For
  `join_request_admin` the portal-api caller passes `org_id` alongside the template and
  variables; klai-mailer resolves the admin email via portal-api callback. For
  `join_request_approved` the caller passes the approval-request id or the requester's
  email, and the `variables.email` field must match.
- Redis is available on `klai-net`. If Redis is unreachable:
  - Nonce check: fail CLOSED (reject the webhook with 503) -- the 5-min replay window is a
    security control, not an availability control. Matches SPEC-SEC-WEBHOOK-001's fail-closed
    posture for webhook auth.
  - Rate limit: fail OPEN (allow the send) with a `warning` log event. Matches SPEC-SEC-005
    REQ-1.3 for the internal rate limit. Rationale: a failed nonce check is an immediate
    security signal; a failed rate-limit check is a degraded-monitoring condition, not an
    auth bypass.
- `PORTAL_ENV` env var is already set on every production container (see SPEC-SEC-HYGIENE-001
  REQ-28 dependency). klai-mailer adds a pydantic-settings field to surface it.
- Zitadel does not currently send any `vN=` field other than `v1=`. If a future Zitadel
  release adds `v2=` (e.g. SHA-512 / HMAC-BLAKE2), REQ-10's strict parser will reject those
  payloads -- this is the intentional forcing function. The runbook SHALL document how to
  extend the parser when Zitadel adds new signature versions.
- The existing `/notify` Jinja2 render path (via `Renderer.wrap`) is NOT the injection
  primitive; Zitadel pre-renders message text in `templateData.text` and klai-mailer only
  wraps it. This SPEC does not remove that path but upgrades its Environment to
  `SandboxedEnvironment` for defence-in-depth (REQ-1.4).

---

## Out of Scope

- Replacing shared SMTP outbound with per-tenant DKIM keys -- mail-provider handles DKIM
  signing at the SMTP layer. This SPEC does not change the outbound SMTP auth model.
- Migrating to a managed transactional-email service (Postmark, Sendgrid) -- strategic
  future SPEC. The current self-hosted SMTP path is preserved.
- HIBP-integrated breached-recipient check -- future hygiene SPEC.
- Adding mTLS between portal-api and klai-mailer -- future infra SPEC (SPEC-INFRA-TLS-001).
  The shared-secret model is strengthened here, not replaced.
- Moving `/internal/send` schemas into a shared `klai-shared` package for portal-api to
  pre-validate -- future refactor. The service-side validator is authoritative for this
  SPEC; portal-api-side validation can be added incrementally.
- Webhook-level audit log analogous to SPEC-SEC-005 REQ-2 for `/internal/*` -- low-volume
  surface, can be added later if needed.

---

## Threat Model

Adversary scenarios considered:

1. **klai-net foothold with INTERNAL_SECRET -- str.format introspection RCE (mailer-2).**
   Attacker compromises a sibling container on `klai-net` (e.g. via a dependency
   vulnerability) and has read access to `INTERNAL_SECRET` from shared env state. They
   POST to `/internal/send` with
   `variables={"name": "{brand_url.__class__.__mro__[1].__subclasses__[0].__init__.__globals__[sys].modules[app.config].settings.smtp_password}"}`
   against `template=join_request_admin`, `to=attacker@example.com`. Python's `str.format`
   resolves the introspection chain and the rendered email body contains the SMTP
   password. Body is exfiltrated via the legitimate SMTP relay. Every setting in
   `app.config.Settings` is reachable the same way (webhook_secret, internal_secret,
   portal_internal_secret).
   **Mitigation:** REQ-1 migrates to `SandboxedEnvironment` which blocks all dunder
   introspection; REQ-2 per-template schema rejects unknown keys (the payload above is
   also rejected here because `name` in `JoinRequestAdminVars` is a
   constrained string, not a template fragment). REQ-3 independently rejects the payload
   because `to` is not the org-admin address.

2. **klai-net foothold -- SPF/DKIM-aligned phishing relay (mailer-3).** Attacker with the
   `INTERNAL_SECRET` sends arbitrary content to arbitrary external recipients using Klai's
   SMTP credentials. Recipient mail servers see valid SPF + DKIM for the Klai sending
   domain and treat the message as authentic. Even without the introspection payload,
   this is a phishing platform.
   **Mitigation:** REQ-3 binds `to_address` to an expected derivation from template
   context; free-form recipient is rejected. REQ-4 caps per-recipient volume so even a
   legitimate recipient pattern cannot be weaponised for bulk phishing.

3. **/debug endpoint log-bomb (mailer-4).** In a staging or pre-prod environment where
   `DEBUG=true` accidentally ships to a production-adjacent host, an attacker POSTs a
   valid-signed Zitadel-formatted payload containing a 1 MB base64 blob. The endpoint
   writes the full blob to the structured log stream on every request, overwhelming
   VictoriaLogs retention and/or exfiltrating sensitive content from neighbouring log
   streams via log-correlation.
   **Mitigation:** REQ-5 double-gates `/debug` on `DEBUG=true` AND `PORTAL_ENV != production`.
   Production builds SHOULD omit the `/debug` route entirely (preferred); the double-gate
   is the fallback when the route cannot be conditionally registered.

4. **Zitadel webhook replay (mailer-6).** Attacker captures a legitimate Zitadel webhook
   HTTP request in-flight (via compromised reverse-proxy log, network mirror, or a leaked
   Alloy log shard). Within 5 minutes they replay the same body + same
   `ZITADEL-Signature` header against `/notify`. The signature validates; the email is
   resent. For password-reset or OTP flows this results in code re-emission to the same
   recipient, extending the code's lifetime beyond Zitadel's intended TTL.
   **Mitigation:** REQ-6 records `(timestamp, v1)` pairs in Redis with a 5-min TTL; a
   replay hits the existing key and is rejected with 401 "invalid signature" (the error
   body is identical to any other signature failure per REQ-7, so replay is not
   distinguishable from wrong-key by the attacker).

Explicit non-goals for this threat model:

- Defeating a determined attacker with valid `INTERNAL_SECRET` **and** the ability to
  spoof a portal-api origin on `klai-net`. mTLS is the correct mitigation; see Out of
  Scope.
- Protecting against misuse by a legitimate internal caller that is itself compromised
  (e.g. portal-api is RCE'd and calls `/internal/send` with correctly-bound recipients
  for phishing). The defence at that point is upstream (portal-api hardening per
  SPEC-SEC-SSRF-001, SPEC-SEC-INTERNAL-001).

---

## Requirements

### REQ-1: Jinja2 Sandboxed Rendering for Internal Templates

The system SHALL render internal transactional email templates via Jinja2's
`SandboxedEnvironment` with autoescape and `StrictUndefined`, replacing the
`str.format(**variables)` code path.

- **REQ-1.1:** WHEN `/internal/send` renders an internal template, THE service SHALL use
  `jinja2.sandbox.SandboxedEnvironment(autoescape=select_autoescape(["html", "j2"]),
  undefined=StrictUndefined)`. The `str.format(**variables)` calls at
  `klai-mailer/app/main.py:200-201` SHALL be removed.
- **REQ-1.2:** WHEN a template variable references a dunder attribute
  (`__class__`, `__mro__`, `__subclasses__`, `__globals__`, `__init__`, `__import__`, etc.),
  THE sandbox SHALL raise `SecurityError` and THE request handler SHALL return HTTP 400
  with body `{"detail": "unexpected placeholder"}`. The original attacker-supplied
  variable value SHALL NOT be echoed in the response body or in logs (no reflection).
- **REQ-1.3:** WHEN a template references an undefined variable (e.g. Jinja `{{ foo }}`
  with no `foo` in the render context), THE `StrictUndefined` behaviour SHALL raise and
  THE handler SHALL return HTTP 400 with body `{"detail": "missing required variable"}`.
- **REQ-1.4:** THE existing `Renderer._theme_env` at `renderer.py:79-82` (currently a
  plain `Environment`) SHALL be upgraded to `SandboxedEnvironment` for the
  `email.html.j2` wrapper. This is defence-in-depth -- the Zitadel `/notify` path passes
  `templateData.text` that is already HTML-escaped by `_text_to_html` and rendered with
  `| safe`, so the wrapper template itself is not currently an injection primitive, but
  moving to sandbox mode ensures the safety property survives future refactors.
- **REQ-1.5:** Each internal template SHALL live in a separate file under
  `klai-mailer/theme/internal/<template-name>.<lang>.html.j2`. The inline strings in
  `_INTERNAL_TEMPLATES` at `main.py:136-173` SHALL be deleted. A `TEMPLATE_REGISTRY` dict
  SHALL map `(template_name, lang)` to the template file path.

### REQ-2: Per-Template Pydantic Variable Schema

The system SHALL validate `variables` against a per-template Pydantic v2 model before
rendering. Unknown keys SHALL be rejected.

- **REQ-2.1:** THE repository SHALL define one Pydantic v2 model per internal template
  name in `klai-mailer/app/schemas.py`. Initial set:
  - `JoinRequestAdminVars(name: str, email: EmailStr, org_id: int)` -- the
    `join_request_admin` template
  - `JoinRequestApprovedVars(name: str, workspace_url: HttpUrl)` -- the
    `join_request_approved` template
  Each model SHALL have `model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)`.
- **REQ-2.2:** WHEN `/internal/send` receives a request, THE handler SHALL resolve the
  Pydantic schema from a `TEMPLATE_SCHEMAS: dict[str, type[BaseModel]]` registry keyed
  on `template_name`. IF the template is unknown, THEN HTTP 400 with
  `{"detail": "Unknown template: <name>"}` (current behaviour at `main.py:193` is
  preserved).
- **REQ-2.3:** WHEN `template_schema.model_validate(variables)` raises `ValidationError`,
  THE handler SHALL return HTTP 400 with body `{"detail": "invalid variables",
  "errors": <pydantic-error-list>}`. THE errors list SHALL NOT echo attacker-supplied
  values for `str` fields longer than 80 characters (truncate to the first 80 chars +
  `...` to avoid log-bomb / reflection).
- **REQ-2.4:** Branding variables (`brand_url`, `logo_url`, `logo_width`) SHALL be
  injected into the render context from `settings`, NOT from the request body. The
  current practice at `main.py:198` (overwriting `variables["brand_url"]`) SHALL be
  replaced with a wrapper that constructs the render context as
  `{**validated_model.model_dump(), **branding_from_settings}`, which makes it impossible
  for the caller to override branding via a crafted `variables` payload.
- **REQ-2.5:** THE per-template schema SHALL be the single source of truth for the
  accepted variable surface. Adding a variable to an internal template SHALL require
  both a schema change AND a template-file change in the same commit. A CI check
  (REQ-5.6 in acceptance.md) SHALL enforce that every `{{ var }}` in a template file
  has a matching field in the template's schema.

### REQ-3: Per-Template Recipient (`to_address`) Allowlist

The system SHALL bind `to_address` to a value derived from the template's context, not
to a free-form caller-supplied string.

- **REQ-3.1:** WHEN `/internal/send` validates a request against `JoinRequestAdminVars`,
  THE service SHALL resolve the expected recipient as the organisation's admin email via
  a portal-api callback (`GET /internal/org/{org_id}/admin-email` or an equivalent
  existing endpoint). THE handler SHALL compare the caller-supplied `to` field to the
  resolved admin email (case-insensitive, whitespace-stripped). IF they differ, THEN
  HTTP 400 with `{"detail": "recipient mismatch"}`.
- **REQ-3.2:** WHEN `/internal/send` validates a request against
  `JoinRequestApprovedVars`, THE service SHALL require the caller-supplied `to` field to
  match `variables.email` (the approved submitter's email). IF they differ, THEN HTTP
  400 with `{"detail": "recipient mismatch"}`. Equivalently, the handler MAY ignore
  the `to` field entirely and send to `variables.email`; both are acceptable.
- **REQ-3.3:** IF a future template has NO natural recipient-binding (e.g. a broadcast
  template), THEN it SHALL NOT be added to `TEMPLATE_REGISTRY` without an explicit
  override declared in the schema (`class BroadcastVars: __allow_free_recipient__ =
  True`). Templates without the override MUST have a recipient-derivation rule.
- **REQ-3.4:** THE portal-api callback used by REQ-3.1 SHALL be authenticated with the
  same `INTERNAL_SECRET` pattern and SHALL time out at 3.0s. ON timeout or network
  error, THE handler SHALL return HTTP 503 with `{"detail": "recipient lookup
  unavailable"}` AND log `event="recipient_lookup_failed"`. Failing CLOSED here is
  correct: without the lookup, the service cannot prove the recipient is legitimate.

### REQ-4: Per-Recipient-Email Rate Limit

The system SHALL enforce a Redis-backed rate limit of 10 sends per 24 hours per
recipient email.

- **REQ-4.1:** WHEN `/internal/send` is about to dispatch an email, THE service SHALL
  check a sliding-window counter keyed on
  `mailer:rl:<sha256(lowercase-recipient-email)>`. IF the counter exceeds 10 within
  the trailing 24-hour window, THEN HTTP 429 with body
  `{"detail": "recipient rate limit exceeded"}` AND `Retry-After: <seconds-until-next-slot>`
  header.
- **REQ-4.2:** THE key SHALL use the SHA-256 digest of the recipient email (not the
  email itself) so that Redis-access logs, if ever exposed, do not leak the recipient
  list.
- **REQ-4.3:** WHEN Redis is unreachable, THE rate limiter SHALL fail OPEN (allow the
  send) AND log a structured warning with `event="mailer_rate_limit_redis_unavailable"`
  so monitoring can alert on degraded protection without breaking live traffic. This
  mirrors SPEC-SEC-005 REQ-1.3.
- **REQ-4.4:** THE ceiling (10) and window (86400s) SHALL be configurable via
  `settings.mailer_rate_limit_per_recipient` and `settings.mailer_rate_limit_window_seconds`
  pydantic-settings fields. Default values are 10 and 86400.
- **REQ-4.5:** THE rate-limit counter SHALL be incremented AFTER the per-template
  schema validation (REQ-2) and recipient binding (REQ-3) pass, and BEFORE the SMTP
  dispatch. A request that fails validation SHALL NOT deplete the recipient's budget
  (failed sends are attacker noise; legitimate retries after validation success should
  count).
- **REQ-4.6:** WHEN the rate limit rejects a send, THE handler SHALL emit a structured
  log event with `event="mailer_recipient_rate_limited"`, `recipient_hash=<sha256>`,
  `template=<name>`. The recipient email itself SHALL NOT be logged in cleartext.

### REQ-5: Double-Gate `/debug` Endpoint on Production Environment

The system SHALL NOT expose the `/debug` endpoint when running in the production
environment, regardless of the `DEBUG` flag.

- **REQ-5.1:** THE `Settings` model SHALL expose a `portal_env: str = "development"`
  field (populated from `PORTAL_ENV` env var). Accepted values: `development`, `staging`,
  `production`.
- **REQ-5.2:** WHEN `/debug` is called AND `settings.portal_env == "production"`, THE
  handler SHALL return HTTP 404 with body `{"detail": "Not found"}` REGARDLESS of
  `settings.debug`. The existing short-circuit at `main.py:225-226` SHALL be extended
  to check BOTH gates.
- **REQ-5.3:** THE Dockerfile SHOULD (preferred) register the `/debug` route
  conditionally via a module-level `if settings.portal_env != "production" and
  settings.debug:` guard around `app.post("/debug")`. The handler body SHOULD be moved
  into a private helper and attached only when both gates pass. This is the preferred
  implementation because it prevents the route from appearing in the OpenAPI schema
  (even though OpenAPI is disabled via `docs_url=None`, belt-and-braces).
- **REQ-5.4:** REQ-5.2 is the fallback requirement that MUST hold even if REQ-5.3 is
  not adopted. The double-gate at runtime is the authoritative defence.
- **REQ-5.5:** WHEN `/debug` returns 404 due to the production gate, THE handler SHALL
  NOT emit a structured log event. 404 on an unregistered endpoint is a normal HTTP
  response; logging would create a side-channel confirmation that the gate activated.

### REQ-6: Zitadel Webhook Nonce Tracking

The system SHALL track seen Zitadel webhook signatures in Redis with a 5-minute TTL and
reject replays.

- **REQ-6.1:** WHEN `_verify_zitadel_signature` has validated a signature successfully
  (HMAC matches, timestamp is within the 5-min window), THE function SHALL attempt to
  record the `(timestamp, v1)` pair as a Redis SET NX entry at key
  `mailer:nonce:<timestamp>:<v1>` with EXPIRE 300 seconds. IF the SET NX returns 0
  (key already exists), THEN the verification SHALL fail with HTTP 401. The error body
  SHALL match REQ-7's uniform body (no "replay" string leaked in the response).
- **REQ-6.2:** THE nonce key SHALL include the full `v1` hex digest so that distinct
  webhooks at the same timestamp (Zitadel emits different `v1` for different bodies)
  remain distinguishable. The TTL of 300 seconds matches the replay window enforced by
  the timestamp check at `main.py:71-76`.
- **REQ-6.3:** WHEN Redis is unreachable, THE nonce check SHALL fail CLOSED: the
  request SHALL be rejected with HTTP 503 and the structured log event
  `event="mailer_nonce_redis_unavailable"`. Rationale: a failed nonce check is a
  security signal, not a degraded-monitoring condition. Contrast with REQ-4.3 (rate
  limit fails open) -- different fail modes are deliberate.
- **REQ-6.4:** THE nonce check SHALL run AFTER the signature verification (not before).
  Otherwise an attacker could force cache-fill with forged signatures.

### REQ-7: Uniform Signature-Verification Error Body

The system SHALL return an identical HTTP 401 body for every signature-verification
failure, removing the phased-error oracle.

- **REQ-7.1:** WHEN any stage of `_verify_zitadel_signature` fails (missing header,
  malformed header, timestamp out of window, HMAC mismatch, nonce replay, unknown `vN`
  field), THE service SHALL return HTTP 401 with body `{"detail": "invalid signature"}`.
  The current distinct bodies at `main.py:59, 67, 73, 76, 83` SHALL be collapsed to
  this single response.
- **REQ-7.2:** THE internal log event emitted on failure SHALL include a precise
  `reason` field (`missing_header`, `malformed_header`, `timestamp_out_of_window`,
  `hmac_mismatch`, `replay`, `unknown_vN_field`) so operators can still distinguish
  failure modes in VictoriaLogs. The distinction SHALL exist in logs, NOT in the HTTP
  response.
- **REQ-7.3:** WHEN the verification fails, THE service SHALL NOT include a
  `WWW-Authenticate` header or any other side-channel that reveals the failure phase.

### REQ-8: Constant-Time Internal-Secret Compare

The system SHALL use `hmac.compare_digest` for the `X-Internal-Secret` header check on
`/internal/send`.

- **REQ-8.1:** THE `/internal/send` handler SHALL replace the `!=` comparison at
  `klai-mailer/app/main.py:182` with:
  ```python
  header = request.headers.get("X-Internal-Secret", "")
  if not settings.internal_secret or not hmac.compare_digest(
      header.encode("utf-8"), settings.internal_secret.encode("utf-8")
  ):
      raise HTTPException(status_code=401, detail="Unauthorized")
  ```
- **REQ-8.2:** THE same check SHALL be factored into a helper (`_validate_incoming_secret`
  or similar) so future internal endpoints cannot reintroduce the `!=` antipattern.
- **REQ-8.3:** This requirement duplicates the fix in SPEC-SEC-INTERNAL-001 REQ-1 for
  `taxonomy.py:382-388`. Both SPECs cover this class of bug (`!=` on a shared secret)
  at different call sites. Cross-reference: SPEC-SEC-INTERNAL-001 is the service-wide
  sweep; this REQ-8 is the mailer-local landing. Landing either SPEC first closes the
  mailer half; landing both is required for full coverage.

### REQ-9: Fail-Closed Startup on Empty WEBHOOK_SECRET and INTERNAL_SECRET

The system SHALL refuse to start if required shared secrets are empty or
whitespace-only.

- **REQ-9.1:** WHEN `Settings` is instantiated AND `webhook_secret` is empty or
  whitespace-only, THE pydantic-settings field validator SHALL raise
  `ValueError("Missing required: WEBHOOK_SECRET")`, aborting the uvicorn process before
  any request is served. Mirrors the existing `_require_vexa_webhook_secret` validator
  at `klai-portal/backend/app/core/config.py:235-248` (SPEC-SEC-WEBHOOK-001 REQ-9).
- **REQ-9.2:** WHEN `Settings` is instantiated AND `internal_secret` is empty or
  whitespace-only, THE validator SHALL raise
  `ValueError("Missing required: INTERNAL_SECRET")`. The current default `""` at
  `config.py:35` SHALL be replaced with a required field (no default) and a validator.
- **REQ-9.3:** The validators SHALL use pydantic v2's `@field_validator` with
  `mode="after"`, reading the value post-env-loading.
- **REQ-9.4:** IF a future deployment wants to disable the `/notify` or `/internal/send`
  endpoint, THEN the operator SHALL unregister the router rather than leave the secret
  empty. Empty-secret is never a valid runtime state. This SHALL be documented in the
  deployment runbook.

### REQ-10: Strict ZITADEL-Signature Parser

The system SHALL reject `ZITADEL-Signature` headers containing any field other than
`t=` and `v1=`.

- **REQ-10.1:** WHEN the parser at `main.py:61` tokenises the signature header, THE
  resulting `parts` dict SHALL be checked for unexpected keys. IF any key other than
  `t` and `v1` is present, THEN the verification SHALL fail (uniform 401 per REQ-7)
  AND the log event SHALL use `reason="unknown_vN_field"` with
  `unknown_fields=<list>`.
- **REQ-10.2:** IF a future Zitadel release adds `v2=` or `v3=`, THEN the parser SHALL
  be extended explicitly in a versioned change (new SPEC, or a point-release of this
  one) -- never via a silent-accept pattern. This SHALL be documented inline as a
  `@MX:NOTE` comment near the parser and in the deployment runbook.
- **REQ-10.3:** THE parser SHALL also reject headers with more than 5 tokens (defence
  against header-splitting / injection attempts).

---

## Non-Functional Requirements

- **Performance:** Sandbox + schema validation + recipient callback + rate-limit check
  combined SHALL add no more than 20 ms p95 overhead to `/internal/send`. The portal-api
  recipient callback (REQ-3.1) dominates this budget; the other checks are sub-millisecond.
- **Observability:** Every requirement's rejection path SHALL emit a structured log event
  with a stable `event` key. Keys inventory:
  `mailer_template_sandbox_violation`, `mailer_template_schema_invalid`,
  `mailer_recipient_mismatch`, `mailer_recipient_lookup_failed`,
  `mailer_recipient_rate_limited`, `mailer_rate_limit_redis_unavailable`,
  `mailer_nonce_replay`, `mailer_nonce_redis_unavailable`, `mailer_signature_invalid`
  (with `reason` sub-field), `mailer_internal_secret_invalid`,
  `mailer_signature_unknown_field`. All queryable via `victorialogs` MCP.
- **Privacy:** Recipient emails SHALL NOT appear in log cleartext. Use SHA-256 hash
  (`recipient_hash`) in rate-limit events. Template render failures MUST NOT reflect
  attacker-supplied values back to the response body (REQ-1.2).
- **Backward compatibility:** Existing portal-api callers of `/internal/send` SHALL
  continue to function. For `join_request_admin`: portal-api already has `org_id` in
  context. For `join_request_approved`: portal-api already has the submitter email.
  Adjusting the caller is a same-PR contract change.
- **Fail modes:**
  - Redis unreachable (nonce): fail CLOSED (503). Security > availability for webhook
    auth.
  - Redis unreachable (rate limit): fail OPEN (allow + warn). Degraded monitoring is
    acceptable; hard-blocking all sends on Redis outage breaks incident response email.
  - portal-api unreachable (recipient callback): fail CLOSED (503). Without the
    callback the service cannot prove recipient legitimacy.
  - Empty `WEBHOOK_SECRET` or `INTERNAL_SECRET`: fail CLOSED at startup (service
    refuses to bind).

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md) (findings
  mailer-2..mailer-9)
- Related: [SPEC-SEC-WEBHOOK-001](../SPEC-SEC-WEBHOOK-001/spec.md) -- same fail-closed
  config-validator pattern (REQ-9); portal-api's `_require_vexa_webhook_secret` is the
  reference implementation for REQ-9.1 / REQ-9.2
- Related: [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md) -- same
  `hmac.compare_digest` fix at a different call site (`taxonomy.py:382-388`); REQ-8 here
  is the mailer-local landing of the same pattern
- Related: [SPEC-SEC-HYGIENE-001](../SPEC-SEC-HYGIENE-001/spec.md) -- same double-gate
  pattern for `/debug` (REQ-28 for `/docs` + `/openapi.json` in portal-api)
- Related: [SPEC-SEC-005](../SPEC-SEC-005/spec.md) -- Redis rate-limit pattern reused;
  fail-open-on-Redis-unreachable posture mirrored for REQ-4.3
- Audit source: klai-security-audit agent run, 2026-04-24, findings mailer-2..mailer-9
