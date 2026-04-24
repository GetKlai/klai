# Research — SPEC-SEC-IDENTITY-ASSERT-001

Codebase analysis supporting the v0.2.0 SPEC. All line numbers verified
against tree at 2026-04-24.

---

## 1. Current trust model per service

This section documents what each service claims, what each service
verifies, and where the gap is.

### 1.1 klai-knowledge-mcp

**Identity signals the server reads:**
- `X-User-ID` header (caller-asserted; injected by LibreChat MCP config)
- `X-Org-ID` header (caller-asserted)
- `X-Org-Slug` header (caller-asserted, with `DEFAULT_ORG_SLUG` fallback)
- `X-Internal-Secret` header (service bearer; shared via SOPS `.env`)

**What is verified:**
- `X-Internal-Secret` is constant-time compared to `KNOWLEDGE_INGEST_SECRET`
  (`main.py:100-111`). When the env var is empty the check is skipped —
  this is tracked as a separate CRITICAL under SPEC-SEC-WEBHOOK-001
  (`knowledge-mcp` fail-open auth on empty secret).

**What is NOT verified:**
- No check that `X-User-ID` belongs to the org in `X-Org-ID`
- No check that `X-User-ID` matches the authenticated MCP session at all
- No cryptographic binding between the end-user's Zitadel session (in
  LibreChat) and the identity claims arriving at the MCP

**Downstream effect (M1 → D1 chain):**
- `_save_to_ingest` at `main.py:115-157` forwards `org_id` and `user_id`
  verbatim to knowledge-ingest
- `save_to_docs` at `main.py:318-433` forwards `X-User-ID` and `X-Org-ID`
  verbatim to klai-docs (`main.py:350-353`, `main.py:422-423`)
- `requireAuthOrService` at `klai-docs/lib/auth.ts:66-85` accepts these
  claims on the strength of the matching secret alone:

```typescript
if (incoming === secret) {
  const sub = request.headers.get("x-user-id");
  if (!sub) return null;
  const org_id = request.headers.get("x-org-id") ?? undefined;
  return { sub, org_id, iss: "internal-service" } as AuthPayload;
}
```

The secret holder can therefore write to any user's personal KB and any
org's docs KB by choosing the `X-User-ID` / `X-Org-ID` they want.

### 1.2 klai-scribe

**Identity signals the endpoint reads:**
- Zitadel Bearer JWT (validated via `get_current_user_id`)
- `body.org_id` in `IngestToKBRequest`

**What is verified:**
- JWT is validated for signature + issuer (standard Klai pattern)
- `user_id = Transcription.user_id` check ensures the caller owns the
  transcription (`transcribe.py:444-450`)

**What is NOT verified:**
- `body.org_id` is NOT checked against the authenticated user's org
  memberships
- A user with a valid JWT for tenant A can submit `body.org_id = <tenant B>`
  and the scribe service will happily forward the transcript to tenant B's
  knowledge-ingest pipeline

**Code path (S1):**
- `transcribe.py:434-459`: reads `body.org_id` verbatim
- `knowledge_adapter.py:21-62`: forwards `org_id` into `payload` sent to
  knowledge-ingest

The `ingest_scribe_transcript` function itself is correct (it takes
`org_id` as a typed parameter); the bug is at the caller that feeds it
untrusted data.

### 1.3 klai-retrieval-api

**Identity signals the middleware handles:**
- Zitadel Bearer JWT (preferred)
- `X-Internal-Secret` (service bearer)

**What is verified:**
- JWT callers: `verify_body_identity` at `middleware/auth.py:321-349`
  checks that `body.org_id == jwt.resourceowner`. This guard is the
  SPEC-SEC-010 REQ-3 cross-org firewall.
- Internal-secret callers: middleware sets `auth.method = "internal"`
  (`auth.py:290-295`), and then `verify_body_identity` early-returns:

```python
auth: AuthContext | None = getattr(request.state, "auth", None)
if auth is None or auth.method != "jwt":
    return
```

**The bug (R1):**
The early return is intentional — the original SPEC-SEC-010 assumed
internal callers were trusted to send correct `(org_id, user_id)` values.
The audit reveals this is false: the shared secret has too many holders
(mailer, knowledge-mcp, scribe, librechat bridge), and any of them — or
any attacker with the secret — can set arbitrary body identity fields.

**`_search_notebook` asymmetry (R2):**
`services/search.py:114-129` builds only `tenant_id` filter conditions.
Compare with `_scope_filter` at `search.py:69-111`, which for
`scope == "personal"` adds an explicit `user_id` FieldCondition. Personal
notebook chunks lack that filter, so any user in the same tenant can read
any other user's personal notebook via `/retrieve` with
`{scope: "notebook", notebook_id: <victim>}`.

**`emit_event` poisoning (R3):**
`api/retrieve.py:353-364` emits product events keyed on `req.org_id` and
`req.user_id` — the pydantic body fields. If R1 is closed via REQ-4, the
`emit_event` must switch to the verified identity (stored on
`request.state.auth`) or we have an inconsistency: the query is rejected
with the caller-claimed org, but the event emits with the caller-claimed
org too. Product-metrics dashboards would show queries attributed to
wrong tenants.

### 1.4 klai-docs

`requireAuthOrService` at `klai-docs/lib/auth.ts:66-85` is a passive
consumer: it trusts whatever `X-User-ID` / `X-Org-ID` knowledge-mcp sends.
Fix belongs upstream (REQ-2). No klai-docs code changes in this SPEC —
follow-up work can replace `requireAuthOrService` with an
`verify_via_portal` call to make the TypeScript side defensive too.

---

## 2. Proposed `/internal/identity/verify` contract

### 2.1 Request

```json
POST /internal/identity/verify
Host: portal-api:8000
X-Internal-Secret: <INTERNAL_SECRET>
X-Request-ID: <uuid propagated via get_trace_headers>
Content-Type: application/json

{
  "caller_service": "knowledge-mcp",
  "claimed_user_id": "b3a5…",
  "claimed_org_id":  "7f9c…",
  "bearer_jwt":      "eyJhbGci…"   // or null
}
```

### 2.2 Response — verified

```json
HTTP/1.1 200 OK

{
  "verified": true,
  "user_id":  "b3a5…",
  "org_id":   "7f9c…",
  "cache_ttl_seconds": 60,
  "evidence": "jwt"            // or "membership"
}
```

### 2.3 Response — denied

```json
HTTP/1.1 403 Forbidden

{
  "verified": false,
  "reason": "no_membership"    // stable code from the reject list below
}
```

Stable reason codes (for log querying):

- `unknown_caller_service` — `caller_service` not in allowlist
- `invalid_jwt` — JWT signature / audience / exp failure
- `jwt_identity_mismatch` — JWT sub/resourceowner ≠ claimed tuple
- `no_membership` — user has no active membership in claimed org
- `cache_unavailable` — Redis unreachable (HTTP 503 path)

### 2.4 Caching strategy

Cache layer sits on the *consumer* side (REQ-7.2). The consumer keys on
`(caller_service, claimed_user_id, claimed_org_id,
hash(bearer_jwt or "none"))` and caches the `VerifyResult` for 60 s.

Why consumer-side and not portal-api side?
- Portal-api cache would cache across all consumers, which is a privacy
  smell (one service's lookup reveals to a co-tenant that another service
  just looked up the same user)
- The consumer-side cache is per-process and does not cross the network
- 60 s TTL keeps revocation-propagation bounded at 60 s — acceptable for
  the threat model (revoked users are NOT active attackers; active
  attackers are blocked at JWT validation time)

### 2.5 Failure mode — FAIL CLOSED

Unlike SPEC-SEC-005 REQ-1.3 (rate limiter fails open), this verifier fails
closed. Justification: a rate limiter is an availability control (fail
open keeps traffic flowing when monitoring is degraded); an auth check is
a security control (fail open would silently turn into "everything
allowed"). The two controls have opposite optimal fail modes.

Consequences of fail-closed:
- Redis outage → all three services start returning HTTP 403 to their
  callers. Operators page. This is louder than the rate-limit case but
  that's the point — auth regressions must be loud.
- Portal-api outage → same. Services report `identity_assert_call` with
  `verified=false, reason=portal_unreachable` (a stable code we log in
  the consumer library — not a portal-generated reason).

---

## 3. LibreChat → MCP token forwarding

### 3.1 Current flow

```
LibreChat (v0.8.4+ MCP client)
  → HTTP POST /mcp
  → headers: X-User-ID, X-Org-ID, X-Org-Slug, X-Internal-Secret
  → body: MCP tool invocation
klai-knowledge-mcp (FastMCP streamable-http)
  → reads headers
  → forwards X-User-ID + X-Org-ID verbatim to knowledge-ingest / klai-docs
```

### 3.2 Target flow

```
LibreChat (patched)
  → HTTP POST /mcp
  → headers: X-User-Token (end-user Zitadel JWT),
             X-User-ID, X-Org-ID (still sent, still caller-claim),
             X-Internal-Secret
  → body: MCP tool invocation
klai-knowledge-mcp
  → reads X-User-Token + X-User-ID + X-Org-ID
  → calls portal-api /internal/identity/verify with
     (claimed_user_id=X-User-ID, claimed_org_id=X-Org-ID,
      bearer_jwt=X-User-Token)
  → only on `verified=true` does it forward the VERIFIED (not claimed)
    user_id/org_id to upstream services
```

### 3.3 Additive, not replacement

`X-Internal-Secret` stays. The end-user JWT is added alongside. This lets
the MCP migrate behind a feature flag without a coordinated LibreChat
cutover:

- Before REQ-2 lands: knowledge-mcp behaves as today (vulnerable)
- Between REQ-2 land date and LibreChat JWT-forwarding land date:
  knowledge-mcp calls verify with `bearer_jwt=null` → membership-only
  evidence → still closes the spoof
- After LibreChat JWT-forwarding lands: stronger JWT evidence

This matches the SPEC-SEC-005 philosophy of strengthening the shared-secret
model rather than replacing it — one layer at a time.

### 3.4 LibreChat patch scope (tracked separately, not this SPEC)

LibreChat's MCP client currently has no hook to attach the end-user JWT.
The patch lives in our LibreChat fork (`klai-librechat-patch`) and
tracks under a LibreChat-specific SPEC. This SPEC assumes the patch
eventually lands; the `bearer_jwt=null` fallback (REQ-2.5) ensures we are
not blocked on it.

---

## 4. Performance budget

### 4.1 Target

- Median added latency: < 20 ms per call (cache hit)
- p95 added latency: < 100 ms (cache miss)
- Cache hit rate target: > 90 % under realistic load

### 4.2 Derivation

Scribe ingest is the tightest budget because it is user-visible
("Transcript being sent to KB"). Measured baseline for
`POST /v1/transcriptions/{id}/ingest` today: ~250 ms (scribe-side +
knowledge-ingest + Qdrant). Adding 20 ms median is ~8 % overhead, within
noise.

Retrieval calls are faster (target < 500 ms total) but also more
frequent. Cache hit rate matters most here: a user in LibreChat often
issues several retrieve calls in a row, each for the same `(user_id,
org_id)`. The 60 s TTL captures every call within a typical LibreChat
turn.

Knowledge-mcp is the slowest of the three (tool invocations are
interactive and the user is watching an LLM think). The 100 ms cold-cache
budget is invisible there.

### 4.3 Instrumentation

`identity_assert_call` structlog events (REQ-7.5) carry `latency_ms` and
`cached` fields. A Grafana panel on top of VictoriaLogs gives cache hit
rate per service:

```
service:scribe
  AND event:"identity_assert_call"
  | stats count() by cached
```

If cache hit rate drops below 80 % in any consumer, the 60 s TTL needs
revisiting (or the consumer is suffering from unique `(user, org)` tuples
on every call, which is its own bug).

---

## 5. Migration order

Order is chosen to minimise incidents:

1. **REQ-1** (portal-api `/internal/identity/verify` endpoint) ships
   first. It has no consumer yet; it can sit idle in production for a
   sprint without affecting anyone.
2. **REQ-7** (shared library + contract) ships second. Consumers adopt
   the helper in a follow-up PR, so the library is ready when they want
   it. Includes a contract test against the REQ-1 endpoint.
3. **REQ-2** (knowledge-mcp) and **REQ-3** (scribe) and **REQ-4**
   (retrieval-api) can ship in any order after REQ-1 + REQ-7 are live.
   Each closes one CRITICAL and is independent.
4. **REQ-5** (`_search_notebook` user_id filter) ships standalone; it
   does not depend on REQ-1 at all — it's a local Qdrant filter fix.
   Ship as soon as the test is written.
5. **REQ-6** (`emit_event` uses verified identity) ships after REQ-4 is
   live in retrieval-api — requires the verified identity to be present
   on `request.state.auth`.

### 5.1 Rollback

Each of REQ-2/3/4 can be individually rolled back by setting a feature
flag `IDENTITY_VERIFY_MODE=off` in the service's env. The default on
rollback is the pre-SPEC behaviour (caller-asserted identity trusted).
This is explicit because a half-landed migration with portal-api down
would otherwise take all three services down.

Flag defaults:
- Before GA: `off` (pre-SPEC behaviour; for safe rollouts)
- GA: `enforce` (fail calls that cannot be verified)
- Permanent: flag removed once GA has been stable for 30 days

---

## 6. `emit_event` inventory

Grep of every Python service for `emit_event(`:

| Service | File | Current identity source | Status after SPEC |
|---|---|---|---|
| retrieval-api | `api/retrieve.py:353-364` | `req.org_id`, `req.user_id` (body) | **Switch to `request.state.auth` — REQ-6.1** |
| portal-api | `api/auth/signup.py` | session context | Already verified via JWT |
| portal-api | `api/auth/*.py` login/signup | session context | Already verified via JWT |
| portal-api | `api/billing/*.py` | session context | Already verified via JWT |
| portal-api | `api/meetings.py` | session context | Already verified via JWT |
| portal-api | `api/connectors.py` (knowledge.uploaded) | session context | Already verified via JWT |
| scribe-api | not emitted directly — goes via portal by returning data | n/a | n/a |
| research-api | `api/notebooks.py` (notebook.created/opened) | session context | Already verified via JWT |
| research-api | `api/sources.py` (source.added) | session context | Already verified via JWT |

Only retrieval-api's `emit_event` reads from the request body; every
other site already uses session-derived identity. REQ-6 is narrow by
design.

---

## 7. Open plan-phase decisions

The following decisions are deferred to `/moai plan` → implementation
design because they are architectural, not requirements:

1. **LibreChat JWT forwarding header name.** `X-User-Token` is the
   candidate in this SPEC; `Authorization: Bearer` would conflict with
   the existing `X-Internal-Secret` auth. Tied to the klai-librechat-patch
   fork — needs a joint design note.
2. **REQ-4.2 vs REQ-4.3.** Global verify-on-every-call vs split-routes.
   Trade-off: latency overhead on all calls vs caller audit + new mount
   point. Recommend REQ-4.2 as lower-cost; REQ-4.3 is the architect
   fallback if REQ-4.2 cannot meet the latency budget under realistic
   load.
3. **`notebook_visibility` flag storage.** REQ-5 needs to distinguish
   personal vs shared notebooks. Candidates: (a) a column on
   `research_notebooks` already exists — research-api's
   `api/notebooks.py` stores a `scope` field; (b) propagate to Qdrant
   payload at ingest time; (c) add a live lookup from search.py. Option
   (b) is simplest; it mirrors the `visibility` field already in
   klai_knowledge chunks.
4. **Primary-org selection when a user has multiple active memberships.**
   REQ-3.5 fast path requires a single "primary" org. Today the JWT
   `resourceowner` claim is the canonical pick; but users with cross-org
   recordings (scribe on behalf of a team outside their primary org)
   need a body-level override. Out of scope for v0.2.0; call out as a
   known limitation and ship the resourceowner path first.

---

## 8. Test-data fixtures needed

For `acceptance.md` scenarios to run in CI:

- Two test users in two orgs:
  - `user_a` (uuid `aaaa…`) in `org_x` (uuid `xxxx…`) — primary-org
    membership
  - `user_b` (uuid `bbbb…`) in `org_y` (uuid `yyyy…`) — primary-org
    membership
  - `user_a` is also a member of `org_x` only (no cross-org membership)
  - `user_b` is also a member of `org_y` only
- Zitadel JWTs for both users with valid `sub` and `resourceowner`
- A valid `INTERNAL_SECRET` value for the test environment
- `portal_org_memberships` seeded with the two memberships
- Two personal notebooks in retrieval's klai_focus collection:
  - Notebook `n_a` owned by `user_a` in `org_x`
  - Notebook `n_b` owned by `user_b` in `org_x` (same org, different
    user — this is the AC-4 fixture)

Fixture setup belongs in a shared test helper under
`klai-libs/identity-assert/tests/fixtures.py` so scribe / retrieval-api /
knowledge-mcp regression tests can all reuse it.
