---
id: SPEC-SEC-HYGIENE-001
version: 0.3.0
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
artifact: research
---

# SPEC-SEC-HYGIENE-001 — Research

Codebase analysis feeding the requirements in `spec.md`. One section per
finding.

---

## §19 — Background provisioning + weak signup rate-limit

### Current state

`klai-portal/backend/app/api/signup.py:99-209` defines `POST /api/signup`:

1. Pydantic validates `SignupRequest` (lines 45-71).
2. `password_strength` validator checks length ≥ 12 only (lines 53-58).
3. Zitadel org is created (104-122), then user (128-153), then role
   grant (158-169).
4. PostgreSQL persist: `portal_orgs` + `portal_users` with RLS tenant
   context (172-198).
5. Background task kicked via `BackgroundTasks` (200-201) calling
   `provision_tenant(org_row.id)` — LibreChat container, MongoDB tenant
   user, vector namespaces.
6. Event emitted (`signup` event, line 202).

`deploy/caddy/Caddyfile:146-158` already rate-limits `/api/signup` and
`/api/billing/*` in the `@portal-api-sensitive` handler block:
`events 10, window 1m` keyed by `{remote_host}`. Zone name
`portal_sensitive_per_ip`.

### Gap

Per-IP limit is trivial to circumvent with a botnet or cloud-IP rotation.
Per-email limit closes the common abuse vector (e.g. fuzzing company
names to exhaust a domain's signup quota, or probing `kb_secrets` via
repeated failed signups).

### Caddy rate-limit zone semantics

Caddy's `rate_limit` plugin (community build) uses leaky-bucket per key.
Key is `{remote_host}` (client IP post-XFF normalisation). The `events`
+ `window` pair defines sustained rate. There is no built-in way to
key on request body content (email) — Caddy sees only headers + path.
Therefore the per-email limit MUST live in the application.

### Redis-based per-email approach

Reference implementation: `klai-portal/backend/app/api/partner_dependencies.py:191-199`:

```python
async def check_rate_limit(redis, key: str, max_requests: int, window_seconds: int) -> bool:
    """Sliding window rate limiter. Returns True if under the limit."""
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    return count <= max_requests
```

For signup per-email the window is 24h (86400 s) and max = 3 per REQ-19.1.
Key pattern: `signup_email_rl:<sha256_hex(normalised_email)>` — SHA-256
to keep plaintext email out of Redis (minor but zero-cost PII hardening).

Email normalisation (REQ-19.3):

```python
def _normalise_email_for_rl(email: str) -> str:
    local, _, domain = email.lower().partition("@")
    local = local.split("+", 1)[0]  # strip +alias
    return f"{local}@{domain}"
```

Note: `+alias` stripping is a gmail/Google Workspace convention. Not all
providers support it, but for abuse prevention a false positive
("legitimate `+` addressing gets rate-limited together") is preferable
to a false negative.

### Background provisioning

No change required. SPEC-PROV-001 already covers the stuck-detector
(orchestrator.py + stuck_detector.py + retry_provisioning.py admin
endpoint). REQ-19.6 only requires a docstring pointer so a future
auditor sees the existing mitigation without re-deriving it.

---

## §20 — `_validate_callback_url` subdomain allowlist

### Current state

`klai-portal/backend/app/api/auth.py:138-159`:

```python
def _validate_callback_url(url: str) -> str:
    try:
        hostname = urlparse(url).hostname or ""
    except Exception:
        hostname = ""
    if hostname in ("localhost", "127.0.0.1"):
        return url
    trusted = settings.domain  # getklai.com
    if not (hostname == trusted or hostname.endswith(f".{trusted}")):
        raise HTTPException(502, "Login failed, please try again later")
    return url
```

### Gap

The `.getklai.com` suffix check accepts `dangling.getklai.com` as well as
`voys.getklai.com`, `getklai.getklai.com`, etc. A forgotten DNS record
or a future subdomain takeover provides a foothold.

### Zitadel as primary defence

Zitadel's OIDC layer validates `redirect_uri` against the registered
application's `redirectUris` list before issuing an auth code. The
portal-api `_validate_callback_url` runs AFTER the callback returns
from Zitadel, so an attacker cannot reach this function with a
`callback_url` that Zitadel itself rejected. This is why the finding
is tagged **LOW (config-dep)**.

### Tenant-slug allowlist lookup cost

`portal_orgs.slug` is the source of truth — already populated for every
tenant (signup.py:175-176 via `_to_slug`). Query cost: one `SELECT
array_agg(slug) FROM portal_orgs WHERE deleted_at IS NULL` per cache
miss. Expected row count: <10k rows at current scale, <100k in 3 years.
Full table scan is fine; the query runs at most once per 60 seconds.

In-process cache with 60s TTL (REQ-20.2) means peak cost is ~1 query/minute
per portal-api replica (currently 1 replica, so literally 1 query/min).
Explicit invalidation on tenant create/soft-delete keeps the cache fresh
enough that a new tenant can log in within seconds of provisioning, not
after the TTL elapses.

Cache data structure: `frozenset[str]` for O(1) membership check, swapped
atomically on refresh (no lock needed in single-process asyncio).

### Alternative considered

Using Redis for the allowlist: rejected because the query is so cheap
and the TTL so short that a process-local cache is simpler and has no
network dependency. If we ever go multi-replica with high churn, moving
to Redis is a one-function refactor.

---

## §21 — `_safe_return_to` backslash + percent-decode

### Current state

`klai-portal/backend/app/api/auth_bff.py:399-404`:

```python
def _safe_return_to(value: str) -> str:
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/app"
    if "://" in value:
        return "/app"
    return value
```

### Gap

Browser URL-parsing quirks that bypass these checks:

- `/\evil.com` — the single leading backslash is normalised by browsers
  to `//evil.com` (protocol-relative), which then opens `https://evil.com`.
- `/%2fevil.com` — the `%2f` decodes to `/`, making the path
  `//evil.com` after the browser decodes. The current check runs on the
  raw string and only rejects literal `//`.
- `/\\evil.com` — double backslash; some browsers treat as `//`.

### Fix

Percent-decode ONCE (not recursively — that opens a different ambiguity
where `%25%32%66` decodes twice to `/`), then run the checks on the
decoded form. Preserve the ORIGINAL value on success so legitimate
encoded query parameters are not stripped.

```python
from urllib.parse import unquote

def _safe_return_to(value: str) -> str:
    if not value:
        return "/app"
    decoded = unquote(value)
    if not decoded.startswith("/"):
        return "/app"
    if decoded.startswith("//") or decoded.startswith("/\\"):
        return "/app"
    if "://" in decoded or "\\\\" in decoded:
        return "/app"
    return value  # return ORIGINAL, not decoded
```

### Test coverage

Covered by `test_auth_bff_return_to.py` (REQ-21.4) with the explicit cases
enumerated in REQ-21.2.

---

## §22 — Password policy zxcvbn integration

### Current state

`klai-portal/backend/app/api/signup.py:53-58`:

```python
@field_validator("password")
@classmethod
def password_strength(cls, v: str) -> str:
    if len(v) < 12:
        raise ValueError("Wachtwoord moet minimaal 12 tekens bevatten")
    return v
```

### Library candidate: zxcvbn-python

- **Package:** `zxcvbn` on PyPI (<https://pypi.org/project/zxcvbn/>),
  pure Python port of Dropbox's zxcvbn.
- **Version at time of research:** 4.4.x (stable since 2020).
- **License:** MIT.
- **Install size:** ~400 KB, no native deps.
- **Runtime memory:** ~30 MB for loaded dictionaries. Loaded on first
  call, so cold-start of portal-api is unchanged.
- **API:** `zxcvbn(password, user_inputs=[...])` returns a dict with
  `score: int 0..4`, `feedback: {warning: str, suggestions: [str]}`,
  `crack_times_display: {...}`.

### Score thresholds (REQ-22.1)

zxcvbn score semantics:
- 0: too guessable (< 10^3 guesses)
- 1: very guessable (< 10^6 guesses)
- 2: somewhat guessable (< 10^8 guesses)
- 3: safely unguessable (< 10^10 guesses)
- 4: very unguessable (>= 10^10 guesses)

OWASP ASVS v4 recommends score ≥ 3 for L1 applications. Klai is a
B2B SaaS holding tenant data — L1 is the correct baseline.

### Moving to model_validator (REQ-22.3)

To pass `user_inputs` (email, names, company_name) to zxcvbn, the check
must run AFTER Pydantic has populated all fields. Field-level
`@field_validator` sees only the single field. Move to
`@model_validator(mode="after")`:

```python
from pydantic import model_validator
from zxcvbn import zxcvbn

class SignupRequest(BaseModel):
    # ... existing fields ...

    @model_validator(mode="after")
    def check_password_strength(self) -> "SignupRequest":
        if len(self.password) < 12:
            raise ValueError("Wachtwoord moet minimaal 12 tekens bevatten")
        result = zxcvbn(
            self.password,
            user_inputs=[self.email, self.first_name, self.last_name, self.company_name],
        )
        if result["score"] < 3:
            raise ValueError(
                "Wachtwoord is te zwak. Kies een langer of minder voorspelbaar wachtwoord."
            )
        return self
```

### Fallback (REQ-22.4)

If zxcvbn import fails (missing dep in a broken install), wrap the
import in a guarded try/except:

```python
try:
    from zxcvbn import zxcvbn
    _ZXCVBN_AVAILABLE = True
except ImportError:
    _ZXCVBN_AVAILABLE = False
    logger.error("zxcvbn_unavailable_falling_back_to_length_check")
```

### Alternative libraries considered

- **passlib `needs_update` / `CryptContext`** — only handles hashing,
  not strength estimation. Not a fit.
- **`password_strength` on PyPI** — less maintained (last release 2021),
  simpler algorithm. Rejected in favour of zxcvbn's dictionary-aware
  scoring.
- **HIBP API integration** — scoped out (privacy SPEC, separate).

---

## §23 — Widget-config Origin header documentation

### Current state

`klai-portal/backend/app/api/partner.py:388-481` — full implementation
of `GET /partner/v1/widget-config`:

- Looks up widget by `widget_id` (line 421-422).
- Validates `Origin` header against `allowed_origins` (lines 428-437).
  Uses `origin_allowed()` from `widget_auth.py:75` — exact match or
  wildcard subdomain (`https://*.example.com`).
- Generates HS256 JWT with 1-hour TTL (lines 452-457) carrying
  `wgt_id`, `org_id`, `kb_ids`, `exp`.
- Returns CORS headers echoing the matched Origin (never `*`).

### Security posture

Downstream endpoints consuming the widget JWT:
- `POST /partner/v1/chat/completions` — requires valid JWT, scopes
  chat to `kb_ids`.
- Future widget endpoints all gate on the same JWT.

A non-browser client (curl) can spoof `Origin`, fetch widget-config,
and receive a valid JWT. But that JWT is tenant-and-kb-scoped, so the
attacker gets exactly what a legitimate widget user on an allowed
domain gets: chat access to the configured KBs, for 1 hour.

This is why Cornelis filed the finding as **PARTIAL**. The fix is
documentation, not code.

### Documentation fix

Extend the existing docstring at `partner.py:394-410` with an explicit
"UX-only" statement (REQ-23.1) and extend the `@MX:REASON` annotation
at line 397 to reference the docstring (REQ-23.3).

---

## §24 — Widget JWT per-tenant secret via HKDF

### Current state

`klai-portal/backend/app/services/widget_auth.py:20-56`:

```python
def generate_session_token(wgt_id, org_id, kb_ids, secret) -> str:
    # ... builds payload ...
    return jwt.encode(payload, secret, algorithm="HS256")
```

Every tenant's JWT is signed with the same `settings.widget_jwt_secret`.
Secret exposure = all tenants' tokens forgeable.

### HKDF-SHA256 approach (REQ-24.1)

HKDF (RFC 5869) is a key-derivation function that converts a
high-entropy master secret + a public label (info) + salt into a
pseudorandom derived key. The derived key is bound to the label —
deriving with a different `info` produces a different key.

Python stdlib: `cryptography.hazmat.primitives.kdf.hkdf.HKDF`.

```python
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

def _derive_tenant_key(master_secret: str, tenant_slug: str) -> bytes:
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,  # HS256-appropriate
        salt=b"klai-widget-jwt-v1",
        info=tenant_slug.encode("utf-8"),
    )
    return hkdf.derive(master_secret.encode("utf-8"))
```

### Why slug, not org_id? (REQ-24.1)

- **Stable:** org_id is a Postgres serial; slugs are created once and
  never change.
- **Collision-free:** enforced by the `ix_portal_orgs_slug_active`
  partial unique index (portal-backend.md § Provisioning state machine).
- **Human-debuggable:** an HKDF info of `"voys"` is greppable in logs;
  `"42"` is not.

### Salt versioning (REQ-24.1)

The constant `b"klai-widget-jwt-v1"` allows future key rotation by
bumping to `v2`. Any such bump invalidates all live tokens; coordinate
with a widget session re-issue window.

### Decode path (REQ-24.2)

`decode_session_token(token, tenant_slug, master_secret)` must derive
the same key before `jwt.decode`. Callers must look up the tenant slug
first — the flow is:

1. Parse JWT without verification to extract `wgt_id`.
2. Query `widgets` + `portal_orgs` to get `org.slug`.
3. Derive key via HKDF with the slug.
4. `jwt.decode(token, derived_key, algorithms=["HS256"])`.

Step 1 is safe because `jwt.get_unverified_claims()` / `jwt.decode(…,
options={"verify_signature": False})` has no trust implications here
— we only read `wgt_id` to look up the key.

### Live session invalidation (REQ-24.3)

Deploying this change invalidates every widget JWT currently in a
browser. Mitigation: 1-hour TTL means worst case is 1h of broken
widget chats for active users. A runbook note in the docstring tells
operators to expect this; a deliberate production deploy outside
business hours minimises user impact.

---

## §27 — `tenant_matcher` cache invalidation

### Current state

`klai-portal/backend/app/services/tenant_matcher.py:1-60`:

```python
CACHE_TTL = timedelta(minutes=5)
SCRIBE_PLANS: frozenset[str] = frozenset({"professional", "complete"})
_cache: dict[str, tuple[tuple[str, int | None] | None, datetime]] = {}

async def find_tenant(email: str) -> tuple[str, int | None] | None:
    now = datetime.now(UTC)
    if email in _cache:
        result, expires = _cache[email]
        if now < expires:
            return result
    result = await _lookup(email)
    _cache[email] = (result, now + CACHE_TTL)
    return result
```

### Gap

A plan downgrade (professional → free) takes up to 5 minutes to
propagate to scribe invite eligibility because the cached resolution
still carries the "has scribe" verdict from `_lookup` (which checks
`SCRIBE_PLANS` at lookup time).

### Options

**Option A — TTL shortening (preferred per REQ-27.1):**
- Change `CACHE_TTL = timedelta(seconds=60)`.
- Worst-case downgrade delay: 60 seconds (business-acceptable).
- Zitadel `find_user_by_email` load: 5x current (from 1 call per
  email per 5min to 1 per 60s). At current scale (~dozens of invites
  per day) this is a rounding error on Zitadel's load.

**Option B — Explicit invalidation hook:**
- Keep 5min TTL.
- Add `invalidate_cache(email: str)` function.
- Call from billing-change webhook and admin plan-update endpoint.
- More surgical but requires tracking all plan-change callsites.

**Decision:** Default to Option A. REQ-27.1 gives /run implementation
the flexibility to switch to Option B if profiling shows the Zitadel
load is unacceptable.

### Single-instance constraint

The `_cache` is in-process. Multi-replica portal-api would need Redis,
but portal-api runs 1 replica in current and near-term deployments.
Noted in the docstring per REQ-27.2.

---

## §28 — `/docs` double-gating

### Current state

`klai-portal/backend/app/main.py:167-177`:

```python
app = FastAPI(
    title="Klai Portal API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.debug else None,
)
```

`klai-portal/backend/app/core/config.py:183-197`:

```python
# Dev mode — enables Swagger UI and /openapi.json; NEVER enable in production
debug: bool = False

# Auth dev mode — bypasses Zitadel authentication for local development.
# REQUIRES debug=True as additional safeguard. NEVER enable in production.
auth_dev_mode: bool = False
auth_dev_user_id: str = ""
```

### Gap

If a deploy accidentally sets `DEBUG=true` (e.g. a typo in the
compose env), `/docs` exposes the full OpenAPI schema including every
internal endpoint path, every admin endpoint, every parameter. This
is not an auth bypass (401s still fire) but it is a reconnaissance
gift.

### Fix (REQ-28.1 / REQ-28.2 / REQ-28.3)

Two gates:

1. **Soft gate (REQ-28.1):** `docs_url` and `openapi_url` require
   `settings.debug AND settings.portal_env != "production"`. The
   OpenAPI handler returns None → 404.

2. **Hard gate (REQ-28.3):** A `@field_validator` on `debug` raises
   at app startup if `debug=True AND portal_env="production"`. The
   service refuses to start with this misconfiguration.

```python
class Settings(BaseSettings):
    portal_env: str = "production"  # conservative default
    debug: bool = False

    @field_validator("debug")
    @classmethod
    def _debug_not_in_production(cls, v: bool, info: ValidationInfo) -> bool:
        if v and info.data.get("portal_env") == "production":
            raise ValueError(
                "Refusing to start: DEBUG=true with PORTAL_ENV=production"
            )
        return v
```

Pydantic v2 `ValidationInfo.data` semantics: validators run in field-
declaration order. `portal_env` must be declared BEFORE `debug` so
`info.data["portal_env"]` is populated when the `debug` validator fires.

### Compose forwarding (REQ-28.4)

Per portal-backend.md § "portal-api uses explicit environment block":
`PORTAL_ENV: ${PORTAL_ENV:-production}` must be added to the portal-api
service in `deploy/docker-compose.yml`. Local-dev `.env` sets
`PORTAL_ENV=development`.

---

## Summary of new dependencies

| Dep | Added for | Package | License | Install size |
|---|---|---|---|---|
| zxcvbn | #22 | `zxcvbn` | MIT | ~400 KB |

HKDF for #24 uses stdlib-transitive `cryptography`, already present
(used by Fernet in signup.py).

No other new runtime dependencies.

## Summary of new config keys

| Key | Added for | Default |
|---|---|---|
| `PORTAL_ENV` | #28 | `"production"` |

No other new config.

## Summary of new structlog events

| Event | Triggered by |
|---|---|
| `signup_email_rate_limited` | REQ-19.1 |
| `signup_email_rl_redis_unavailable` | REQ-19.4 |
| `callback_url_subdomain_not_allowlisted` | REQ-20.1 |
| `tenant_slug_allowlist_cache_miss` | REQ-20.2 |
| `zxcvbn_unavailable_falling_back_to_length_check` | REQ-22.4 |

All queryable in VictoriaLogs via the `event` field.

---

# Internal-wave additions (2026-04-24)

This section documents the 21 additional items absorbed in v0.3.0. Each
finding gets a current-state synopsis and an explicit note on whether
the item lives in HYGIENE or deserves its own dedicated SPEC.

## Grouping rationale

Cornelis's external audit gave us a clean P0-P3 severity sort. The
internal wave of reviews didn't produce that sort; it produced a mix
of (a) trivial-fix hygiene, (b) latent landmines tied to specific
config-drift scenarios, (c) items that overlap with already-filed
SPECs, (d) one item (HY-46) whose blast radius cannot be bounded
without additional research.

The grouping rule is:

- **Live in HYGIENE:** trivial fixes (one-line imports, annotations,
  docstring updates), per-service defense-in-depth where the primary
  SPEC doesn't exist, and landmine items that deserve a one-PR close.
- **Live in dedicated SPEC:** structural changes that rewrite a core
  code path (MCP transport, retrieval fail-closed rate limit), items
  that are already claimed by a named SPEC (IDENTITY-ASSERT-001,
  MAILER-INJECTION-001, CORS-001).
- **Stay stub:** items whose proper REQ depends on external research
  (HY-46 → klai-docs route-handler audit).

The result: 21 items in HYGIENE, three stub/pointer entries to
dedicated follow-ups, one explicit klai-docs audit spike.

---

## klai-connector subsection

### HY-30 — `HTTPException` NameError

**Current state snapshot:** `klai-connector/app/routes/connectors.py`.

Line 5:

```python
from fastapi import APIRouter, Depends, Request
```

Lines 75, 90, 121 all execute `raise HTTPException(status_code=404,
detail="Connector not found")` with `HTTPException` undefined in the
module namespace. Python defers the lookup to the point the `raise`
statement executes, so the error is request-time, not import-time.
At request-time the uncaught `NameError` becomes a FastAPI 500 with
generic `"Internal Server Error"`.

**Why ruff F821 didn't catch this:** the rule IS project-enabled in
`klai-connector/pyproject.toml`'s ruff config — verified by reading
`klai-connector/pyproject.toml`. One of two things is happening:

1. The file was added AFTER the most recent ruff run in CI. Pre-push
   hooks are advisory, not enforced.
2. The CI ruff step excludes this path. Verify during /run.

Either way the fix is to add `HTTPException` to the import line and
add a test. The NameError would have been caught by unit tests had
any of the three affected routes had a "not found" test. Grepping
`klai-connector/tests/` for `not found` confirms the gap.

**Why this is P2 not P3:** it's technically a live 500 bug, not
hygiene. Two reasons it's still in HYGIENE:

1. The fix is one line.
2. The blast radius is tiny (three routes in one file).

Rather than spin up a new SPEC, fold it into HYGIENE-001.

**Lives in HYGIENE because:** trivial fix, one file, test coverage
already planned by REQ-30.2.

### HY-31 — `/api/v1/compute-fingerprint` dead import

**Current state snapshot:**
`klai-connector/app/routes/fingerprint.py:52-54`:

```python
# Import adapter lazily — it needs Settings which is configured at app startup.
from app.adapters.webcrawler import WebCrawlerAdapter, _extract_markdown
from app.core.config import Settings
```

Per the audit `app.adapters.webcrawler` was deleted during an earlier
cleanup. Verification method: `find klai-connector/app/adapters/ -type f`
during /run — if `webcrawler.py` is absent, HY-31 is live. If present
but empty / unused, same outcome (the lazy import may succeed but
`WebCrawlerAdapter` is not defined).

**SPEC-CRAWL-004 REQ-9 context:** the docstring says
`/api/v1/compute-fingerprint` exists for portal-admin recompute of
canary page fingerprint. Check portal's `canary` code path:

```bash
Grep "compute-fingerprint" klai-portal/backend/
```

If zero hits, option (a) in REQ-31.1 (remove endpoint) is safe. If
non-zero hits, option (b) (rewire to crawl4ai HTTP client) is required.

**Error-message leakage:** line 99 `detail=f"Crawl failed: {exc}"`
echoes the ModuleNotFoundError into the HTTP 502 body, exposing
`app.adapters.webcrawler` as an internal module name. Minor recon
value but falls under "no internal module names in error responses"
hygiene.

**Lives in HYGIENE because:** localised to one route file; either
fix is a <30-line change.

### HY-32 — No rate-limit on `/api/v1/connectors`

**Current state snapshot:**
`klai-connector/app/middleware/auth.py:100-148` defines the Zitadel
JWT check. Every authenticated request reaches the route handler
without any rate-limit layer. There IS a Caddy zone for portal-api
(`@portal-api-sensitive`) but connector service is behind a different
Caddy handler with no rate-limit entries.

**Threat model:** authenticated attacker (either legitimate tenant
user with malicious intent, or attacker who stole a valid JWT) can:

- POST unbounded `connectors` rows → DB bloat.
- GET/PUT/DELETE with fuzzed UUIDs → UUID-existence oracle (even with
  HY-30 fixed, timing differences between 404 and a valid GET's 200
  may leak which UUIDs belong to the tenant).

**Reference impl:** portal-api's
`partner_dependencies.check_rate_limit` works on Redis INCR + EXPIRE.
The same pattern ports to connector with almost zero change — it's
a function in a shared utils module.

**Key decision: `org_id` vs `remote_host`:** per-IP is useless behind
Caddy (all requests come from a small pool of Caddy IPs). Per-org_id
is the right key. Limits are generous (60 r/min GET, 10 r/min
POST/PUT/DELETE) to avoid breaking legitimate admin bulk operations.

**Fail-open on Redis outage:** consistent with the rest of the
codebase (REQ-19.4, partner-dependencies pattern). A Redis outage is
a separate alerting concern; it shouldn't block connector management.

**Lives in HYGIENE because:** port of an existing pattern, one Redis
key, one middleware, ~80 lines of code + test.

---

## klai-scribe subsection

### HY-33 — Audio path traversal latent

**Current state snapshot:**
`klai-scribe/scribe-api/app/services/audio_storage.py:30-77`
constructs paths as:

```python
audio_path = Path("/data/audio") / user_id / f"{txn_id}.wav"
```

No `.resolve().is_relative_to(base)` check. `user_id` comes from
`jwt.sub` (see HY-34) which is currently a numeric string but has no
enforced charset.

**Traversal scenarios:**

- `user_id="../../../etc/passwd"` → `/data/audio/../../../etc/passwd`
  resolves to `/etc/passwd`. File writes could clobber system files.
- `user_id="/absolute/path"` → when Path left-operand is absolute,
  `Path("/data/audio") / "/absolute/path"` RESOLVES TO
  `/absolute/path` (Python's Path absorbs absolute rhs). File writes
  escape the base dir entirely.

**Current deployment:** `/data/audio/` is a docker-mounted volume in
the scribe container. Escape would hit the container filesystem, not
the host. BUT the container runs the worker process which may have
write access to sensitive paths (e.g. `/app/` if misconfigured).

**Reference pattern:** Python 3.9+ `Path.is_relative_to()` is the
canonical check:

```python
def _safe_audio_path(base: Path, user_id: str, txn_id: str) -> Path:
    p = (base / user_id / f"{txn_id}.wav").resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError("invalid audio path")
    return p
```

**Lives in HYGIENE because:** one helper, applied across <5 call sites.

### HY-34 — Zitadel `sub` not format-validated

**Current state snapshot:**
`klai-scribe/scribe-api/app/core/auth.py:71-74`:

```python
claims = jwt.decode(token, ...)
user_id = claims["sub"]  # no validation
return AuthContext(user_id=user_id, ...)
```

Zitadel's current `sub` format is a numeric string of 19-20 digits
(e.g. `"269462541789364226"`). A future custom IdP federation or a
SAML flow might produce a string with `:`, `@`, `/`, or `..` — any of
which would cascade through to HY-33's path construction.

**Regex choice (REQ-34.1):** `^[A-Za-z0-9_-]{1,64}$`. Tolerances:

- Numeric Zitadel sub: matches.
- UUID-formatted sub (some IdPs): the `-` is allowed.
- Base64url-ish subs: `A-Z`, `a-z`, `0-9`, `_`, `-` matches.
- Anything with `/`, `.`, `@`, `:` rejects — same characters that
  enable path traversal.

**Defense-in-depth:** HY-33 and HY-34 are partners. Either alone
blocks the current exploit vector; together they provide belt-and-
suspenders.

**Lives in HYGIENE because:** one regex, one validation site.

### HY-35 — Stranded `processing` status

**Current state snapshot:**
`klai-scribe/scribe-api/app/api/transcribe.py:156-176`:

```python
record.status = "processing"
await session.commit()
try:
    transcript = await whisper.transcribe(audio_path, ...)
    record.transcript = transcript
    record.status = "complete"
except Exception:
    record.status = "failed"
finally:
    await session.commit()
```

Problem: if the worker is SIGKILLed (OOM, container restart) between
line 157 and the finally block, no flip to `failed` happens. The row
stays `processing` until human intervention.

**Reaper design:**

```python
async def reap_stranded(session: AsyncSession) -> int:
    threshold = datetime.utcnow() - timedelta(minutes=STRANDED_TIMEOUT_MIN)
    result = await session.execute(
        update(Transcription)
        .where(Transcription.status == "processing")
        .where(Transcription.started_at < threshold)
        .values(status="failed", error_reason="worker_restart_stranded")
    )
    return result.rowcount
```

Called once at worker startup (before accepting new work). Idempotent
— subsequent startups find no stranded rows.

**Timeout default:** 30 minutes. Whisper transcription of a 1-hour
audio file takes <5 minutes on the current GPU; 30 minutes is
generous. Configurable for future longer-audio workloads.

**Lives in HYGIENE because:** startup hook + one SQL update.

### HY-36 — Finalize race

**Current state snapshot:** the current order in transcribe.py
finalize is:

```python
record.audio_path = None
await session.commit()
await delete_audio(audio_path)  # may crash here
```

If the crash lands between commit and delete, the file persists but
the DB says deleted. Worse: the recovery reaper (HY-35) would see
`audio_path=None` and skip re-running delete.

**Fix (REQ-36.1):** reverse the order.

```python
await delete_audio(audio_path)   # FIRST
record.audio_path = None
await session.commit()           # SECOND
```

A crash after delete but before commit leaves the DB saying "audio
still on disk" (true) when the file is actually gone. Next access
attempt gets FileNotFoundError which bubbles up as 404 — acceptable.

**Janitor (REQ-36.2):** covers the reverse case where delete succeeds
but commit never happens. Scans `/data/audio/{user_id}/*.wav`,
cross-references with `transcriptions.audio_path`, deletes orphans
after grace window.

**Grace period:** 24 hours default. Balances orphan cleanup against
the edge case of an audio file that was just written and the DB row
not yet committed.

**Lives in HYGIENE because:** order reversal + one janitor job (~50
lines).

### HY-37 — Configurable whisper URL SSRF landmine

**Current state snapshot:**
`klai-scribe/scribe-api/app/api/health.py:20-31`:

```python
@router.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{settings.whisper_server_url}/health")
        return {"status": "ok" if resp.status_code == 200 else "error"}
    except Exception as exc:
        return JSONResponse({"status": "error", "detail": str(exc)}, 503)
```

`settings.whisper_server_url` is env-driven. Current value:
`http://whisper:8000`. If operator typos to `http://internal-admin`
or `http://169.254.169.254/` (AWS IMDS), unauthenticated `GET /health`
becomes an SSRF.

**Allowlist regex (REQ-37.1):**

```python
_WHISPER_URL_ALLOW = re.compile(
    r"^https?://(whisper|whisper-server|localhost|127\.0\.0\.1)(:\d+)?(/.*)?$"
    r"|^https?://[a-z0-9-]+\.getklai\.com(:\d+)?(/.*)?$"
)
```

Validation runs at Settings load → app refuses to start on
misconfiguration. Production safety net.

**Error response sanitisation (REQ-37.2):** detail goes to
`logger.exception(...)`, response body stays at `{"detail": "whisper
unreachable"}`. No hostname leak via the probe.

**Lives in HYGIENE because:** two small changes, both well-scoped.

### HY-38 — CORS `*.getklai.com` + credentials (docs-only)

**Current state snapshot:**
`klai-scribe/scribe-api/app/main.py:42-48`:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+\.getklai\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

The combination `allow_origin_regex` + `allow_credentials=True` is a
known CORS footgun. If a tenant domain ever serves a malicious SPA
at (say) `evil.getklai.com` (via subdomain takeover on a dangling DNS
record — see Finding #20's allowlist story), that SPA can make
credentialed XHR requests to scribe.

**Currently safe because:** scribe is NOT browser-reachable. Caddy
has no public route to scribe; all traffic is internal portal → scribe
via the `internal` docker network.

**Landmine status:** if a future frontend adds direct browser →
scribe XHR, this config becomes exploitable.

**Dedup with CORS-001:** SPEC-SEC-CORS-001 covers cross-service CORS
hardening end-to-end. HY-38 is the defense-in-depth marker so the
CORS-001 implementer knows scribe is in scope.

**Lives in HYGIENE as docs-only:** annotation + pointer; structural
fix is CORS-001's job.

---

## klai-retrieval-api subsection

### HY-39 — `/health` blocking + topology

**Current state snapshot:**
`klai-retrieval-api/retrieval_api/main.py:73-131`. Verified during
planning by reading the file.

Two distinct issues in one finding:

**Issue A — event-loop blocking:**

```python
db = FalkorDB(host=settings.falkordb_host, port=settings.falkordb_port)
db.connection.ping()  # SYNC! blocks event loop
```

The `falkordb` Python client is synchronous. Calling `.ping()` inside
an `async def` handler pauses the event loop until the TCP roundtrip
completes. Under normal conditions this is 1-2 ms. Under FalkorDB
stress, it can be 100+ ms. Caddy polls `/health` every 10 s by
default, so the blocking is periodic, not continuous — but any
concurrent request during the ping waits.

Fix per `.claude/rules/klai/lang/python.md` § asyncio.to_thread:

```python
await asyncio.to_thread(db.connection.ping)
```

Runs the sync call in the default thread pool. Zero event-loop
impact. One context-switch cost (negligible).

**Issue B — topology leak:**

Lines 83, 101, 112, 124 all have the shape:

```python
except Exception as exc:
    checks["tei"] = f"error: {exc}"
```

The `str(exc)` for `httpx.ConnectError` contains the full target URL
including internal hostname and, in the TEI case, the GPU-host IP
via docker bridge (`http://172.18.0.1:7997`). An external attacker
hitting `/health` learns the full internal topology when any
dependency is transiently unreachable.

Fix:

```python
except Exception as exc:
    checks["tei"] = "error"
    logger.warning("health_check_failed", service="tei", exc_info=True)
```

Client sees `"tei": "error"`. Observability retains the full
traceback via structlog → Alloy → VictoriaLogs.

**Lives in HYGIENE because:** both fixes are ~6 lines each, scoped
to one file.

### HY-40 — Unbounded `_pending`

**Current state snapshot:**
`klai-retrieval-api/retrieval_api/services/events.py:24`:

```python
_pending: set[asyncio.Task] = set()
```

Line 96-99 `emit_event`:

```python
task = asyncio.create_task(_emit(...))
_pending.add(task)
task.add_done_callback(_pending.discard)
```

Tasks self-clean on completion. Under normal load, `_pending` stays
near-empty. Under the pathological case where `_emit` blocks on
something slow (Redis fail-open during REQ-42 → product_events write
is slow → `_emit` hangs for seconds each), concurrent retrievals can
spawn tasks faster than they complete. No cap → OOM.

**Cap design (REQ-40.1):** simplest form is a `len(_pending) >=
MAX_PENDING` check before create_task. If over, drop + counter
increment.

**Alternative: bounded queue (REQ-40.4):** `asyncio.Queue(maxsize=N)`
+ one consumer task. This is cleaner but a bigger refactor.
/run picks whichever is less risky; the invariant (bounded memory)
is what matters.

**Prometheus counter:** `retrieval_events_dropped_total`. Fed into
Grafana alerting; an alert at rate > 0 indicates the cap is hitting.

**Lives in HYGIENE because:** small, local change with a clear cap.

### HY-41 — Log poisoning via headers

**Current state snapshot:**
`klai-retrieval-api/retrieval_api/logging_setup.py:73-79`
`RequestContextMiddleware` binds:

```python
request_id = request.headers.get("X-Request-ID", "")
org_id = request.headers.get("X-Org-ID", "")
structlog.contextvars.bind_contextvars(request_id=request_id, org_id=org_id)
```

No cap, no validation. Attacker-controlled data flows into every log
line for the duration of the request. Three classes of impact:

1. **Log storage bloat:** 10 MB request_id × N requests/sec → Alloy /
   VictoriaLogs costs.
2. **Dashboard pollution:** tenant-scoped dashboards that display
   `org_id` or `request_id` can show attacker-controlled strings.
3. **Terminal injection:** structlog's pretty-printed dev output
   doesn't escape control characters by default. ASCII escape
   sequences in a header can reposition the cursor when an admin
   tails logs.

**Fix — regex cap:**

```python
_REQ_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_ORG_ID_RE = re.compile(r"^[0-9]{1,20}$")

raw_req = request.headers.get("X-Request-ID", "")
request_id = raw_req if _REQ_ID_RE.match(raw_req) else str(uuid.uuid4())

raw_org = request.headers.get("X-Org-ID", "")
org_id = raw_org if _ORG_ID_RE.match(raw_org) else None  # drop if invalid
```

**Cross-service symmetry (REQ-41.4):** the same
`RequestContextMiddleware` exists in portal-api, connector, scribe,
mailer, research-api, knowledge-ingest. Applying the regex in only
retrieval would leave a gap — the attacker hitting a different
service still achieves log poisoning. /run greps every
`RequestContextMiddleware` and applies the same cap.

**Lives in HYGIENE because:** ~10 lines per service × 7 services =
~70 lines total, plus one ruff-caught regression test per service.

### HY-42 — Rate-limiter fails open (docs-only)

**Current state snapshot:**
`klai-retrieval-api/retrieval_api/services/rate_limit.py:69-96`:

```python
async def check_limit(redis, key, limit, window) -> bool:
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window)
        return count <= limit
    except Exception as exc:
        logger.warning("rate_limit_redis_unavailable", error=str(exc))
        return True  # fail open
```

**Why fail-open is the current design:** retrieval is on the hot path
for user queries. A Redis outage would take retrieval down with it
under a fail-closed model. Product availability > per-tenant flood
protection in this trade-off.

**Why the next audit should not re-file:** HYGIENE-001 annotates the
fail-open with an MX:WARN + REASON pointing at this SPEC AND the
future SPEC-RETRIEVAL-RL-FAILCLOSED-001. A reviewer sees the
rationale inline and doesn't re-derive.

**Secondary fix (REQ-42.2):** the current `error=str(exc)` throws
away the traceback (TRY401). Switch to `exc_info=True`. Orthogonal
to the fail-open decision.

**Lives in HYGIENE as docs-only:** no behaviour change; only
annotation + logging-level tweak.

### HY-43 — TRY antipattern in `search.py`

**Current state snapshot:**
`klai-retrieval-api/retrieval_api/services/search.py:142`:

```python
except (TimeoutError, Exception) as exc:
    logger.error("dense_search_failed", error=str(exc))
    return []
```

Same shape at lines 242 and 310.

**Why it's dead code:** `TimeoutError` is a subclass of `Exception`
in Python 3. `except (Subclass, Parent)` is identical to `except
Parent`. Ruff's `TRY` rules (if enabled) catch this.

**Why it throws away the traceback:** `error=str(exc)` produces a
bare message like `"Connection reset by peer"` with no frame info.
When this fires at 3am in production, on-call has no line number.

**Fix:**

```python
except Exception:
    logger.exception("dense_search_failed")
    return []
```

`logger.exception` is `logger.error` + `exc_info=True`. Traceback
preserved; level stays error.

If TimeoutError needs distinct handling:

```python
except TimeoutError:
    logger.warning("dense_search_timeout", exc_info=True)
    return []
except Exception:
    logger.exception("dense_search_failed")
    return []
```

**Scope:** just `search.py` for this SPEC. Other files in retrieval-
api get the same treatment but in a follow-up audit (not in scope to
avoid bloat).

**Lives in HYGIENE because:** three line changes, enforced by
existing ruff config.

### HY-44 — JWKS 20s worker-DoS landmine

**Current state snapshot:**
`klai-retrieval-api/retrieval_api/middleware/auth.py:273-275`. The
flow under `jwt_auth_enabled=False`:

1. Request arrives with `Authorization: Bearer x`.
2. Middleware checks `settings.jwt_auth_enabled` → False.
3. Middleware's short-circuit SHOULD skip auth entirely.
4. BUT the current code falls through to the JWKS-fetch branch,
   which calls `httpx.get(settings.jwks_url, timeout=10.0)`.
5. If `settings.jwks_url=""` (dev default), httpx tries to parse
   empty URL → fails / tries twice → 20 seconds of worker time.

**Why 20 s kills throughput:** ASGI worker pool is bounded (uvicorn
default 1 worker × 1 thread; retrieval-api typically runs 2-4
workers). An attacker sending 4+ concurrent `Authorization: Bearer x`
requests pins every worker for 20 s each. Throughput drops to zero
until timeouts elapse. Repeat → indefinite DoS.

**Why not exploitable today:**

- Production sets `ZITADEL_ISSUER` non-empty →
  `jwt_auth_enabled=True` (default derivation).
- Under `jwt_auth_enabled=True`, JWKS fetch is legitimate (the URL
  is valid), so a malicious Bearer just fails signature verification
  quickly.

**What breaks the defence:** one config drift where
`ZITADEL_ISSUER` is unset in production (accidentally, or via a
deploy that missed an env var). Under that drift, the 20s path is
live. Attacker probability for exploiting = very high given the path
is well-known from the source.

**Fix layers:**

- REQ-44.1: short-circuit to 401 when auth disabled + Bearer present.
  No JWKS fetch.
- REQ-44.2: fail-at-startup if `jwt_auth_enabled=True AND jwks_url=""`.
  Prevents the landmine from being latent.
- REQ-44.3: cap JWKS timeout at 3s (it's sub-second in practice; 3s
  tolerates one retry).
- REQ-44.4: 15-minute in-memory cache on JWKS response. Amortises
  the cost and blocks a slow-loris against the JWKS endpoint.

**Lives in HYGIENE because:** ~30 lines across four REQs, all local
to one middleware module.

---

## klai-knowledge-mcp subsection

### HY-45 — FastMCP DNS rebinding disabled (docs-only)

**Current state snapshot:**
`klai-knowledge-mcp/main.py:170-176`:

```python
mcp = FastMCP(
    name="klai-knowledge-mcp",
    enable_dns_rebinding_protection=False,  # ← here
    ...
)
```

**DNS rebinding 101:** an attacker hosts `evil.com` that initially
resolves to their IP. Victim visits evil.com → SPA loads. Attacker
then changes `evil.com` DNS to `127.0.0.1`. Victim's browser
re-requests `evil.com` → now talks to localhost (where MCP might
run). Browser same-origin policy considers it same-origin (same
host string). Attacker's SPA can now call local MCP tools.

Standard defence: the server validates `Host:` header against an
allowlist. `enable_dns_rebinding_protection=True` in FastMCP enables
this.

**Why off now:** MCP currently speaks stdio/unix-socket, not HTTP.
DNS rebinding is HTTP-only, so flag is irrelevant today. The flag
would become relevant if MCP ever listens on a TCP port.

**Landmine:** if a future op adds a Caddy route to MCP for remote IDE
integration, that op MIGHT not know to flip this flag. HYGIENE-001's
annotation is the signal.

**Lives in HYGIENE as docs-only:** no code change, only annotation +
Caddyfile comment.

### HY-46 — `page_path` encoding bypass (stub)

**Current state snapshot:**
`klai-knowledge-mcp/main.py:337-339`:

```python
if ".." in page_path or "\\" in page_path or page_path.startswith("/"):
    raise ValueError("invalid page_path")
```

**Bypasses:**

1. URL-encoded: `%2e%2e` → when passed to an HTTP client downstream,
   gets decoded to `..` in the URL path.
2. Fullwidth stops: `．．` (U+FF0E × 2) → NFKC-normalised to `..`.
3. Overlong UTF-8: `C0 AE C0 AE` → historical UTF-8 decoders decoded
   these to `..`. Modern Python unicode handling rejects overlong
   forms but downstream Go / Rust consumers might accept.

**Blast radius (CANNOT-VERIFY):** MCP passes `page_path` to
klai-docs. Does klai-docs sanitise? Maybe. Its route-handlers would
need a separate audit. Without that audit, HY-46's severity is
unknown. Worst case: path traversal in klai-docs → arbitrary file
read. Best case: klai-docs also validates → defence-in-depth only.

**Conservative stopgap (REQ-46.1):** apply NFKC normalisation first,
then run the existing check on the normalised form. ALSO reject if
the input contains `%` (URL encoding).

```python
import unicodedata
normalised = unicodedata.normalize("NFKC", page_path)
if "%" in page_path:
    raise ValueError("invalid page_path — URL encoding disallowed")
if ".." in normalised or "\\" in normalised or normalised.startswith("/"):
    raise ValueError("invalid page_path")
```

**Stub status:** full test matrix (every encoding variant) lives in
the follow-up SPEC. HYGIENE-001 ships the conservative rejection as
a stopgap.

**Lives in HYGIENE as stub:** full details deferred pending
klai-docs audit.

### HY-47 — No MCP rate-limit

**Current state snapshot:** MCP tools in
`klai-knowledge-mcp/main.py` are all of the form:

```python
@mcp.tool()
async def query_kb(kb_slug: str, query: str) -> list[str]:
    ...
```

No pre-check, no token bucket, no concurrency cap.

**Cost per tool:**

- `query_kb` → TEI embedding + Qdrant search + Infinity rerank → LLM.
  Per-call GPU cost: non-trivial (milliseconds of TEI + Infinity time;
  LLM tokens).
- `list_sources` → one Postgres query. Cheap.
- `get_page_content` → one HTTP call to klai-docs. Medium.

**Threat model:** authenticated-but-malicious user calls `query_kb`
at 1000 r/s → GPU saturation → legitimate users denied service.

**Fix — Redis token bucket (REQ-47.1-4):** reuse the portal-api
`check_rate_limit` helper. Key on Zitadel sub. Default limits per
REQ-47.1. JSON-RPC error on reject per REQ-47.3. Fail-open on Redis
per REQ-47.4 (same pattern as everywhere else).

**FastMCP middleware vs per-tool decorator:** FastMCP's middleware
API is the cleaner fit. If the framework doesn't expose a tool-level
middleware hook, fall back to a `@rate_limited` decorator applied to
each tool. /run picks based on FastMCP's current API.

**Full per-tenant quota system out of scope:** that's SPEC-MCP-
QUOTAS-001.

**Lives in HYGIENE because:** port of existing pattern to FastMCP.

### HY-48 — Personal-KB kb_slug guessability (docs-only)

**Current state snapshot:**
`klai-knowledge-mcp/main.py:234-243`:

```python
kb_slug = f"personal-{identity.user_id}"
```

`identity.user_id` is the Zitadel sub. Once an attacker knows the
sub (from a separate leak), they know the slug. NO membership check
runs between `identity.user_id` and `identity.org_id` elsewhere in
the MCP — this part is SPEC-SEC-IDENTITY-ASSERT-001's job.

**Why HYGIENE doesn't fix it:**

1. Changing the slug format (e.g. HMAC of user_id instead of raw)
   breaks every existing personal KB. Migration is non-trivial.
2. The real structural fix is in IDENTITY-ASSERT-001: a proper
   membership check at the assertion point, not a slug obfuscation.
3. Obfuscating the slug without fixing the membership check is
   security-theatre.

**HYGIENE's contribution:** annotation that documents (a) the
deterministic format, (b) the reliance on IDENTITY-ASSERT-001 for
the real fix.

**Lives in HYGIENE as docs-only:** pointer to the SPEC that fixes it.

---

## klai-mailer subsection (defense-in-depth)

### HY-49 — Signature error taxonomy oracle

**Current state:** `_verify_zitadel_signature` in the mailer webhook
endpoint module. Path confirmed during /run — check for this symbol
via `Grep`.

Current behaviour: four distinct error messages (missing, malformed,
timestamp too old, invalid HMAC). An attacker probing the endpoint
can distinguish each case.

**Oracle value:**

- Missing header → mailer accepts Zitadel signature flow (vs. being
  a different endpoint entirely).
- Malformed → parser exists; crafted header is at least shape-valid.
- Timestamp too old → learn the window size by binary search.
- Invalid HMAC → know the hmac check fired (worst case signal before
  collision attacks).

Individually harmless; together they reduce attacker uncertainty.

**Fix — collapsed message:** single `"unauthorized"` 401 for all
four cases. Phase info to structlog only.

**Overlap with MAILER-INJECTION-001:** that SPEC is the structural
fix for Zitadel signature verification end-to-end. HY-49 is the
specific "error-message taxonomy" angle. Kept in HYGIENE as a
belt-and-suspenders backup.

**Lives in HYGIENE because:** one function body rewrite; defense-in-
depth against MAILER-INJECTION-001 missing the detail.

### HY-50 — Permissive v-field parser (speculative, docs-only)

**Current state:** `ZITADEL-Signature: t=<timestamp>,v1=<hmac>`. The
parser splits on `,`, then on `=`, and silently ignores any field
with an unknown key prefix. Today Zitadel emits only `v1`. If
Zitadel adds `v2` in a future release, the parser would still only
verify `v1` unless the code is updated.

**Attack scenario (speculative):** Zitadel ships v2 signing with
`v1` kept as compatibility. Attacker forges `v1` (weaker) and sends
both. Mailer verifies `v1`, accepts. A strict parser would either
require all versions to verify or pick the strongest — either
defeats the downgrade.

**Why speculative:** there is no current roadmap for Zitadel v2
signing. HYGIENE files the MX:NOTE as a tripwire for the future.

**Fix:** no code change. MX:NOTE + SPEC reference.

**Lives in HYGIENE as docs-only:** speculative tripwire.

---

## Cross-cutting hygiene notes

### Structlog TRY401 sweep

HY-42 and HY-43 both touch the `str(exc)` → `exc_info=True` pattern.
A broader sweep is warranted across every service. Not all in scope
for HYGIENE-001; /run sees the pattern and may file a follow-up.

### F821 re-enablement across services

HY-30 surfaces that ruff F821 enforcement is not uniform across klai
services. Every new FastAPI service added after the ruff config baseline
needs an explicit audit. /run re-enables F821 on klai-connector as part
of REQ-30.3; a follow-up meta-SPEC should audit every service's ruff
config for consistency.

### MX tag annotations introduced in v0.3.0

| Tag type | Location | REQ |
|---|---|---|
| MX:WARN | connector create/update/delete routes | REQ-30 (if applicable) |
| MX:WARN | scribe CORS middleware | REQ-38 |
| MX:WARN | retrieval rate_limit fail-open | REQ-42.1 |
| MX:WARN | knowledge-mcp DNS-rebinding flag | REQ-45.1 |
| MX:NOTE | knowledge-mcp personal-KB slug | REQ-48.1 |
| MX:NOTE | mailer signature parser | REQ-50.1 |

All new MX:WARN tags include `@MX:REASON` per protocol.

### New runtime dependencies

None for the v0.3.0 additions. All fixes use stdlib +
already-installed packages.

### New config keys (v0.3.0)

| Key | Added for | Default |
|---|---|---|
| `SCRIBE_STRANDED_TIMEOUT_MIN` | HY-35 | `30` |
| `SCRIBE_JANITOR_GRACE_HOURS` | HY-36 | `24` |
| `RETRIEVAL_EVENTS_MAX_PENDING` | HY-40 | `1000` |
| `CONNECTOR_RL_READ_PER_MIN` | HY-32 | `60` |
| `CONNECTOR_RL_WRITE_PER_MIN` | HY-32 | `10` |
| `MCP_RL_READ_PER_MIN` | HY-47 | `60` |
| `MCP_RL_WRITE_PER_MIN` | HY-47 | `30` |

All scoped to the individual service's Settings, not global.

### New structlog events (v0.3.0)

| Event | Service | REQ |
|---|---|---|
| `connector_rate_limit_redis_unavailable` | connector | REQ-32.3 |
| `scribe_stranded_recovered` | scribe | REQ-35.2 |
| `scribe_janitor_orphan_deleted` | scribe | REQ-36.3 |
| `health_check_failed` | retrieval | REQ-39.2 |
| `retrieval_events_cap_hit` | retrieval | REQ-40.2 |

All queryable via `event:<key>` in VictoriaLogs LogsQL.

### New Prometheus counters (v0.3.0)

| Counter | Service | REQ |
|---|---|---|
| `retrieval_events_dropped_total` | retrieval | REQ-40.1 |

Scrape config already covers retrieval-api's `/metrics` endpoint —
no Alloy change.

