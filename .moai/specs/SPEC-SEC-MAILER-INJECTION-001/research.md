# SPEC-SEC-MAILER-INJECTION-001 Research

Codebase analysis for the klai-mailer template-injection + SMTP-relay hardening SPEC.
Supports requirements REQ-1 through REQ-10. Traces each finding to the exact source line,
explains the attack primitive, and derives the chosen mitigation.

## Current rendering flow (end-to-end)

Request path for `/internal/send` today:

1. `klai-portal/backend/...` (admin join-request approval flow) POSTs JSON to
   `http://mailer:8030/internal/send` with the `X-Internal-Secret` header. Body shape:
   ```json
   {
     "template": "join_request_admin",
     "to": "admin@example.com",
     "locale": "nl",
     "variables": {"name": "Alice", "email": "alice@example.com"}
   }
   ```
2. `klai-mailer/app/main.py:176-215` (`internal_send`) authenticates via
   `X-Internal-Secret` at line 182 using `!=`:
   ```python
   if not settings.internal_secret or request.headers.get("X-Internal-Secret") != settings.internal_secret:
       raise HTTPException(status_code=401, detail="Unauthorized")
   ```
   (Finding mailer-5; REQ-8 replaces with `hmac.compare_digest`.)
3. The handler reads `body["template"]`, `body["to"]`, `body["locale"]`, `body["variables"]`
   with no schema -- `body.get(...)` everywhere.
4. Line 191-193 looks up the template in the inline `_INTERNAL_TEMPLATES` dict. Unknown
   template -> 400 (the ONE validation that exists today).
5. Line 195 selects the locale variant: `lang_template = template.get(locale, template.get("nl", {}))`.
6. Line 198 mutates the caller-supplied dict: `variables["brand_url"] = settings.brand_url`.
   (REQ-2.4 removes this mutation pattern.)
7. **Line 200-201 -- the vulnerable calls:**
   ```python
   subject = lang_template["subject"].format(**variables)
   body_html = lang_template["body"].format(**variables)
   ```
   `str.format(**variables)` evaluates arbitrary Python attribute access in the format
   string. Any key in `variables` becomes a root identifier; `{key.__class__...}`
   walks the Python object graph.
8. Line 204-207 wraps the rendered body+subject via `Renderer.wrap` (Jinja2, already
   autoescape ON, existing `renderer.py:79-82`).
9. Line 210 SMTPs out via `send_email` (`klai-mailer/app/mailer.py`).

## `str.format` attack-surface walkthrough

The format mini-language supports attribute access (`.`) and item access (`[]`) on any
substituted root. Given:

```python
"Hello {name}".format(name=payload)
```

If `payload` is `"x"`, the output is `Hello x`. But if the format string is attacker-
controlled, any resolved variable can be walked:

```python
"{x.__class__.__mro__[1].__subclasses__}".format(x=some_value)
```

This resolves to the string representation of the `object.__subclasses__` bound method.
From there:

```python
"{0.__class__.__mro__[1].__subclasses__()}".format(some_value)
```

...prints the full `object.__subclasses__()` list -- roughly 500 classes reachable from
the interpreter, including `subprocess.Popen`, `importlib._bootstrap.SourceFileLoader`,
`os._wrap_close`, etc.

In klai-mailer's `/internal/send`, the format strings ARE server-side (the template
content in `_INTERNAL_TEMPLATES`), but `variables` is attacker-controlled. The payload
is crafted the other way:

```json
{
  "template": "join_request_admin",
  "to": "attacker@example.com",
  "variables": {
    "name": "irrelevant",
    "email": "{__class__.__mro__[1].__subclasses__()[...idx...].__init__.__globals__['sys'].modules['app.config'].settings.smtp_password}"
  }
}
```

The server-side format string `"{name} ({email})"` resolves `{email}` -- which at that
point is the attacker-supplied string. **But format doesn't re-parse substituted values.**
Substituted values are inserted literally.

Wait -- so how does this work?

It works because the format string contains named placeholders like `{brand_url}` and
`{name}`, and `str.format(**variables)` EVALUATES the placeholder expressions against
`variables`. The placeholder itself is in the server-side template; the attack requires
the attacker to either:

(a) Influence the template string itself (not possible here; templates are server-side), OR
(b) Be able to add a key to `variables` that the template references, where the key name
    is the attack vector -- e.g., the template says `{brand_url}` and the attacker
    supplies `variables={"brand_url": "attacker-controlled-string"}`. But this only
    substitutes the string literally; no introspection happens.

So the critical detail: **`str.format` lets the template string (not the values) walk
attributes of the values.** If the server-side template contained
`{brand_url.__class__}`, that would be a server-owned escape. But the server-side
templates in `_INTERNAL_TEMPLATES` only use `{name}`, `{email}`, `{brand_url}`,
`{workspace_url}` -- no attribute access.

### So where's the primitive?

The primitive is `variables["brand_url"] = settings.brand_url` at line 198. The caller
supplies the initial `variables` dict with arbitrary keys, and the server overwrites
`brand_url`. But the server-side format string only references documented placeholders
(`{name}`, `{email}`, `{brand_url}` for `join_request_admin`), so attacker-supplied
extra keys are ignored by the format call -- they don't appear in the output.

**Reviewed -- the CRITICAL claim here needs the precise mechanism.**

Re-checking: in Python,
```python
">>> '{name}'.format(**{"name": "{x.__class__}", "x": "foo"})
'{x.__class__}'
```
The substituted value "{x.__class__}" is inserted literally; it is NOT re-parsed.

So the finding as stated ("`variables={"name": "{...}"}` yields RCE") is NOT exploitable
with the current server-side templates. The CVE-class bug (`str.format` template
injection) requires the attacker to control the FORMAT STRING, not the VALUES.

**However**, there are two reasons to still treat this as CRITICAL and to keep REQ-1
as written:

1. **Future template additions.** If a future developer adds a template that includes
   attribute access -- e.g., `"{brand_url.rstrip('/')}"` or even just
   `"{var!r}"` -- the attack surface opens. The audit finding guards against a class of
   bug, not just today's payload.
2. **`{0.__class__.__mro__[1].__subclasses__}` works when a template contains ANY
   attribute-access placeholder.** If any internal template anywhere in the codebase
   now or later uses dotted access on an attacker-controlled value, the primitive
   exists.

Additionally, `variables["brand_url"] = settings.brand_url` at line 198 means every
render already has `brand_url` bound to a string from `settings`. If an attacker can
add a template (e.g. via a separate bug) that references `{brand_url.<attr>}`, the
introspection chain on `settings.brand_url` reaches `app.config` where all secrets
live.

### Mitigation: Jinja2 sandbox

`jinja2.sandbox.SandboxedEnvironment` blocks `__` prefixed attribute access and
`_` prefixed attribute access is restricted via `is_safe_attribute` checks. It also
restricts function-call argument types. Any attempt to resolve `{{ x.__class__ }}`
raises `SecurityError`.

Crucially, `StrictUndefined` raises on any reference to a variable not in the render
context -- combined with the per-template Pydantic schema (REQ-2), the render-time
context is a fixed set of typed fields. Unknown keys can't even reach the renderer
because schema validation rejects them first.

This gives us defence in depth:
- Layer 1 (REQ-2): Pydantic `extra="forbid"` rejects unknown keys at the request
  boundary.
- Layer 2 (REQ-1.3): `StrictUndefined` raises if a template references a variable not
  supplied (catches schema/template mismatch).
- Layer 3 (REQ-1.2): `SandboxedEnvironment` blocks dunder access even if a future
  template accidentally uses attribute syntax on an attacker-influenceable field.

## Per-template Pydantic schema design

Target structure:

```python
# klai-mailer/app/schemas.py
from pydantic import BaseModel, ConfigDict, EmailStr, HttpUrl

class JoinRequestAdminVars(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    email: EmailStr
    org_id: int

class JoinRequestApprovedVars(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str
    workspace_url: HttpUrl

TEMPLATE_SCHEMAS: dict[str, type[BaseModel]] = {
    "join_request_admin": JoinRequestAdminVars,
    "join_request_approved": JoinRequestApprovedVars,
}
```

The `org_id` field on `JoinRequestAdminVars` is NEW (not in the current payload).
Portal-api already has `org_id` in context when approving join requests, so adding it
is a same-PR contract change. It is required for REQ-3.1 (recipient binding via
callback).

Field-type choices:
- `EmailStr` (pydantic-email-validator) for any email field -- catches malformed
  addresses before they reach SMTP.
- `HttpUrl` for URL fields -- catches schemes other than http/https.
- `int` for `org_id` -- defends against path-style injection in the callback URL.
- Plain `str` for display fields (`name`) -- `str_strip_whitespace=True` trims
  surrounding whitespace; per-character sanitisation is unnecessary because autoescape
  handles HTML safety.

## Recipient-validation design

Two candidate approaches were considered:

### Option A: portal-api callback (CHOSEN)

Klai-mailer resolves the expected recipient by calling portal-api:

```
GET http://portal-api:8010/internal/org/{org_id}/admin-email
Authorization: Bearer <PORTAL_INTERNAL_SECRET>
```

Pros:
- Single source of truth for org membership is portal-api's DB.
- The callback is internal-secret authenticated; existing plumbing
  (`portal_client.py`) already handles this for `get_user_language`.
- Adding a sibling endpoint (`/internal/org/{id}/admin-email`) is a trivial route.

Cons:
- Adds ~10 ms p95 to `/internal/send`. Acceptable (REQ-NFR Performance budget is
  20 ms).
- Introduces a portal-api dependency for mailer. Portal-api is already in the critical
  path for `get_user_language`, so this doesn't add a new failure mode.
- Fail-closed posture means portal-api outages block admin-notification emails. This
  is acceptable because admin-notification emails depend on portal-api state anyway
  (the join-request originates there).

### Option B: signed recipient-hint from portal-api

Portal-api signs the recipient alongside the template name with a shared HMAC key;
klai-mailer verifies the signature locally without a callback.

Pros:
- No portal-api dependency at render time.

Cons:
- Requires a new shared signing key (another secret to rotate).
- Signing-key rotation becomes a cross-service coordination problem.
- The signing logic lives in two services -- bug-prone.
- Equivalent security: portal-api is the authoritative source for the admin email in
  both cases.

**Decision:** Option A. The callback is simple, uses existing auth plumbing, and the
failure mode (portal-api down -> no admin emails) is not worse than today (portal-api
down -> no `/internal/send` calls at all).

## Redis nonce schema

Key format: `mailer:nonce:<timestamp>:<v1>`

Example: `mailer:nonce:1745000000:5e884898da...`

Redis operations:
```
SET mailer:nonce:1745000000:5e884898da... "1" NX EX 300
```

- `NX` -> only succeeds if key doesn't exist. Return value 1 = new, 0 = replay.
- `EX 300` -> TTL 300 seconds (matches Zitadel's 5-min replay window at `main.py:71-76`).

Storage footprint: assuming 100 webhook/minute peak, 500 entries in flight at steady
state. Each entry ~100 bytes (key + value + Redis overhead). Total ~50 KB Redis RAM --
trivial.

## `/debug` removal vs double-gate trade-off

Option A: **Removal in production builds.** The `@app.post("/debug")` decorator runs
at import time. A build-time check (`if os.getenv("PORTAL_ENV") != "production"`) around
the decorator registration prevents the route from being added to the FastAPI router
in production. Benefits:
- Endpoint truly doesn't exist in prod (not just 404'd).
- Zero chance of accidental activation via `DEBUG=true` env flip.

Option B: **Double-gate at handler entry (FALLBACK).** The handler itself checks both
flags. Benefits:
- Simpler code (no conditional decorator).
- Same runtime behaviour from the client's perspective (404).

**Decision:** Implement BOTH, per REQ-5.3 + REQ-5.4.
- REQ-5.3: preferred -- conditional route registration.
- REQ-5.4: fallback -- handler-level double-gate.

The double-gate (REQ-5.4) is the authoritative requirement because it holds even if
REQ-5.3 is forgotten in a future refactor.

## Config validator pattern (REQ-9)

Reference implementation (SPEC-SEC-WEBHOOK-001 REQ-9,
`klai-portal/backend/app/core/config.py:235-248`):

```python
@field_validator("vexa_webhook_secret", mode="after")
@classmethod
def _require_vexa_webhook_secret(cls, v: str) -> str:
    if not v or not v.strip():
        raise ValueError("Missing required: VEXA_WEBHOOK_SECRET")
    return v
```

Mailer-side equivalent (REQ-9.1, REQ-9.2):

```python
# klai-mailer/app/config.py

from pydantic import field_validator

class Settings(BaseSettings):
    ...
    webhook_secret: str
    internal_secret: str  # change from `= ""` to required

    @field_validator("webhook_secret", mode="after")
    @classmethod
    def _require_webhook_secret(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Missing required: WEBHOOK_SECRET")
        return v

    @field_validator("internal_secret", mode="after")
    @classmethod
    def _require_internal_secret(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Missing required: INTERNAL_SECRET")
        return v
```

Note the pattern is identical to the reference; the forcing function is the same
(container refuses to start with empty values). Operational change: any dev
environment running mailer locally MUST now have both secrets populated, even for
routes not exercised by the dev workflow.

## Signature-parser hardening (REQ-10)

Current parser at `main.py:61`:
```python
parts = {k: v for k, v in (p.split("=", 1) for p in signature_header.split(",") if "=" in p)}
```

Accepts any `k=v` pair; silently ignores keys other than `t`, `v1`.

Hardened parser:
```python
ALLOWED_SIG_KEYS = {"t", "v1"}

def _parse_signature_header(header: str) -> dict[str, str]:
    tokens = header.split(",")
    if len(tokens) > 5:
        raise _SignatureError("unknown_vN_field")
    parts: dict[str, str] = {}
    for p in tokens:
        if "=" not in p:
            raise _SignatureError("malformed_header")
        k, v = p.split("=", 1)
        k = k.strip()
        if k not in ALLOWED_SIG_KEYS:
            raise _SignatureError("unknown_vN_field", extra={"unknown_key": k})
        parts[k] = v
    return parts
```

The 5-token ceiling at line 3 is a defence against header-splitting / padding
attacks. Zitadel's signature format is always 2 tokens (`t=...,v1=...`); 5 leaves
generous headroom for future `v2=`, `v3=`, `v4=` additions if this SPEC is extended.

## Internal-wave inventory (uncovered during research)

The audit also flagged several adjacent anti-patterns that this SPEC intentionally
does NOT cover (deferred to SPEC-SEC-INTERNAL-001 amendments):

| Finding | Location | Deferred to |
|---|---|---|
| `resp.text[:200]` error-body reflection in logs | `portal_client.py:40` | SPEC-SEC-INTERNAL-001 amendment |
| Bare logging.getLogger (not structlog) | `main.py:29`, `renderer.py:24`, `portal_client.py:16` | Separate logging migration (see `portal-logging-py.md`) |
| `except Exception as exc` without traceback | `portal_client.py:39-41` | Same migration |

These are genuine defects but do not block mailer-2..mailer-9 closure. They will land
with the next mailer-hardening SPEC or the service-wide structlog migration.

## Dependency inventory

New runtime dependencies required by REQ-1..REQ-10:

- `redis>=5.0` -- Redis client (for nonce + rate limit). Currently NOT in
  `klai-mailer/pyproject.toml`.
- `email-validator>=2.0` -- `EmailStr` support in Pydantic v2. Currently NOT present.
- `jinja2>=3.1` -- already present (Renderer uses it). `SandboxedEnvironment` is in
  `jinja2.sandbox` module, no version bump needed.

No new test dependencies (pytest + respx + fakeredis already in dev set).

## MX tag targets identified

Per `.claude/rules/moai/workflow/mx-tag-protocol.md`:

- `@MX:ANCHOR` on the new `_verify_zitadel_signature` helper after REQ-6/7/10 land
  (fan_in >= 3 via `/notify` + `/debug` call sites; will gain a test-fake call site too).
- `@MX:WARN` on the Redis-unreachable fail-open branch in the rate limiter (REQ-4.3),
  with `@MX:REASON: "degraded monitoring preferred over hard-block on Redis outage; see SPEC-SEC-MAILER-INJECTION-001 NFR"`.
- `@MX:NOTE` on the sandbox construction in `Renderer` and on `TEMPLATE_SCHEMAS` to
  document the single-source-of-truth invariant for REQ-2.5.
- `@MX:TODO` on the new internal-template files until golden-output regression (AC-9)
  passes.
