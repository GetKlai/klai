---
id: SPEC-SEC-SESSION-001-research
version: 0.1.0
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
---

# SPEC-SEC-SESSION-001 — Research

Codebase analysis supporting the three findings closed by this SPEC
(#13, #15, #16 from SPEC-SEC-AUDIT-2026-04).

---

## 1. Current in-memory state shape

### 1.1 `TTLCache` class (auth.py:72-97)

A minimal in-process TTL cache shared by one consumer:
`_pending_totp = TTLCache(_TOTP_PENDING_TTL)` at line 130.

```python
class TTLCache:
    def __init__(self, ttl: int) -> None:
        self._ttl = ttl
        self._store: dict[str, dict] = {}

    def put(self, value: dict) -> str:
        token = secrets.token_urlsafe(32)
        self._store[token] = {**value, "expires_at": time.monotonic() + self._ttl}
        return token

    def get(self, token: str) -> dict | None: ...
    def pop(self, token: str) -> None: ...
```

Properties that matter for this SPEC:

- Single-process Python dict, no lock, no persistence.
- `time.monotonic()` is per-process, so even if state were shared
  across replicas the expiry check would be wrong.
- The value dict includes a caller-mutable `failures` int
  (`pending["failures"] += 1` at auth.py:487). Each replica holds its
  own counter.

### 1.2 TOTP pending-login consumer (auth.py:459-533)

Sequence:
1. `pending = _pending_totp.get(body.temp_token)` — pre-check
2. If `pending["failures"] >= _TOTP_MAX_FAILURES` → 429 and pop
3. Call Zitadel `update_session_with_totp`
4. On 400/401: `pending["failures"] += 1`, audit-log, recheck ceiling
5. On success: audit-log, `pop`, finalise and set SSO cookie

The recheck at step 4 is guarded by the same ceiling, so on the 5th
failed call the attacker sees 429 and the token is popped. But the
counter lives on ONE replica; on a different replica's cache the same
token is simply absent (→ 400 "Session expired") because `put` on
replica A never propagated to replica B. In practice the
round-robin-proxy scenario plays out differently:

- A user actually logs in and gets `temp_token=T` from replica A
  (only replica A has the record).
- The user's subsequent TOTP attempt hits replica B: 400 "Session
  expired" → user retries → proxy pins to A → A's counter increments.
  Net effect: every other attempt ends with 400 on a replica that
  doesn't have the token.

This is the first-order concern: user experience is unreliable with
in-memory state behind round-robin, and the lockout only applies on
the specific replica that happens to hold the record.

The brute-force amplification is the second-order concern: if an
attacker can deliberately target different replicas, they multiply
their attempts. Even without replica-targeting, a reset-on-restart
(`TTLCache` is lost on each deploy) means an attacker who gets within
4 failures can wait for the next deploy and try again.

### 1.3 `klai_idp_pending` cookie issue site (auth.py:1070-1095)

```python
pending_payload = json.dumps({
    "session_id": session_id,
    "session_token": session_token,
    "zitadel_user_id": zitadel_user_id,
}).encode()
encrypted_pending = _fernet.encrypt(pending_payload).decode()
# ...
response.set_cookie(
    key=_IDP_PENDING_COOKIE,
    value=encrypted_pending,
    max_age=_IDP_PENDING_MAX_AGE,  # 600 seconds
    httponly=True,
    secure=True,
    samesite="lax",
    domain=cookie_domain,
    path="/",
)
```

The payload contains only the Zitadel session factors. There is no
origin-context binding. Any cookie, valid within 10 minutes, consumed
anywhere, completes the signup and mints a full SSO cookie on the
consuming browser.

### 1.4 `klai_idp_pending` cookie consume site (signup.py:243-391)

```python
raw = _get_fernet().decrypt(klai_idp_pending.encode(), ttl=_IDP_PENDING_MAX_AGE)
pending = json.loads(raw)
session_id = pending.get("session_id", "")
session_token = pending.get("session_token", "")
zitadel_user_id = pending.get("zitadel_user_id", "")
```

No UA check, no IP check. The rest of the handler creates the Zitadel
org and mints `klai_sso` directly on the consuming response.

### 1.5 `_fernet` initialisation at auth.py:106

```python
_fernet = Fernet(
    settings.sso_cookie_key.encode() if settings.sso_cookie_key else Fernet.generate_key()
)
```

Failure modes when `SSO_COOKIE_KEY=""`:

- **Key divergence across replicas.** Each replica generates its own
  random key at process start. A cookie issued by replica A cannot
  be decrypted by replica B → users behind a round-robin proxy see
  random 401/"Session expired" errors after any restart.
- **Ephemeral keys.** Each restart rotates the key, invalidating
  every outstanding SSO cookie silently.
- **Local-dev illusion of correctness.** On a single-replica dev box
  this "works" until the first restart. The audit review pass only
  caught this because the branch exists; there is no runtime signal
  that the deployment is broken.

---

## 2. Redis schema proposal for TOTP attempts

### 2.1 Key namespace

Two keys per pending-login token:

| Key | Type | Value | TTL |
|---|---|---|---|
| `totp_pending:<token>` | HASH | `session_id`, `session_token`, `ua_hash`, `ip_subnet` | `settings.totp_pending_ttl_seconds` (default 300) |
| `totp_pending_failures:<token>` | STRING (counter) | int incremented via `INCR` | same 300s |

Two keys instead of a single hash because:

- `INCR` on a STRING is the atomic primitive. `HINCRBY` on a HASH is
  also atomic but bundles the `failures` field with the session
  token, which we want to read rarely (only on decode). Separating
  them keeps the decode path independent of the counter path.
- Deleting both keys is a single `DEL k1 k2` round-trip in Redis;
  there is no cost penalty for the split.

Alternative considered: single HASH with `HINCRBY`. Rejected because
`HINCRBY` returns the new value, which we'd immediately compare — but
then a second client's concurrent `HINCRBY` could push the value past
the ceiling in a way that depends on read-modify-write timing on the
session-token side. Separating the counter cleanly aligns the atomic
primitive with the lockout decision.

### 2.2 Key lifecycle

1. On password success (`/api/auth/login` with `has_totp=True`):
   - Generate `token = secrets.token_urlsafe(32)`
   - Compute `ua_hash`, `ip_subnet` from the login request headers
   - `HSET totp_pending:<token> session_id ... session_token ...
      ua_hash ... ip_subnet ...`
   - `EXPIRE totp_pending:<token> 300`
   - `SET totp_pending_failures:<token> 0 EX 300 NX`
   - Return `temp_token=<token>` to client.

2. On `/api/auth/totp-login`:
   - `HGETALL totp_pending:<token>` → missing? 400 Session expired.
   - Redis unavailable? 503 (fail-closed per REQ-1.7).
   - Call Zitadel `update_session_with_totp`.
   - On Zitadel 400/401:
     - `new_failures = INCR totp_pending_failures:<token>`
     - If `new_failures >= 5`: `DEL` both keys → 429 lockout.
     - Else: 400 "Invalid code, please try again".
   - On Zitadel success:
     - `DEL` both keys → 200 + SSO cookie.

### 2.3 TTL

Default 300 seconds preserves the current UX window. The TTL is now a
pydantic-settings field `totp_pending_ttl_seconds` so operations can
tune it without code changes. Redis `EXPIRE` is set on every
`HSET`/`INCR` creation; `INCR` alone does NOT set TTL, so the `SET
... EX 300` idiom or a follow-up `EXPIRE` after each `INCR` is
required. REQ-1.4 uses the simpler "set TTL at create, not per INCR"
approach — the counter cannot outlive the session it counts against,
because the session HASH expires at the same moment.

### 2.4 Why atomic `INCR` vs. read-modify-write

`INCR` is single-round-trip and single-key atomic. A naive
`failures = HGET + 1; HSET` is two round trips and has a
classic read-modify-write race between concurrent TOTP attempts on
different replicas — two attacker requests could both read
`failures=4` and both write `failures=5`, meaning they get 2 attempts
at the lockout boundary instead of 1. `INCR`'s atomicity is the
entire point of the REQ-1 change; getting it wrong re-introduces the
bug in a more subtle form.

### 2.5 Reference implementation

`klai-portal/backend/app/services/partner_rate_limit.py::check_rate_limit`
(lines 21-61) is the canonical example of Redis sliding-window
counters in this codebase. TOTP's needs are simpler — no sliding
window, just a single counter per token — so the implementation is
closer to a direct `SET NX EX 300` + `INCR` pair. Reusing
`partner_rate_limit` wholesale is not appropriate: the semantics
differ (sliding-window vs. per-token counter), and the namespace
differs (`partner_rl:` vs. `totp_pending:`).

What WE reuse: the pattern of reading `settings` for configurable
ceilings, the `fakeredis` testing approach, and the fail-closed /
fail-open decision framework.

---

## 3. Cookie-binding scheme considerations

### 3.1 User-Agent hashing

- **Hash, not raw.** Storing a raw UA string in the encrypted Fernet
  payload bloats the cookie and leaks the UA onto the client's
  machine. A SHA-256 hash is 32 bytes → 64 hex chars. With the rest
  of the payload (~200 bytes base64-encoded Fernet output) the cookie
  stays comfortably under 4 KB.
- **Empty UA tolerated.** Some API-centric clients send no
  `User-Agent`. Hashing `""` still produces a fixed-length hash; the
  check still detects a change from `""` → `Mozilla/...` and vice
  versa. A pair of "no UA" requests from the same origin will match.
- **No normalisation.** We hash the header verbatim. Browsers change
  their UA across minor version updates roughly once a quarter; the
  cookie's 10-minute TTL is short enough that mid-session version
  bumps are vanishingly rare.

### 3.2 IP subnet binding

Mobile networks are the hard case: a user starting signup on a home
Wi-Fi and switching to 4G mid-form should not be locked out. The
ecosystem has three common patterns:

1. **Exact IP match** — breaks every mobile-network switch. Rejected.
2. **/16 subnet** — tolerates most carrier-NAT drift but is too
   permissive (same /16 can span thousands of unrelated users).
3. **/24 (IPv4) and /48 (IPv6)** — middle ground. Major US/EU
   carriers generally route a user through a handful of /24 CGNAT
   pools and tend to pin the user to one for the duration of a
   "session" (minutes, not hours). IPv6 `/48` is the typical
   site-prefix boundary for residential and mobile ISPs.

We pick /24 (IPv4) and /48 (IPv6) because:

- The 10-minute cookie TTL makes the window short enough that a
  genuine carrier handoff outside the same /24 is rare.
- Mobile users inside the same carrier session stay inside the /24
  with near-certainty.
- Users crossing a major network boundary (home Wi-Fi → cellular,
  different country/carrier) will be asked to restart the signup.
  That flow is already rare at this stage (the IDP-pending cookie
  exists only for <10 minutes between Zitadel callback and the
  social-signup form submit).

Implementation: `ipaddress.ip_network(f"{ip}/24", strict=False).network_address`
(and `/48` for IPv6). Stored as a string in the Fernet payload.

### 3.3 What the binding does NOT defend against

- **Adversary with full browser control on the victim's machine.**
  They see the same UA and the same IP. Cookie binding is a replay
  defence against cookie-theft-plus-remote-replay, not a
  session-integrity defence against on-device malware.
- **Adversary sharing the victim's home network.** Same /24. Layer 2
  separation (or full passkey replacement of TOTP) is the mitigation
  for that class.

### 3.4 Why NOT extend binding to the long-lived `klai_sso` cookie

`klai_sso` lives for the full Zitadel session. Users legitimately
roam across networks for hours; binding would produce frequent
spurious logouts. Zitadel is the authority on session validity (see
auth.py comment lines 101-104). The threat model for `klai_sso` is
different (longer-lived, already validated by Zitadel on each use),
and the UX cost of binding is higher. Explicitly out of scope.

---

## 4. `signup.py::_get_fernet` as the reference pattern

```python
# signup.py:233-240 (current code)
def _get_fernet() -> Fernet:
    key = settings.sso_cookie_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Social signup not configured",
        )
    return Fernet(key.encode())
```

Properties:

- Runs per call, so no import-time side effect on an unconfigured
  deployment.
- Fails CLOSED with 503, not 500, making the intent explicit.
- Does not generate a fallback key.

What we need to change to apply this pattern in `auth.py`:

### 4.1 Proposed `auth.py` shape

```python
# auth.py — proposed replacement for line 106
@functools.lru_cache(maxsize=1)
def _get_sso_fernet() -> Fernet:
    key = settings.sso_cookie_key
    if not key:
        raise RuntimeError(
            "SSO_COOKIE_KEY is not set. "
            "Configure klai-infra SOPS-encrypted .env before starting portal-api."
        )
    return Fernet(key.encode())
```

Differences from `signup.py::_get_fernet`:

- Raises `RuntimeError` (not `HTTPException`) because the failure
  mode is "deployment misconfigured", not "individual request
  failed". `RuntimeError` at startup aborts the process (REQ-4.1).
- `lru_cache` avoids reconstructing the Fernet instance per call
  (the current module-global does this for free; we need to preserve
  that).

### 4.2 Callsite migration

Current callsites of `_fernet`:

- `_encrypt_sso` (auth.py:112) → `_get_sso_fernet().encrypt(...)`
- `_decrypt_sso` (auth.py:118) → `_get_sso_fernet().decrypt(...)`
- `idp_signup_callback` pending cookie encrypt (auth.py:1078) →
  `_get_sso_fernet().encrypt(...)`

### 4.3 Startup guard (REQ-4)

Add to the FastAPI lifespan handler (existing, in `main.py`) a
one-time call:

```python
# main.py lifespan startup, pseudocode
from app.api.auth import _get_sso_fernet
try:
    _get_sso_fernet()  # raises RuntimeError if unconfigured
except RuntimeError:
    logger.critical("sso_cookie_key_missing_startup_abort", env_var="SSO_COOKIE_KEY")
    raise
```

The structlog event fires BEFORE the `raise` re-propagates so Alloy
captures it. Without that ordering the stderr-bound raise can race
the log flush.

### 4.4 Consolidation opportunity (out of scope for this SPEC)

A natural follow-up is to move `_get_sso_fernet()` and
`_get_fernet()` into a shared `app/core/sso_crypto.py` so `auth.py`
and `signup.py` share the single source of truth. This SPEC does
NOT do that — minimal-changes rule. A refactor SPEC can extract the
shared helper once both callsites use the same pattern.

---

## 5. Where the happy-path flows must still work

### 5.1 Password + TOTP login

`POST /api/auth/login` → `temp_token` issued (now from Redis) →
`POST /api/auth/totp-login` → SSO cookie set. No client changes.
The `temp_token` surface contract is identical.

### 5.2 Social signup via IDP

`GET /api/auth/idp-signup-callback` → `klai_idp_pending` cookie set
(now with `ua_hash` + `ip_subnet` inside the Fernet payload) →
browser redirects to `/signup/social` form → user submits company
name → `POST /api/signup/social` decrypts, verifies binding,
creates org, sets `klai_sso`. If binding matches, flow is unchanged.

### 5.3 Existing IDP user login (auth.py:1054-1068)

Existing-user branch at `idp_signup_callback` does NOT use the
pending cookie (it sets `klai_sso` directly). Untouched by this
SPEC.

---

## 6. Files to change

| File | Change | Reason |
|---|---|---|
| `klai-portal/backend/app/api/auth.py` | Replace `_fernet` global with `_get_sso_fernet()`; remove `_pending_totp` global; add Redis-backed TOTP pending helpers; extend pending cookie payload with `ua_hash`+`ip_subnet`; add `_resolve_caller_ip_subnet` + `_hash_user_agent` helpers | REQ-1, REQ-2.1, REQ-3 |
| `klai-portal/backend/app/api/signup.py` | Verify `ua_hash` + `ip_subnet` on consume; no changes to `_get_fernet` (already correct) | REQ-2.2 |
| `klai-portal/backend/app/main.py` (lifespan) | Call `_get_sso_fernet()` on startup | REQ-4 |
| `klai-portal/backend/app/core/config.py` | Add `totp_pending_ttl_seconds: int = 300` setting | REQ-1.1 |
| `klai-portal/backend/tests/api/test_auth_totp_lockout.py` (new) | REQ-6.1 regression | REQ-6 |
| `klai-portal/backend/tests/api/test_idp_pending_binding.py` (new) | REQ-6.2 / REQ-6.3 regression | REQ-6 |
| `klai-portal/backend/tests/api/test_startup_sso_key_guard.py` (new) | REQ-6.4 regression | REQ-6 |

No DB migrations. No frontend changes. No infra changes (Redis is
already available).

---

## 7. Open questions (resolved during plan)

1. **Q:** Do we need a per-IP rate limit on `/api/auth/totp-login`
   (separate from the per-token counter)?
   **A:** Out of scope. The per-token counter is what closes
   finding #13. Per-IP limit lives in SPEC-SEC-HYGIENE-001 if
   prioritised.

2. **Q:** Should we migrate `_pending_totp` to Redis with a
   compatibility shim so in-flight sessions survive the deploy?
   **A:** No. 5-minute TTL. Users re-enter password. Not worth the
   shim complexity.

3. **Q:** Should the binding `ip_subnet` respect IPv4-mapped IPv6
   (`::ffff:a.b.c.d`)?
   **A:** The `ipaddress.ip_network(..., strict=False)` call handles
   this natively. IPv4-mapped addresses get a `/24` on the embedded
   v4 portion via ip_address version detection; the test suite
   should include this case.
