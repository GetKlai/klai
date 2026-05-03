---
id: SPEC-SEC-IDENTITY-ASSERT-001
version: 0.4.0
status: done
created: 2026-04-24
updated: 2026-04-28
author: Mark Vletter
priority: critical
tracker: SPEC-SEC-AUDIT-2026-04
---

# SPEC-SEC-IDENTITY-ASSERT-001: Verify Caller-Asserted Identity on Service-to-Service Calls

## HISTORY

### v0.4.0 (2026-04-29) — SHIPPED, audit close-out
- All REQs merged to main as part of SPEC-SEC-AUDIT-2026-04 closure sweep.
- Merge commits on origin/main: 20f004ce, 1c86c603, c5b3d0d9, c4421146
- Verified by re-audit (see SPEC-SEC-AUDIT-2026-04 v1.0.0 mission-1 results).

### v0.4.0 (2026-04-28) — Phase B + C + D landed; SPEC done

All four phases delivered to `main` and live on core-01:

- **Phase B (PR #190, 2026-04-28)** — REQ-2 (knowledge-mcp) + REQ-2.6
  endpoint+library extension. Knowledge-mcp now consumes
  `klai-libs/identity-assert`, drops the caller-asserted
  X-User-ID/X-Org-ID/X-Org-Slug forwarding, reads end-user JWT from
  `Authorization: Bearer`, and forwards verified identity to
  knowledge-ingest and klai-docs. The Phase A `/internal/identity/verify`
  endpoint and library were extended with `claimed_org_slug` input +
  canonical `org_slug` output (new deny code `org_slug_mismatch`).

- **Phase C (PR #192, 2026-04-28)** — REQ-3 (scribe). Scribe's
  `POST /v1/transcriptions/{id}/ingest` no longer accepts `org_id` in
  the request body — the tenant is derived from the authenticated JWT's
  `resourceowner` claim. No portal verify call: scribe already validates
  JWT signatures locally, so resourceowner is cryptographically authentic.
  Schema-level closure of the S1 finding.

- **Phase D (PR #193, 2026-04-28)** — REQ-4 + REQ-6 (retrieval-api +
  emit_event). Internal-secret callers no longer bypass the
  body-identity guard: `verify_body_identity` is now async and calls
  portal-api `/internal/identity/verify` for any internal-secret caller
  whose body carries a user_id, with required `X-Caller-Service` header.
  `emit_event` in `api/retrieve.py` sources tenant_id and user_id from
  `request.state.verified_caller`, never from the request body.

**Findings closed**:

| Finding | Severity | Phase | Closure |
|---|---|---|---|
| M1 + D1 (knowledge-mcp + klai-docs spoof chain) | CRITICAL | B | Verify-before-forward in knowledge-mcp; verified identity flows downstream |
| S1 (scribe body.org_id cross-tenant write) | CRITICAL | C | `org_id` removed from `IngestToKBRequest` schema |
| R1 (retrieval-api internal-secret bypass) | CRITICAL | D | `verify_body_identity` async + global verify per REQ-4.2 |
| R2 (`_search_notebook` user_id filter) | HIGH | A | Symmetric filter + ingest payload + retrieval guard |
| R3 (emit_event poisoning) | MEDIUM | D | Sources tenant from `request.state.verified_caller` |

**Decisions revised during implementation** (versus original spec):

- `IDENTITY_VERIFY_MODE` rollback flag dropped (Phase B sparring): the
  library already fails closed on portal outage and `git revert` is the
  standard rollback. Adding a flag would have shipped the spoof
  primitive as a configurable option — exactly what this SPEC closes.
- REQ-2.5 JWT-refresh retry on `invalid_jwt` dropped (Phase B sparring):
  a `bearer_jwt=None` membership-only fallback is *weaker* security
  than a JWT one. Token refresh races are a LibreChat responsibility,
  tracked under `klai-librechat-patch`.
- Scribe: REQ-3.5 fast path is the only path (Phase C). Scribe validates
  JWT signatures locally — no portal verify call needed for the
  JWT-derived org_id.

**Live verification** (2026-04-28 on core-01):

- knowledge-mcp + scribe-api + retrieval-api containers running stable
  since deploy (no restart loops, no startup errors)
- End-to-end verify call from knowledge-mcp container: 26.14 ms latency,
  matching `request_id` propagated to portal `identity_verify_decision`
  log
- End-to-end verify call from retrieval-api container: 9.24 ms latency
- Library invariants preserved: hashed user_id in logs, denials never
  cached, `KNOWN_CALLER_SERVICES` allowlist matches portal allowlist
- Cross-service trace correlation working via `X-Request-ID`
- Scribe schema closure verified live: `IngestToKBRequest.fields ==
  ['kb_slug']`

Total tests across all 4 phases: **180+ passing** (Phase A 83, Phase B
+38, Phase C +8, Phase D +13, plus regression coverage).

Status: **done**. Follow-ups (klai-docs `requireAuthOrService` rewrite,
`klai-librechat-patch` JWT-refresh proactivity) tracked separately and
no longer urgent — every upstream caller now forwards verified identity.

### v0.3.0 (2026-04-27) — Phase A landed

Phase A delivered via PR #178 on `feature/SPEC-SEC-IDENTITY-ASSERT-001`:

- **REQ-1** — `POST /internal/identity/verify` endpoint shipped on portal-api.
  Service layer (`app/services/identity_verifier.py`) and Redis cache layer
  (`app/services/identity_verify_cache.py`) split for unit-testability. JWT
  validation reuses Zitadel JWKS via an independent `PyJWKClient`. 27 tests:
  20 endpoint + 5 contract + 2 cache-evidence-isolation.
- **REQ-5** — `_search_notebook` symmetric-with-knowledge filter + ingest
  payload + endpoint guard. Three-service touch (klai-focus ingest,
  klai-retrieval-api search, klai-retrieval-api endpoint). 17 tests:
  6 new + 11 unchanged scope_filter.
- **REQ-7** — `klai-libs/identity-assert/` shared library with
  `IdentityAsserter`, in-process LRU cache (60 s TTL), structlog telemetry,
  fail-closed contract. 39 tests.
- **Backfill script** — `klai-focus/research-api/scripts/backfill_notebook_visibility.py`
  added so historical klai_focus chunks (pre-REQ-5) get the new payload
  fields applied before retrieval starts filtering them out.
- **Architectural decisions resolved** during Phase A:
  - End-user JWT forwarding header: `Authorization: Bearer <jwt>` (matches
    `klai-retrieval-api/middleware/auth.py` precedent).
  - REQ-4 path: REQ-4.2 (global verify) when REQ-4 ships in Phase B —
    `/admin/retrieve` split is YAGNI until a true admin/diagnostic caller
    appears. SPEC REQ-4.5 forbids the admin-flag-on-internal-secret hybrid.
  - `notebook_visibility` storage: Qdrant payload field, value mirrors
    `Notebook.scope` ∈ {"personal", "org"} — no translation layer.
- **Contract drift caught and fixed** during Phase A: the library originally
  sent the shared INTERNAL_SECRET in a custom `X-Internal-Secret` header,
  but portal-api's `/internal/*` surface uses `Authorization: Bearer
  <secret>` per `_require_internal_token`. The end-to-end contract test
  caught this before merge; library now uses the correct header.

Phase A is independently revertable per service via the
`IDENTITY_VERIFY_MODE=off` flag documented in research.md §5.1. Status moves
from `draft` to `in-progress` because the SPEC has consumers but is not
yet fully delivered (REQ-2/3/4/6 outstanding, see Phase B/C/D in
`progress.md`).

### v0.2.0 (2026-04-24)
- Expanded from stub into full EARS-format SPEC with research.md + acceptance.md
- Inventoried every call site where a service-to-service call carries a tenant
  or user identity that the receiving service currently trusts without proof
- Confirmed three CRITICAL findings share one root pattern: the shared service
  bearer (`INTERNAL_SECRET`, `KNOWLEDGE_INGEST_SECRET`, `DOCS_INTERNAL_SECRET`,
  `PORTAL_CALLER_SECRET`) is mistakenly treated as proof of tenant identity
- Added REQ-1 (portal-api identity-assertion endpoint with a 60-second cache),
  REQ-2 (knowledge-mcp must not forward caller-asserted headers verbatim),
  REQ-3 (scribe ingest derives `org_id` from authenticated JWT + membership
  lookup), REQ-4 (retrieval-api internal-secret path re-asserts (user_id,
  org_id) or splits into two distinct code paths), REQ-5 (`_search_notebook`
  adds `user_id` filter symmetric with `_search_knowledge`), REQ-6 (`emit_event`
  uses verified identity, not caller-supplied body fields), REQ-7 (shared
  library location + contract documentation)
- Added LibreChat token-forwarding migration path as additive to existing
  `INTERNAL_SECRET` — the service bearer stays, a signed end-user assertion is
  added alongside it
- Added performance budget and 60-second identity-cache TTL to keep per-call
  latency under ~20 ms at the receiving service

### v0.1.0 (2026-04-24)
- Stub created from internal-audit wave on klai-knowledge-mcp, klai-scribe,
  klai-retrieval-api
- Priority P0 — three independent CRITICAL findings share one root pattern
- Expand via `/moai plan SPEC-SEC-IDENTITY-ASSERT-001`

---

## Findings addressed

| # | Service | Finding | Severity | Reference |
|---|---|---|---|---|
| M1 | knowledge-mcp | `X-User-ID` / `X-Org-ID` / `X-Org-Slug` headers accepted caller-asserted, forwarded verbatim to knowledge-ingest and klai-docs | CRITICAL | [main.py:71-97](../../../klai-knowledge-mcp/main.py#L71), [main.py:348-355](../../../klai-knowledge-mcp/main.py#L348) |
| S1 | scribe | `POST /v1/transcriptions/{id}/ingest` accepts `org_id` in request body with no membership verification | CRITICAL | [transcribe.py:424-459](../../../klai-scribe/scribe-api/app/api/transcribe.py#L424) |
| R1 | retrieval-api | Internal-secret callers skip `verify_body_identity`; `/retrieve` returns data for caller-supplied `org_id` without entitlement check | CRITICAL | [auth.py:287-295](../../../klai-retrieval-api/retrieval_api/middleware/auth.py#L287), [auth.py:335-339](../../../klai-retrieval-api/retrieval_api/middleware/auth.py#L335) |
| R2 | retrieval-api | `_search_notebook` scopes only to `tenant_id`; no `user_id` check for personal notebooks | HIGH | [search.py:114-129](../../../klai-retrieval-api/retrieval_api/services/search.py#L114) |
| R3 | retrieval-api | `emit_event` called with caller-supplied `tenant_id` and `user_id` — product_events integrity depends on those fields being trustworthy | MEDIUM | [retrieve.py:353-364](../../../klai-retrieval-api/retrieval_api/api/retrieve.py#L353) |
| D1 | klai-docs | `requireAuthOrService` trusts `X-User-ID` / `X-Org-ID` when `X-Internal-Secret` matches | CRITICAL (chain with M1) | [auth.ts:66-85](../../../klai-docs/lib/auth.ts#L66) |

Root pattern: **a service-to-service call carries an `org_id` or `user_id`
claim, and the receiving service trusts that claim on the strength of the
shared `INTERNAL_SECRET` alone.** The secret proves *network identity* (the
caller is one of our services) — not *tenant identity* (the caller is acting
for a specific user/org).

The most dangerous chain is M1 → D1: a user holding a valid LibreChat session
can invoke the knowledge-mcp with `X-User-ID: <victim_uuid>` in their own
LibreChat headers (LibreChat forwards caller-controlled headers), and both
knowledge-ingest and klai-docs will honour the spoofed identity because the
`X-Internal-Secret` matches.

---

## Goal

Every service-to-service call within klai that carries a tenant or user
identity MUST either (a) derive that identity from a trusted token the caller
presents (Zitadel JWT, signed portal assertion), or (b) verify the claimed
identity against a source of truth (`portal_users` / `portal_org_memberships`
via portal-api's `/internal` lookup) before acting on it.

A shared service bearer (`INTERNAL_SECRET`, `KNOWLEDGE_INGEST_SECRET`,
`DOCS_INTERNAL_SECRET`, `PORTAL_CALLER_SECRET`) is a proof of *network
identity*, not tenant identity. Services MUST stop conflating the two.

---

## Success Criteria

- `klai-knowledge-mcp` no longer forwards caller-supplied `X-User-ID` /
  `X-Org-ID` headers to upstreams. Identity is resolved from a Zitadel-signed
  token that the MCP client (LibreChat) presents, or from a portal-api
  assertion call.
- `klai-scribe` `/v1/transcriptions/{id}/ingest` derives `org_id` from the
  authenticated JWT's owning org (lookup via portal-api) rather than accepting
  a body field.
- `klai-retrieval-api` internal-secret path verifies that the requested
  `org_id` / `user_id` matches a trusted assertion from portal-api; OR
  separates "service-call requiring no identity" from "service-call acting on
  behalf of user X" into two code paths with distinct auth semantics.
- `klai-retrieval-api` `_search_notebook` adds a `user_id` filter when the
  notebook scope is personal, symmetric with `_search_knowledge`.
- `emit_event` in retrieval-api and other Python services uses the verified
  identity stored on `request.state.auth`, never a caller-supplied body field.
- A shared library (`klai-libs/identity-assert/` or equivalent) exposes
  `verify_identity(caller_service, claimed_user_id, claimed_org_id, …)` so
  the three services share exactly one implementation of the check.
- portal-api exposes `POST /internal/identity/verify` (or equivalent) that
  takes a `(caller_service, claimed_user_id, claimed_org_id, authenticated_jwt
  or None)` tuple and returns allow/deny with the canonical resolved identity
  and a short TTL cache hint.
- Regression tests cover each finding (see `acceptance.md`).

---

## Environment

- **Services in scope:**
  - `klai-knowledge-mcp` (Python 3.13, FastMCP over streamable-http) — the MCP
    server LibreChat agents call for personal/org/docs knowledge saves
  - `klai-scribe/scribe-api` (Python 3.13, FastAPI) — transcription service
    with a `/v1/transcriptions/{id}/ingest` endpoint that pushes recordings
    into the KB
  - `klai-retrieval-api` (Python 3.13, FastAPI) — RAG retrieval service with
    `X-Internal-Secret` bypass for service callers
  - `klai-portal/backend` (Python 3.13, FastAPI) — source of truth for
    `portal_users` / `portal_org_memberships`; owner of the new
    `/internal/identity/verify` endpoint
  - `klai-docs` (Next.js 15, TypeScript) — consumer of `X-User-ID` /
    `X-Org-ID` via `requireAuthOrService`; depends on M1 being fixed upstream
    in knowledge-mcp
- **Files in scope:**
  - [klai-knowledge-mcp/main.py](../../../klai-knowledge-mcp/main.py)
    — `_get_identity` (lines 71-97), `_validate_incoming_secret` (100-111),
    `_save_to_ingest` (115-157), `save_personal_knowledge` (215-247),
    `save_org_knowledge` (267-298), `save_to_docs` (318-433)
  - [klai-scribe/scribe-api/app/api/transcribe.py](../../../klai-scribe/scribe-api/app/api/transcribe.py)
    — `IngestToKBRequest` (424-427) and `ingest_transcription_to_kb`
    (434-459)
  - [klai-scribe/scribe-api/app/services/knowledge_adapter.py](../../../klai-scribe/scribe-api/app/services/knowledge_adapter.py)
    — `ingest_scribe_transcript` (21-62), which forwards `org_id` into
    knowledge-ingest
  - [klai-retrieval-api/retrieval_api/middleware/auth.py](../../../klai-retrieval-api/retrieval_api/middleware/auth.py)
    — `AuthMiddleware.dispatch` (257-318), `verify_body_identity` (321-349)
  - [klai-retrieval-api/retrieval_api/services/search.py](../../../klai-retrieval-api/retrieval_api/services/search.py)
    — `_scope_filter` (69-111), `_search_notebook` (114-129)
  - [klai-retrieval-api/retrieval_api/api/retrieve.py](../../../klai-retrieval-api/retrieval_api/api/retrieve.py)
    — `emit_event("knowledge.queried", …)` call at line 353-364
  - [klai-docs/lib/auth.ts](../../../klai-docs/lib/auth.ts)
    — `requireAuthOrService` (66-85) — read-only consumer; fix is upstream
  - New file: `klai-portal/backend/app/api/internal.py` — add
    `/internal/identity/verify` endpoint
  - New library: `klai-libs/identity-assert/` (Python) — `verify_identity`
    helper + typed result object
- **Infra coupling:**
  - portal-api on `klai-net` (reachable by every service listed above)
  - Redis already available to every Python service via `get_redis_pool()` —
    used to cache identity-assertion results per tuple
  - `INTERNAL_SECRET` continues to be the transport-layer gate; this SPEC adds
    a second layer on top, it does NOT replace the shared-secret model

## Assumptions

- portal-api can serve as the source-of-truth for `user.org_memberships`
  without unacceptable latency. A 60-second per-tuple cache is acceptable
  (same TTL argument as SPEC-SEC-HYGIENE-001 REQ-27 for tenant_matcher).
- LibreChat / upstream MCP clients can be updated to forward the end-user
  Zitadel JWT alongside the service bearer. Where that is not yet wired,
  knowledge-mcp falls back to portal-api's `/internal/identity/verify`, which
  accepts the tuple `(caller_service, claimed_user_id, claimed_org_id, None)`
  and decides on membership evidence only. The fallback is narrower than the
  JWT path (no stronger-than-membership check) but still closes the spoof.
- For scribe, the ingest flow can accept a lookup round-trip (adds ~20 ms
  per ingest) without noticeable UX impact. Ingest is a user-initiated action
  and does not happen in a tight loop.
- `INTERNAL_SECRET` remains the primary network-level authentication for all
  service-to-service calls throughout the lifetime of this SPEC. mTLS or
  per-service credentials are Out of Scope (see SPEC-SEC-005 and future
  SPEC-SEC-INTERNAL-001 amendments).
- The retrieval-api internal-secret path serves two caller classes today:
  (a) pure admin/diagnostic calls that do not need user identity (health,
  warm-up, batch scripts), and (b) calls that act on behalf of a specific
  user (LibreChat → retrieve). This SPEC codifies the split.
- Redis is available for the identity-assertion cache. If Redis is
  unavailable the verifier MUST fail CLOSED (reject the call) — unlike the
  SPEC-SEC-005 rate limiter which fails OPEN, because an auth-class check
  must not degrade into "pass everything through".
- The shared library location `klai-libs/identity-assert/` follows the
  `klai-libs/image-storage/` precedent in SPEC-SEC-SSRF-001: one guard, three
  Python consumers, no drift.

---

## Out of Scope

- Mutual TLS between services (separate future infra SPEC; tracked alongside
  SPEC-SEC-005 mTLS out-of-scope note).
- Replacing the shared `INTERNAL_SECRET` with per-service credentials.
- Zitadel-signed service-to-service tokens (future if SPEC-SEC-005 /
  SPEC-SEC-INTERNAL-001 converge on per-service identity).
- Retroactively migrating klai-docs' `requireAuthOrService` helper to issue
  its own portal-api verify call; fixing knowledge-mcp (the only current
  upstream) closes the attack, and klai-docs can adopt the helper in a
  follow-up.
- Auth changes to research-api / klai-focus — that service is FROZEN per the
  tracker's governance note and not currently reachable in compose.
- Per-endpoint audit logging on identity-assertion outcomes; SPEC-SEC-005
  REQ-2 already installs `portal_audit_log` for every `/internal/*` call,
  which includes the new `/internal/identity/verify` endpoint.

---

## Threat Model

Three adversary scenarios drive the requirements:

### Scenario 1: Malicious internal-secret holder (M1 + D1)

An attacker controls an authenticated LibreChat session. LibreChat forwards
the `X-Internal-Secret` (shared for the whole cluster) and injects
user-controlled values into `X-User-ID` / `X-Org-ID` / `X-Org-Slug` via the
MCP config. The attacker sets `X-User-ID` to the victim's UUID and
`X-Org-ID` to the victim's org ID. knowledge-mcp forwards these values
verbatim in `X-User-ID` / `X-Org-ID` headers to klai-docs on every
`save_to_docs` call. `requireAuthOrService` in klai-docs accepts the
assertion because the secret matches.

Impact: write into victim's personal KB, arbitrary org's documentation KB,
bypass of `checkKBAccess(kb, userId)` which would otherwise reject
cross-user personal-KB writes.

Mitigation: REQ-2 (knowledge-mcp stops forwarding caller-asserted headers)
AND REQ-1 (portal-api identity-verify gate that knowledge-mcp calls before
forwarding to klai-docs).

### Scenario 2: Stolen internal-secret (S1 + R1)

An attacker obtains `INTERNAL_SECRET` from a leaked env file or compromised
container. With it they can post directly to
`POST /v1/transcriptions/{id}/ingest` on scribe (S1) OR to
`POST /retrieve` on retrieval-api (R1) with arbitrary `org_id` / `user_id`
values in the body. Scribe has no membership check; retrieval-api's
`verify_body_identity` explicitly returns early when
`auth.method != "jwt"` (auth.py:336), so the internal-secret path bypasses
the guard entirely.

Impact: read any tenant's chunks from the retrieval Qdrant index; inject
transcripts into any tenant's KB (by claiming to be a scribe instance for
that tenant).

Mitigation: REQ-3 (scribe ingest uses authenticated JWT not body field) and
REQ-4 (retrieval-api internal-secret path either re-asserts via portal-api
or splits into no-identity-needed vs on-behalf-of-user code paths). The
former is adjacent work for SPEC-SEC-005 (secret rotation shrinks the
window). This SPEC closes the "secret alone = tenant access" primitive.

### Scenario 3: Cross-tenant write via knowledge-mcp (M1 only)

A legitimate LibreChat user is tricked (via prompt injection against a
shared LibreChat agent) into producing an MCP `save_org_knowledge` call
with the `X-Org-ID` of a different tenant. Today, the MCP server accepts
the tenant the LibreChat client asserts — and the LibreChat config
forwards this value directly from the agent session context, which prompt
injection can influence.

Impact: writes into an unrelated org's knowledge base, poisoning RAG
output for every user in that org.

Mitigation: REQ-2 rejects the call when the asserted `X-Org-ID` does not
belong to the LibreChat user (resolved from the user's Zitadel JWT or via
portal-api verify). Prompt injection can still influence what the user
types, but it cannot force a cross-tenant write.

### Explicit non-goals for the threat model

- Defending against an attacker who has a valid Zitadel JWT for the victim
  user. This is the Zitadel auth boundary — out of scope here.
- Defending against portal-api being compromised. portal-api is the
  source-of-truth by definition; protecting its integrity belongs to
  SPEC-SEC-005 + SPEC-SEC-INTERNAL-001.
- Protection against a compromised LibreChat instance emitting valid
  end-user JWTs for arbitrary victims. That requires per-user-per-service
  signed assertions and is a SPEC-SEC-INTERNAL-001 amendment, not this SPEC.

---

## Requirements

### REQ-1: Portal-api identity-assertion endpoint

The system SHALL expose a portal-api endpoint that accepts a claimed
identity from a calling service and returns an authoritative allow/deny
decision, backed by a short TTL cache.

- **REQ-1.1:** WHEN a service POSTs to
  `POST /internal/identity/verify` on portal-api with a JSON body
  `{"caller_service": "<service>", "claimed_user_id": "<uuid>",
  "claimed_org_id": "<uuid>", "bearer_jwt": "<jwt-or-null>"}`,
  THE endpoint SHALL either (a) return HTTP 200 with
  `{"verified": true, "user_id": "<uuid>", "org_id": "<uuid>",
  "cache_ttl_seconds": 60, "evidence": "jwt"|"membership"}` when the
  claimed identity matches a valid, current `(user, org)` membership,
  OR (b) return HTTP 403 with `{"verified": false, "reason": "<stable_code>"}`
  when the claim cannot be substantiated.
- **REQ-1.2:** THE endpoint SHALL require both the existing
  `X-Internal-Secret` header (as for every other `/internal/*` endpoint)
  AND a `caller_service` body field from the reject-list of recognised
  services (`knowledge-mcp`, `scribe`, `retrieval-api`, `connector`,
  `mailer`). Unknown `caller_service` values SHALL return HTTP 400 with
  `reason="unknown_caller_service"` AND SHALL NOT count toward the portal
  rate limit — mis-configured services must be loud, not silenced.
- **REQ-1.3:** WHEN `bearer_jwt` is present, THE endpoint SHALL validate
  it via the existing portal Zitadel validator AND SHALL require
  `claimed_user_id == jwt.sub` AND
  `claimed_org_id == jwt.resourceowner`. IF either mismatch THE endpoint
  SHALL return HTTP 403 with `reason="jwt_identity_mismatch"`. The
  `evidence` field in the success response SHALL be `"jwt"` in this case.
- **REQ-1.4:** WHEN `bearer_jwt` is null, THE endpoint SHALL verify that
  `claimed_user_id` has an active membership in `claimed_org_id` in
  `portal_org_memberships`. On match THE `evidence` field SHALL be
  `"membership"`. On no match THE endpoint SHALL return HTTP 403 with
  `reason="no_membership"`.
- **REQ-1.5:** THE endpoint SHALL cache the `(caller_service,
  claimed_user_id, claimed_org_id, evidence)` tuple in Redis for 60
  seconds after a successful verification. Cache misses trigger a fresh DB
  read; cache hits SHALL NOT trigger a JWT signature re-check (the JWT's
  own `exp` is shorter than 60s is untrue — JWTs are longer; the 60 s
  bound is the caller-side worst case for revocation propagation).
- **REQ-1.6:** IF Redis is unreachable, THE endpoint SHALL fail CLOSED
  (return HTTP 503 with `reason="cache_unavailable"`). Unlike the
  SPEC-SEC-005 rate limiter which fails open, an auth-class decision MUST
  NOT degrade into "pass everything through" under degraded
  infrastructure. Calling services SHALL handle HTTP 503 by refusing the
  upstream write — the same safe fallback they use for any network error.
- **REQ-1.7:** THE endpoint SHALL emit a structlog line at level `info`
  with stable key `event="identity_verify_decision"` AND fields
  `caller_service`, `claimed_user_id_hash`, `claimed_org_id`,
  `verified` (bool), `reason` (on deny), `evidence` (on allow). User IDs
  SHALL be hashed (same pattern as `_hash_sub` in retrieval-api's
  `middleware/auth.py`) to avoid UUID enumeration via log inspection.
- **REQ-1.8:** THE endpoint SHALL NOT accept revoked JWTs. WHEN JWT
  validation fails (expired, invalid signature, wrong audience), THE
  endpoint SHALL return HTTP 403 with `reason="invalid_jwt"` AND SHALL
  NOT fall back to the membership path — an invalid JWT is a strictly
  stronger signal than an absent JWT and must not be weakened.

### REQ-2: klai-knowledge-mcp — remove caller-asserted header trust

The system SHALL stop forwarding caller-asserted identity headers to
upstreams from the knowledge-mcp server. Identity SHALL come from a
verified source before any upstream call is made.

- **REQ-2.1:** WHEN a tool call arrives at `save_personal_knowledge`,
  `save_org_knowledge`, or `save_to_docs` in `main.py`, THE server
  SHALL extract the end-user Zitadel JWT from the request (header name
  `X-User-Token` or `Authorization: Bearer` — to be finalised in plan)
  AND SHALL call portal-api `/internal/identity/verify` with
  `(caller_service="knowledge-mcp", claimed_user_id, claimed_org_id,
  bearer_jwt)` BEFORE invoking `_save_to_ingest` or `klai-docs` PUT.
- **REQ-2.2:** IF `/internal/identity/verify` returns deny, THE tool call
  SHALL return an error string to the MCP client and SHALL NOT invoke any
  upstream service. The error message to the client SHALL NOT include the
  reason code verbatim (prevents information leakage); logs SHALL include
  the reason.
- **REQ-2.3:** WHEN knowledge-mcp calls klai-docs (the `save_to_docs`
  flow), THE request SHALL carry the *verified* `X-User-ID` / `X-Org-ID`
  values from the portal response, NOT the caller-asserted values. The
  `DOCS_INTERNAL_SECRET` header is unchanged.
- **REQ-2.4:** THE `_get_identity` helper at `main.py:71-97` SHALL be
  renamed to `_get_claimed_identity` to reflect that it returns an
  *unverified* claim. Every call site SHALL pair it with a verification
  step before using the returned values. A `# SEC: asserted — must verify
  before use` inline comment SHALL guard the dataclass definition so
  future readers cannot miss the semantics.
- **REQ-2.5:** WHEN the LibreChat client forwards an `X-User-Token` that
  has already expired, THE MCP server SHALL call the fallback path
  `/internal/identity/verify` with `bearer_jwt=null` AND the claimed
  user/org tuple. The verify endpoint's `evidence="membership"` response
  is acceptable as fallback — it still proves the user is entitled to the
  tenant. This keeps the MCP usable during LibreChat token refresh races.
- **REQ-2.6:** WHEN any of the three tools is called with a claimed
  `org_slug` that does not match the verified `org_id`'s canonical slug,
  THE server SHALL reject the call with an error AND SHALL NOT fall back
  to `DEFAULT_ORG_SLUG`. The current fallback at `main.py:79-85` SHALL be
  removed — it silently downgrades identity when a header is missing,
  which is exactly the pattern this SPEC is designed to eliminate.

### REQ-3: klai-scribe — derive org_id from authenticated JWT

The system SHALL derive `org_id` for transcription ingest from the
authenticated end-user JWT + portal-api membership lookup, not from a
request body field.

- **REQ-3.1:** THE `IngestToKBRequest` model at
  `klai-scribe/scribe-api/app/api/transcribe.py:424-427` SHALL be changed
  to drop the `org_id` field. The new body SHALL carry only `kb_slug`
  (plus any future non-identity fields). Existing callers sending a body
  with `org_id` SHALL be ignored at validation time
  (`model_config = ConfigDict(extra="ignore")`) during a one-sprint
  transition window, after which `extra="forbid"` SHALL be enabled.
- **REQ-3.2:** WHEN `POST /v1/transcriptions/{txn_id}/ingest` is called,
  THE handler SHALL call portal-api `/internal/identity/verify` with
  `caller_service="scribe"`, `claimed_user_id=user_id` (from JWT),
  `claimed_org_id=<looked-up-primary-org-of-user>`,
  `bearer_jwt=<the_jwt>`. The looked-up primary org SHALL come from a
  new portal-api lookup helper `/internal/users/{user_id}/primary-org`
  OR SHALL be derived from the JWT's `resourceowner` claim (preferred —
  zero extra roundtrip).
- **REQ-3.3:** THE `ingest_scribe_transcript` function at
  `klai-scribe/scribe-api/app/services/knowledge_adapter.py:21-62` SHALL
  continue to take `org_id` as a parameter (the function contract is
  correct); only the caller (`transcribe.py:434-459`) SHALL change to
  pass the verified value instead of `body.org_id`.
- **REQ-3.4:** IF the authenticated user has no active org membership
  in portal, THE endpoint SHALL return HTTP 403 with
  `{"detail": "no_active_org_membership"}` AND SHALL NOT invoke
  `ingest_scribe_transcript`.
- **REQ-3.5:** WHEN the JWT's `resourceowner` claim is present AND the
  user has exactly one active membership whose `org_id` equals
  `resourceowner`, THE handler MAY skip the portal-api verify call for
  this single scenario — the JWT alone is evidence enough. This is the
  "fast path" to stay within the 20 ms latency budget. All other cases
  (no resourceowner, multiple memberships, cross-org JWT) go through
  `/internal/identity/verify`.

### REQ-4: klai-retrieval-api — internal-secret path re-asserts or splits

The system SHALL stop allowing internal-secret callers to bypass the
body-identity guard. Either the guard MUST run on every call, OR the
internal-secret surface MUST be split into two distinct auth classes.

- **REQ-4.1:** THE `verify_body_identity` function at
  `klai-retrieval-api/retrieval_api/middleware/auth.py:321-349` SHALL
  apply to ALL callers, not only JWT callers. THE early return at
  `auth.py:336-337` (`if auth.method != "jwt": return`) SHALL be removed.
- **REQ-4.2:** FOR internal-secret callers, THE guard SHALL call
  `/internal/identity/verify` on portal-api with
  `caller_service=<from_caller_header>`, `claimed_user_id=body.user_id`,
  `claimed_org_id=body.org_id`, `bearer_jwt=null`. The caller service
  identifier SHALL come from a required `X-Caller-Service` header sent
  by every internal caller (LibreChat bridge, knowledge-mcp proxy,
  etc.). Missing or unknown `X-Caller-Service` SHALL fail closed with
  HTTP 400 `missing_caller_service`.
- **REQ-4.3:** ALTERNATIVELY (architect choice during plan phase), THE
  internal-secret path MAY be split into two mount points:
  `/admin/retrieve` (no user context, requires admin role in the
  internal call) and `/retrieve` (requires the `X-Caller-Service` +
  `X-End-User-Id` contract described in REQ-4.2). In that case, the
  retrieval-api internal-secret acceptance SHALL be gated per-route.
- **REQ-4.4:** WHEN portal-api `/internal/identity/verify` returns deny
  for a retrieval call, THE middleware SHALL return HTTP 403 with
  `{"error": "identity_assertion_failed"}` AND SHALL NOT execute the
  retrieve query. The `verified` metric at
  `middleware/auth.py` (`cross_org_rejected_total`) SHALL increment on
  every deny with a `reason=identity_assertion_failed` label.
- **REQ-4.5:** THE admin-role exemption currently at
  `middleware/auth.py:338-339` (JWT callers with `role="admin"` skip the
  guard) SHALL continue to apply for JWT callers. It SHALL NOT be
  extended to internal-secret callers (there is no "admin" concept in the
  internal-secret world — that's exactly the conflation this SPEC fixes).
- **REQ-4.6:** THE `identity_verify_decision` log entries emitted by
  portal-api (REQ-1.7) plus the retrieval-api middleware decision SHALL
  share the same `request_id` via `get_trace_headers()` propagation, so
  one `request_id:<uuid>` query in VictoriaLogs shows the full chain.

### REQ-5: klai-retrieval-api — `_search_notebook` user_id filter

The system SHALL extend `_search_notebook` to filter by `user_id` when the
notebook scope is personal, matching the symmetry already present in
`_search_knowledge`.

- **REQ-5.1:** THE `_search_notebook` function at
  `klai-retrieval-api/retrieval_api/services/search.py:114-129` SHALL add
  a `FieldCondition(key="user_id", match=MatchValue(value=request.user_id))`
  to the `must_conditions` list WHEN `request.notebook_scope` (or
  equivalent indicator — to be finalised in plan) signals a personal
  notebook. The `tenant_id` filter continues to apply in addition.
- **REQ-5.2:** IF the request does not carry a `user_id` AND the notebook
  is personal, THE endpoint SHALL return HTTP 400 with
  `missing_user_id_for_personal_scope` AND SHALL NOT query Qdrant. This
  is a hard contract: personal-scope retrieval requires an authenticated
  user identity.
- **REQ-5.3:** WHEN the notebook is shared/team scope, THE current
  tenant-only filter remains correct AND this requirement SHALL NOT
  apply. The decision SHALL be driven by a `notebook_visibility` payload
  field indexed into klai_focus at ingest time, OR by querying the
  notebook-ownership table before the Qdrant search (architect choice in
  plan).
- **REQ-5.4:** THE regression test SHALL explicitly cover the
  cross-user-same-org scenario: user A in org X queries user B's personal
  notebook in org X → returns zero results (effectively 404 equivalent at
  the retrieval layer).

### REQ-6: `emit_event` uses verified identity, not caller-supplied body

The system SHALL source `tenant_id` and `user_id` for `emit_event` calls
from the verified `request.state.auth` context established by the
AuthMiddleware, not from the request body.

- **REQ-6.1:** THE `emit_event("knowledge.queried", tenant_id=req.org_id,
  user_id=req.user_id, …)` call at
  `klai-retrieval-api/retrieval_api/api/retrieve.py:353-364` SHALL be
  changed to source `tenant_id` and `user_id` from the verified assertion
  (the result of REQ-4's portal-api lookup stored on `request.state.auth`
  or an equivalent extension). It SHALL NOT read from `req` (the pydantic
  body model).
- **REQ-6.2:** WHEN `request.state.auth` does not carry a verified
  `(user_id, org_id)` (for example the request failed the REQ-4 guard
  and somehow still reached the handler — defensive depth), THE handler
  SHALL SKIP the `emit_event` call AND SHALL log a warning with stable
  key `event="product_event_skipped_no_identity"` rather than emitting
  an event with placeholder or caller-supplied values. Product-event
  integrity is a business-metrics contract (Grafana dashboards); a wrong
  `tenant_id` in that table poisons every downstream dashboard.
- **REQ-6.3:** THE same pattern SHALL apply to every `emit_event` call
  site in Python services. A grep inventory for `emit_event(` in
  `klai-retrieval-api`, `klai-portal/backend`, and
  `klai-scribe/scribe-api` SHALL be produced in `research.md`; each
  caller SHALL either source from `request.state.auth` OR from a
  value that was itself verified via REQ-1 earlier in the request.
- **REQ-6.4:** Regression tests SHALL assert that an internal-secret
  call with mismatched `(body.org_id, body.user_id)` — rejected by
  REQ-4 — never produces a `product_events` row.

### REQ-7: Shared library + contract documentation

The system SHALL expose exactly one implementation of the identity-verify
helper across all Python consumers, with its contract documented for
future additions.

- **REQ-7.1:** A new Python package SHALL exist at
  `klai-libs/identity-assert/` exposing `verify_identity(caller_service,
  claimed_user_id, claimed_org_id, bearer_jwt) -> VerifyResult`.
  `VerifyResult` SHALL be a frozen dataclass with fields `verified: bool`,
  `user_id: str | None`, `org_id: str | None`, `reason: str | None`,
  `evidence: Literal["jwt", "membership"] | None`,
  `cached: bool`.
- **REQ-7.2:** THE helper SHALL handle transport (httpx.AsyncClient with
  the standard Klai timeout + `get_trace_headers()` propagation), caching
  (per-process LRU keyed on the tuple, TTL 60 s), and failure mode
  (fail-closed on Redis unavailable OR portal unreachable). Consumers
  call the helper; they do not re-implement it.
- **REQ-7.3:** THE package SHALL ship with a README documenting the
  contract: when to call, what `caller_service` values are valid, how
  `bearer_jwt` is acquired, what each `reason` code means. The README
  SHALL include a migration snippet showing "before (caller-asserted
  identity)" and "after (verified identity)" for the three migrated
  services.
- **REQ-7.4:** THE three services (knowledge-mcp, scribe, retrieval-api)
  SHALL depend on this library via editable installs in their
  `pyproject.toml`, same pattern as `klai-libs/image-storage/`.
- **REQ-7.5:** THE library SHALL emit structlog entries on every call
  with stable key `event="identity_assert_call"` AND fields
  `caller_service`, `verified`, `cached`, `latency_ms`, `reason` (on
  deny). This is per-service telemetry separate from portal-api's
  own `identity_verify_decision` log (REQ-1.7), so we can measure
  cache hit rate at the caller side.

---

## Non-Functional Requirements

- **Performance:** Median added latency SHALL be under 20 ms per scribe
  ingest, per retrieval call, and per knowledge-mcp tool call when the
  identity-assertion cache hits. Cold-cache worst case SHALL be under
  100 ms p95, bounded by portal-api's `/internal/identity/verify`
  response time. The 60-second cache TTL is tuned to keep cache hit rate
  above 90 % under realistic load.
- **Security fail mode:** Redis unavailable → fail CLOSED (reject
  call). portal-api unreachable → fail CLOSED. This is the deliberate
  inverse of SPEC-SEC-005 REQ-1.3 (rate limiter fails open) — an
  auth-class decision must never degrade into "pass everything".
- **Observability:** Both the portal-api `identity_verify_decision` log
  and the per-service `identity_assert_call` log SHALL carry matching
  `request_id` via the existing `X-Request-ID` propagation
  (`get_trace_headers`). VictoriaLogs LogsQL
  `event:"identity_verify_decision" AND verified:false` returns every
  identity-spoof attempt, keyed by `caller_service`.
- **Privacy:** Caller-supplied user/org IDs SHALL be hashed
  (`_hash_sub` pattern) before logging, matching the existing
  retrieval-api middleware convention.
- **Backward compatibility:** Existing internal-secret callers that do
  NOT send identity fields (admin-only, no-user-context calls) continue
  to work via the REQ-4.3 route split OR the REQ-4.2
  `X-Caller-Service=admin` variant. Callers that DO send identity
  fields (LibreChat bridge, scribe ingest path, MCP proxy) get the new
  verify gate and MAY see HTTP 403 during the migration window — this
  is expected and documented in the migration order (see research.md).
- **Rollout:** Each service can migrate independently once
  portal-api `/internal/identity/verify` ships. Migration order is
  REQ-1 → REQ-7 → REQ-2 / REQ-3 / REQ-4 in parallel → REQ-5 (standalone)
  → REQ-6 (depends on REQ-4 being complete).

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Portal-api becomes the single point of failure for every service-to-service call | Redis-backed 60 s cache at the consumer side (REQ-7.2) keeps the steady-state cache-hit path off portal-api; only cache misses and invalidations hit portal-api. |
| 60 s cache window hides a just-revoked user's access for up to 60 s | Accepted trade-off. Documented in research.md. Users whose access is *revoked* (e.g. org membership ended) see up to 60 s of continued access at the worst case — shorter than the effective Zitadel JWT `exp` for most sessions, so the cache does not materially worsen revocation latency. |
| Latency budget of 20 ms median could be missed under cold-cache portal overload | REQ-3.5 "fast path" (JWT `resourceowner` claim suffices) keeps the common case (user acting in their own primary org) zero-extra-roundtrip. portal-api `/internal/identity/verify` SHALL run in the same event loop as existing `/internal/*` endpoints — no new infra hop. |
| LibreChat cannot be patched to forward the end-user JWT quickly | REQ-2.5 fallback: knowledge-mcp uses `bearer_jwt=null` + membership evidence. Narrower than the JWT path but still closes the spoof. Full JWT forwarding lands as a follow-up. |
| Scribe transition window breaks callers that send `org_id` in the body | REQ-3.1 one-sprint transition with `extra="ignore"` → then `extra="forbid"`. Callers are internal (portal frontend, LibreChat bridge); a one-sprint window is enough. |
| Retrieval-api split into admin vs on-behalf-of breaks existing internal callers | REQ-4.3 is an alternative to REQ-4.2. Either path satisfies the SPEC. The split route variant is more future-proof but requires auditing every existing internal caller; the global verify variant is lower-cost but adds 20 ms to every call. Decision belongs to plan phase. |
| emit_event poisoning continues on Python services not audited here | REQ-6.3 grep inventory in research.md enumerates every call site. Any service added later (or an `emit_event` call added in a future PR) MUST be covered by the same pattern; enforcement is code review plus a ruff custom rule candidate (SPEC-SEC-HYGIENE-001 backlog). |
| Redis-backed fail-closed breaks rollout when Redis is briefly unavailable | Accepted trade-off. SPEC-SEC-005's fail-open rate limiter is compatible with this fail-closed verifier — they are two different control classes. Redis outage should page operators, not silently weaken auth. |

---

## Cross-references

- Tracker: [SPEC-SEC-AUDIT-2026-04](../SPEC-SEC-AUDIT-2026-04/spec.md)
- Related: [SPEC-SEC-TENANT-001](../SPEC-SEC-TENANT-001/spec.md) — same
  root theme (tenant-scoping in admin endpoints); this SPEC covers the
  service-to-service surface
- Related: [SPEC-SEC-005](../SPEC-SEC-005/spec.md) — internal-secret
  rotation + audit log are assumed; this SPEC is additive to the
  shared-secret model
- Related: [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md) —
  internal-secret surface hardening; `sanitize_response_body` and
  `hmac.compare_digest` rules land there, not here
- Related rule: [.claude/rules/klai/infra/observability.md](../../../.claude/rules/klai/infra/observability.md)
  — `request_id` cross-service trace and `event="identity_verify_decision"`
  query via VictoriaLogs MCP
