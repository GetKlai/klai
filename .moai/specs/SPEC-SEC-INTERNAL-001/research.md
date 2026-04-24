# Research — SPEC-SEC-INTERNAL-001

Verification pass against the klai-portal codebase on 2026-04-24. This SPEC
is a defensive hardening layer on top of SPEC-SEC-005; the research below
scopes **only** the residual paths not covered by SPEC-SEC-005 REQ-1/2/3.

## Inventory A: `_require_internal_token` implementations in klai-portal

Grep: `_require_internal_token|INTERNAL_SECRET` under
`klai-portal/backend/app/`. Two distinct implementations exist, plus call
sites in a third location.

### A.1 `klai-portal/backend/app/api/internal.py:237-258` — canonical, constant-time

Since SPEC-SEC-005 landed this site uses:

```python
async def _require_internal_token(request: Request) -> None:
    if not settings.internal_secret:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    token = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.internal_secret}"
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
    # ... rate-limit + audit-context stash
```

**Risk classification**: NONE. This is the canonical implementation. REQ-1
preserves it as the authoritative shape and propagates it to the other site.

Call sites within the same file: 12 handlers, all prefixed with
`await _require_internal_token(request)`. Lines 291, 333, 366, 435, 496,
566, 677, 709, 769, 866, 970, 1106. No action needed.

### A.2 `klai-portal/backend/app/api/taxonomy.py:399-405` — non-constant-time, REGRESSION

Current implementation:

```python
def _require_internal_token(request: Request) -> None:
    """Reject requests without the correct internal shared secret."""
    if not settings.internal_secret:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    token = request.headers.get("Authorization", "")
    if token != f"Bearer {settings.internal_secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")
```

**Comparison style**: `!=` string equality.

**Risk classification**: MEDIUM (timing side-channel).

**Impact**: The two call sites of this helper are
`list_taxonomy_nodes_internal` (taxonomy.py:423) and
`upsert_taxonomy_nodes_internal` (taxonomy.py:467). Both are reachable
from knowledge-ingest. A local attacker able to measure sub-millisecond
response-time deltas (same Docker host, same `klai-net` bridge) can
leak the secret byte-by-byte. This is the textbook string-equality
timing leak; Python's short-circuit comparison means the first mismatching
byte determines the response time.

**Fix (REQ-1.1)**: Replace the `def` with an `async def` (matches internal.py
shape) or keep sync and port the body:

```python
def _require_internal_token(request: Request) -> None:
    if not settings.internal_secret:
        raise HTTPException(status_code=503, detail="Internal API not configured")
    token = request.headers.get("Authorization", "")
    expected = f"Bearer {settings.internal_secret}"
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

`hmac.compare_digest` on equal-length strings runs in constant time (scans
both strings to completion regardless of the first mismatch). On unequal
lengths it runs a dummy compare to still avoid the length side-channel.

**Secondary observation**: the taxonomy.py sites do NOT run the
SPEC-SEC-005 rate-limit + audit plumbing because those live in
`internal.py._require_internal_token` specifically. Bringing taxonomy.py
into SPEC-SEC-005 coverage is explicitly OUT of scope for this SPEC (it
would expand the endpoint surface of SEC-005); REQ-1 only fixes the
timing leak. Audit trail for taxonomy internal endpoints is a separate
backlog item, not this SPEC.

### A.3 Call sites (summary)

- `internal.py`: 12 call sites, all `await _require_internal_token(request)` —
  constant-time since SPEC-SEC-005.
- `taxonomy.py`: 2 call sites (`taxonomy.py:423`, `taxonomy.py:467`), both
  plain `_require_internal_token(request)` — non-constant-time, REQ-1 fix.
- No other file in `klai-portal/backend/app/` defines or calls a function
  by this name. The stub mentioned `auth.py` but grep confirms auth.py
  does NOT have its own `_require_internal_token` — it is a Zitadel
  token check, a different code path.

## Inventory B: `exc.response.text` log sites (potential header reflection)

Grep: `exc\.response\.text` under `klai-portal/backend/app/`. 24 hits in
3 files. Each hit is classified by whether the error body plausibly
contains request-header values.

### B.1 `klai-portal/backend/app/api/auth.py` (22 hits)

All 22 sites log Zitadel Management API / OIDC error responses. Zitadel
echoes the original request payload back in its error bodies (standard
gRPC-gateway behaviour). When portal-api proxies a login that includes a
pre-auth hint header, the header value can round-trip through Zitadel's
error response and land in the log line.

| Line | Context | Risk |
|---|---|---|
| 192 | `resp_text = exc.response.text` then membership check | MEDIUM — raw resp_text flows into logger.warning one frame down |
| 324 | `find_user_id_by_email failed ... %s` | LOW-MEDIUM — body contains email query param |
| 376 | `create_session failed ... %s` | MEDIUM — body may contain session-create params |
| 485 | `update_session_with_totp failed ... %s` | HIGH — TOTP code appears in error bodies when invalid |
| 561 | `sso finalize failed ... %s` | MEDIUM — SSO state |
| 579 | `register_user_totp failed ... %s` | MEDIUM |
| 600 | `verify_user_email failed ... %s` | LOW |
| 621 | `verify_user_totp failed ... %s` | HIGH — same as line 485 |
| 645 | `start_passkey_registration failed ... %s` | MEDIUM |
| 667 | `verify_passkey_registration failed ... %s` | MEDIUM |
| 687 | `register_email_otp failed ... %s` | MEDIUM |
| 704 | `remove_email_otp failed ... %s` | MEDIUM |
| 712 | `register_email_otp (resend) failed ... %s` | MEDIUM |
| 728 | `verify_email_otp failed ... %s` | MEDIUM |
| 753 | `create_idp_intent failed ... %s` | LOW |
| 789 | `create_session_with_idp_intent failed ... %s` | MEDIUM |
| 881 | `idp finalize_auth_request failed ... %s` | MEDIUM |
| 921 | `create_idp_intent (signup) failed ... %s` | LOW |
| 964 | raw `exc.response.text` as positional arg | MEDIUM |
| 979 | raw `exc.response.text` as positional arg | MEDIUM |
| 1008 | raw `exc.response.text` as positional arg | MEDIUM |
| 1031 | raw `exc.response.text` as positional arg | MEDIUM |

**Aggregate risk for auth.py**: Zitadel does NOT natively echo the
`X-Internal-Secret` header (it is stripped at the Caddy boundary before
reaching Zitadel, and portal-api sends the INTERNAL_SECRET to Zitadel via
PAT Authorization, not via X-Internal-Secret). However, other secrets
pass through auth.py handlers — session tokens, TOTP codes, passkey
challenges — and any of these could reflect back in a Zitadel 4xx
response body.

**Conclusion**: REQ-4 sanitization is the right mitigation. The
sanitizer's secret list SHALL include `settings.portal_api_zitadel_pat`,
`settings.session_secret`, and every other Settings field in the
authentication domain, not only `INTERNAL_SECRET`.

### B.2 `klai-portal/backend/app/services/docs_client.py` (2 hits)

| Line | Context | Risk |
|---|---|---|
| 104 | `exc.response.text[:500]` — already truncated but not sanitized | LOW — docs-app is internal, but the client injects `X-Internal-Secret` via `get_trace_headers()` and docs-app may echo request body on 4xx |
| 138 | Same shape as 104 | LOW |

**Conclusion**: REQ-4 applies. Existing `[:500]` truncation coincidentally
matches the proposed `max_len: int = 512` default; the sanitizer wraps
the existing truncation safely.

### B.3 Risk classification summary

- HIGH (2 sites): TOTP code reflection in auth.py:485, auth.py:621
- MEDIUM (16 sites): session/challenge/SSO state reflection
- LOW (6 sites): email or public-id reflection only

REQ-4 covers all 24 sites uniformly via the codemod in REQ-4.4. No
per-site risk-based differentiation in the mitigation — the sanitizer
is cheap enough to run everywhere.

### B.4 Additional audit scope (see HISTORY amendment notice)

The concurrent audits on klai-scribe, klai-mailer, klai-focus,
klai-connector, klai-retrieval-api, and klai-knowledge-mcp may surface
additional `exc.response.text` sites with the same risk profile. If so,
REQ-4 SHALL be amended in-place to include those services — not a new
SPEC — because the sanitizer utility ships as a shared library.

## Inventory C: BFF proxy header blocklist gap

`klai-portal/backend/app/api/proxy.py:51-68`:

```python
_HOP_BY_HOP: Final[frozenset[str]] = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
    "host", "cookie", "authorization",
    "content-length",
})
```

`_build_upstream_headers` at lines 113-121:

```python
def _build_upstream_headers(request: Request, session: SessionContext) -> dict[str, str]:
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        headers[k] = v
    headers["Authorization"] = f"Bearer {session.access_token}"
    return headers
```

**Gap**: `x-internal-secret` is not in `_HOP_BY_HOP`. Every inbound
header that is NOT in the set is forwarded. Upstream services
(scribe-api, docs-app) explicitly read `X-Internal-Secret` —
see `klai-retrieval-api/retrieval_api/middleware/auth.py:264`
(`request.headers.get("x-internal-secret")`) as the equivalent shape for
retrieval-api. scribe-api and docs-app follow the same pattern.

**Attack sequence** (2-step):

1. Attacker controls a portal-frontend session (legitimate user).
2. Attacker crafts a fetch/XHR from the browser to `/api/scribe/foo`
   with `X-Internal-Secret: <guess>`. The browser permits this
   because same-origin; portal-api receives it.
3. `_build_upstream_headers` copies `X-Internal-Secret` into the
   upstream request (not filtered) and then sets `Authorization` on top.
4. scribe-api sees BOTH `X-Internal-Secret: <guess>` AND `Authorization:
   Bearer <valid>`. Depending on the middleware precedence in scribe-api,
   a correct `X-Internal-Secret` guess could bypass the Bearer-token
   check and reach internal-only endpoints.

**Impact assessment**: This is a header-injection amplifier rather than
a direct secret leak. The attacker still has to guess the secret (32+
random bytes), so brute force is infeasible; but combined with a different
leak (e.g. log reflection via B.1 above before sanitization lands, or
the taxonomy.py timing leak from A.2), the proxy becomes the relay that
turns a portal-frontend session into an internal-API foothold.

**Fix (REQ-3)**: strip the header at the proxy. Two-part fix:

1. Add the literal names to `_HOP_BY_HOP` (REQ-3.1).
2. Add a regex catch-all for future header names (REQ-3.2).

The regex is deliberately conservative — `(?i)^(x-)?(klai-internal|internal-auth|internal-token)`
matches `X-Klai-Internal-Whatever`, `Internal-Auth-Foo`, etc., but does
NOT match `X-Request-ID` or `X-Forwarded-For` (both legitimate).

## Inventory D: `/internal/librechat/regenerate` FLUSHALL

`klai-portal/backend/app/api/internal.py:1022-1034`:

```python
try:
    redis_client = aioredis.Redis(
        host=settings.redis_host,
        port=6379,
        password=settings.redis_password or None,
        decode_responses=True,
    )
    async with redis_client:
        await redis_client.flushall()
    logger.info("Redis FLUSHALL completed")
except RedisError as exc:
    logger.warning("Redis FLUSHALL failed: %s", exc)
    errors.append(f"redis-flushall: {exc}")
```

**Impact**: `flushall()` on the Redis instance used by portal-api. Given
that the same Redis instance is shared across:

- SPEC-SEC-005 rate-limit keyspace (`internal_rl:*`, `partner_rl:*`)
- Session/SSO cache (`_sso_cache` — partial, per portal-security.md moving to Redis)
- LibreChat config cache (target of invalidation)
- Any ad-hoc Redis usage from LiteLLM hooks

…`FLUSHALL` blows away EVERYTHING, not only the LibreChat yaml cache.
Observable side-effects:

- Rate limit counters reset — partner API callers get a free burst.
- SSO cache entries dropped — in-flight SSO flows error out for end users.
- LiteLLM hook caches invalidated — first request per tenant after a
  regenerate pays the cold-cache latency tax.

None of these rise to a security finding on their own. Combined with the
endpoint being a high-privilege "fix the whole system" button, the
collateral damage is disproportionate to the advertised contract ("regenerate
per-tenant LibreChat yaml").

### LibreChat Redis cache shape

From `.claude/rules/klai/platform/librechat.md`:

> `librechat.yaml` is cached in Redis with no TTL when `USE_REDIS=true`.

LibreChat (upstream) uses `keyv` as its cache abstraction. The default
namespace for `keyv` in LibreChat is `configs` — keys look like
`configs:librechat-config` and possibly
`configs:librechat-config:<tenant>` depending on the per-tenant override
scheme.

**Verification (at implementation time, not here)**:

```bash
docker exec redis redis-cli --scan --pattern 'configs:*' | head
docker exec redis redis-cli --scan --pattern 'configs:librechat*' | head
```

If the pattern diverges from `configs:*` (e.g. LibreChat upstream renames
it to `keyv:configs:*` in a future version), the SPEC REQ-2.3 introduces
`settings.librechat_cache_key_pattern` so the operator can track the
upstream rename without a code-side change.

**Fix (REQ-2)**: Replace `flushall()` with a SCAN + UNLINK loop scoped to
the configured pattern:

```python
cursor = 0
deleted = 0
while True:
    cursor, keys = await redis_client.scan(
        cursor=cursor,
        match=settings.librechat_cache_key_pattern,
        count=100,
    )
    if keys:
        deleted += await redis_client.unlink(*keys)
    if cursor == 0:
        break
logger.info("librechat_cache_invalidated", pattern=pattern, deleted_count=deleted)
```

`UNLINK` is non-blocking (asynchronous free) whereas `DEL` blocks the
event loop per key; since we may face thousands of keys after a multi-week
run, `UNLINK` is the safer primitive.

## Inventory E: rate-limit fail-mode

`klai-portal/backend/app/api/internal.py:99-143` (from SPEC-SEC-005):

```python
async def _check_rate_limit_internal(caller_ip: str) -> None:
    redis_pool = await get_redis_pool()
    if redis_pool is None:
        structlog_logger.warning("internal_rate_limit_redis_unavailable", ...)
        return   # fail OPEN
    try:
        allowed, retry_after = await check_rate_limit(...)
    except Exception:
        structlog_logger.warning("internal_rate_limit_redis_unavailable", ...)
        return   # fail OPEN
    ...
```

**Current behaviour**: unconditional fail-open on Redis unavailability.

**SEC-005 REQ-1.3 rationale**: availability of internal traffic is more
important than rate-limit coverage during a Redis outage. Valid in
isolation; but when the INTERNAL_SECRET is suspected compromised AND
Redis happens to be down, this becomes a free-for-all.

**Fix (REQ-5)**: make it configurable. Production defaults to closed so
that a suspected-secret-compromise + Redis-outage scenario is not a
silent bypass. Staging keeps open for developer ergonomics.

## Relationship with SPEC-SEC-005 — no overlap, no duplication

| Aspect | SPEC-SEC-005 | SPEC-SEC-INTERNAL-001 |
|---|---|---|
| `_require_internal_token` constant-time | internal.py only | taxonomy.py (new) |
| Rate limit primitive | introduces `_check_rate_limit_internal` | unchanged; REQ-5 modifies fail-mode branch |
| Audit trail | introduces `portal_audit_log` writes | NOT extended (out of scope) |
| Rotation runbook | `klai-infra/INTERNAL_SECRET_ROTATION.md` | NOT edited (REQ-7.3 references it for new env var) |
| FLUSHALL in regenerate | not addressed | REQ-2 |
| BFF proxy header blocklist | not addressed | REQ-3 |
| `exc.response.text` log reflection | not addressed | REQ-4 |
| ast-grep regression | not addressed | REQ-6 |

Four entirely new REQ groups (REQ-2, REQ-3, REQ-4, REQ-6), two that
extend SPEC-SEC-005 in-place without duplication (REQ-1 adds a site,
REQ-5 adds a branch). REQ-7 is the explicit dependency statement.

## Open questions (tracked, not blocking)

- Should the sanitizer also redact hash-like substrings (32-byte hex, 64-byte
  base64) as a generic "looks like a secret" catch? LEFT OUT of REQ-4 to avoid
  redacting legitimate UUIDs and request IDs; revisit if a concurrent audit
  surfaces a secret that is NOT a Settings field.
- Should REQ-2 keep a fallback `FLUSHALL` behind an admin-only runbook
  command (not HTTP-reachable)? NO — the protocol-client approach in
  `docker-socket-proxy.md` rule "talk the service's native protocol" means
  operators can run `redis-cli FLUSHALL` directly if they truly need to.
  No HTTP surface should call `FLUSHALL` again, ever.
- Should the BFF proxy REQ-3 regex be centralised in a constants file for
  sharing with knowledge-ingest / scribe-api if they ever add their own
  BFF-like surface? Deferred; one consumer today.

---

## Internal-wave additions (2026-04-24)

The concurrent audits of klai-mailer, klai-connector, klai-scribe, and
klai-knowledge-mcp completed on 2026-04-24. Every portal-finding shape
(`!=` token compare, silent-empty-string auth, `resp.text[...]` log
reflection, persisted error-body) has at least one sibling occurrence
elsewhere. The inventories below are verified-exhaustive at v0.3.0 draft
time; re-run the grep commands at implementation time to catch drift.

### Inventory F: inbound shared-secret compare sites (all services)

Scope: every `if <header> != <expected>:`, `if <header> == <expected>:`,
or `hmac.compare_digest(<header>, <expected>)` where one operand is a
settings field matching `(?i)(secret|internal_token|bearer_token|api_key)`.

| Service | File:line | Variable | Operator | Risk |
|---|---|---|---|---|
| portal-api | `app/api/internal.py:237-258` | `settings.internal_secret` | `hmac.compare_digest` | NONE (canonical) |
| portal-api | `app/api/taxonomy.py:399-405` | `settings.internal_secret` | `!=` | MEDIUM — Finding A2 |
| mailer | `app/main.py:182` | `settings.internal_secret` | `!=` | HIGH — Finding 6 |
| mailer | `app/main.py:81` | `settings.webhook_secret` (via hmac expected) | `hmac.compare_digest` | NONE (already constant-time) |
| connector | (none) | — | — | connector is a SOURCE of outbound calls only; no inbound shared-secret route |
| scribe-api | `scribe-api/app/middleware/internal_auth.py` (if present) | verify at impl time | verify at impl time | verified-clean at draft time (no inbound `!=`) |
| knowledge-mcp | (none) | — | — | auth is Bearer JWT via `mcp-auth-middleware`; shared-secret inbound is not in scope |

**Conclusion**: REQ-1 targets exactly two production sites —
`taxonomy.py:399` (v0.2.0) and `mailer/app/main.py:182` (v0.3.0). REQ-6's
ast-grep rule is the regression-proofing; it runs against every service
tree to catch a future copy-paste regression in scribe-api or
knowledge-mcp, not just portal-api + mailer.

### Inventory G: `resp.text[...]` / `exc.response.text[...]` sites across all klai Python services

Scope: grep `response.text|resp.text` across `klai-portal`, `klai-mailer`,
`klai-connector`, `klai-scribe`, `klai-knowledge-mcp`. Includes log
sites, user-facing return sites, and persisted-column sites.

| Service | File:line | Slice | Destination | Severity |
|---|---|---|---|---|
| portal-api | `app/api/auth.py` (22 lines — see Inventory B.1) | full | structlog | MEDIUM (v0.2.0) |
| portal-api | `app/services/docs_client.py:104, 138` | `[:500]` | structlog | LOW (v0.2.0) |
| mailer | `app/main.py:65-66` (if/when logging Zitadel error bodies) | `[:200]` | structlog | MEDIUM — Finding 10 |
| mailer | `app/portal_client.py` (locale lookup error paths) | `[:200]` | structlog | LOW |
| connector | `app/services/sync_engine.py:530-539` | `[:500]` | `sync_runs.error_details` JSONB + portal UI | MEDIUM — Finding 12 (persisted) |
| connector | `app/services/portal_client.py` (error paths in `get_connector_config`, `report_sync_status`) | `[:200]` | structlog | LOW |
| scribe-api | `app/services/providers.py:115-125` | `[:200]` | structlog | MEDIUM — Finding 10 |
| scribe-api | `app/services/knowledge_adapter.py` (post-raise error paths) | full | structlog via `raise_for_status` traceback | LOW |
| knowledge-mcp | `main.py:360-365` | `[:200]` | structlog | MEDIUM — Finding 10 |
| knowledge-mcp | `main.py:430-431` | `[:300]` | **MCP tool return value → chat UI** | HIGH — Finding 11 (user-visible) |

**Total**: 32 distinct sites across five services. All are covered by
either REQ-4 (sanitize before logging), REQ-8 (no echo to user-facing
response), or REQ-10 (sanitize before persistence). The high-severity
Finding 11 site moves to REQ-8 because sanitizing a secret-redacted body
is not sufficient — a chat UI should never receive the body at all.

### Inventory H: conditional header injection (`if settings.X_SECRET:`) — silent-empty-string fallbacks

Scope: grep for `if settings\..*secret:` and `if .*_SECRET:` that guard
an httpx header injection. Each site is a latent bypass if both the
outbound and inbound sides misconfigure to the empty string.

| Service | File:line | Header | Pattern | Fix REQ |
|---|---|---|---|---|
| connector | `app/clients/knowledge_ingest.py:117-119` | `x-internal-secret` | `if self._internal_secret:` | REQ-9.3 |
| connector | `app/services/portal_client.py:49, 52` | `Authorization: Bearer <secret>` | no guard — returns `Bearer ""` on empty | REQ-9.3 |
| scribe-api | `app/services/knowledge_adapter.py:53-54` | `X-Internal-Secret` | `if settings.knowledge_ingest_secret:` | REQ-9.4 |
| knowledge-mcp | `main.py:144-145` | `X-Internal-Secret` | `if KNOWLEDGE_INGEST_SECRET:` | REQ-9.5 |
| knowledge-mcp | `main.py:350-354` + `420-425` | `X-Internal-Secret` | unconditional — sends `DOCS_INTERNAL_SECRET` even if empty | REQ-9.5 (add startup guard) |
| mailer | `app/portal_client.py` (if present — verify at impl time) | `Authorization: Bearer <portal_internal_secret>` | TBD at impl time | REQ-9.2 |

**Fix pattern**: REQ-9 flips the default. Instead of "skip the header if
empty", services refuse to start when the value is empty. Silent
degradation is eliminated because the misconfiguration becomes a boot
error rather than a silent 401 / 401-bypass.

### Inventory I: error-body persisted columns

Scope: grep for writes to database columns named `error_details`,
`last_error`, `error_message`, or similar, where the value is an
`exc.response.text` slice.

| Service | Table.column | Source | REQ |
|---|---|---|---|
| connector | `connector.sync_runs.error_details` (JSONB) | `klai-connector/app/services/sync_engine.py:537` | REQ-10.1 |
| connector | same, forwarded to portal via `report_sync_status(error_details=...)` | `portal_client.py` | REQ-10.4 |
| portal-api | `portal_audit_log.details` (JSONB) — SEC-005 audit rows | Already sanitized per SEC-005 REQ-2 | no action |
| portal-api | `connectors.last_error` (TEXT) — if present at impl time | verify at impl time | REQ-10.2 |
| portal-api | `billing_runs.error_message` (TEXT) — if present at impl time | verify at impl time | REQ-10.2 |
| mailer | (none — mailer is stateless) | — | — |
| scribe-api | (none — scribe persists via knowledge-ingest, not its own error column) | — | — |
| knowledge-mcp | (none — stateless MCP server) | — | — |

**Primary target**: connector's `sync_runs.error_details`. This is the
only confirmed persisted-body column at draft time. The portal-api
columns need a per-table verification pass during implementation; if
they exist and are plausibly written with upstream body text, REQ-10.2
applies.

### Shared library proposal: `klai-libs/log-utils/`

**Motivation**: The v0.2.0 SPEC scoped `sanitize_response_body` to a
portal-api-internal module (`app/utils/response_sanitizer.py`). With five
services now needing the same utility, three options were considered:

1. **Duplicate per-service** — copy-paste the file into each service.
   Rejected: divergence within a release cycle is certain, and the
   secret-scan regex needs to stay in sync across all consumers.
2. **PyPI-published internal package** — publish to an internal PyPI
   index. Rejected for v0.3.0: overkill for a monorepo with five
   consumers, and requires CI-side PyPI-index plumbing that does not
   exist yet.
3. **Monorepo path dependency** (chosen) — `klai-libs/log-utils/` is
   a sibling directory to the service directories, consumed via
   `log-utils = { path = "../klai-libs/log-utils" }` in each service's
   `pyproject.toml`. Docker build contexts already include the monorepo
   root, so no image-build plumbing changes.

**Package layout** (v0.1.0 at ship time):

```
klai-libs/
  log-utils/
    pyproject.toml
    log_utils/
      __init__.py           # re-exports sanitize_response_body, extract_secret_values, verify_shared_secret
      sanitize.py           # core sanitize_response_body implementation
      settings_scan.py      # extract_secret_values(settings_obj) -> set[str]
      verify.py             # verify_shared_secret(header_value, configured) -> bool (REQ-1.7)
    tests/
      test_sanitize.py
      test_settings_scan.py
      test_verify.py
```

**Public API surface** (frozen at v0.1.0):

- `sanitize_response_body(exc_or_response: Union[httpx.Response, httpx.HTTPStatusError, None], *, max_len: int = 512, secret_values: set[str] = frozenset()) -> str`
- `extract_secret_values(obj: Any) -> set[str]` — scans any object for
  attributes matching the secret-field regex; accepts a
  `pydantic-settings.BaseSettings` instance, a module, or a dict
- `verify_shared_secret(header_value: str, configured: str) -> bool`
- `sanitize_from_settings(settings_obj: Any, exc_or_response: ...) -> str`
  convenience wrapper combining the above two

**Consumer wiring**: each service adds a thin wrapper (see spec.md
REQ-4.4) that closes over its own Settings instance so call sites can
stay short:

```python
# klai-connector/app/core/sanitize.py
from log_utils import sanitize_from_settings
from app.core.config import Settings

_settings = Settings()

def sanitize(exc_or_response):
    return sanitize_from_settings(_settings, exc_or_response)
```

**Rollout ordering**:

1. Land `klai-libs/log-utils/` with tests (no consumers yet).
2. Wire into portal-api first (replaces the v0.2.0-planned
   `app/utils/response_sanitizer.py`). Verify no regressions.
3. Wire into connector (fixes REQ-10 at the same time).
4. Wire into mailer, scribe-api, knowledge-mcp in parallel (they share
   no files with each other, so worktree-per-service is fine).
5. Each service gets its own PR referencing this SPEC ID.

---

## Internal-wave open questions

- Should `verify_shared_secret` (REQ-1.7) accept an `expected_prefix`
  param so Bearer-token checks can pass only the raw token and let the
  helper prepend `Bearer ` itself? Adds convenience; leaves ambiguity
  at call sites. Deferred — keep the helper shape minimal.
- Should `extract_secret_values` respect a hypothetical future
  `SecretStr` pydantic field type? `SecretStr.get_secret_value()` is
  the correct accessor; the scan needs to handle that branch. Added
  to the v0.1.0 implementation plan; not a separate REQ.
- Should knowledge-mcp's module-level globals (no Settings class) be
  migrated to a proper `pydantic-settings` class as part of REQ-9?
  Doing so makes the startup validator trivial; doing so also enlarges
  this SPEC's diff. Deferred to a future cleanup; REQ-9.5 lands a
  module-level `assert` instead.
- Should sync_engine's `error_details` JSONB schema formalise with a
  Pydantic model so the sanitizer is invoked at model-validation time,
  not at write time? Better long-term; out of scope for v0.3.0.
  Tracked as a follow-up under SPEC-SEC-AUDIT-2026-04.

