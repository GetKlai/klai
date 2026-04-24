# Acceptance Criteria — SPEC-SEC-IDENTITY-ASSERT-001

Testable scenarios that a regression suite MUST cover. Each AC maps to
one or more requirements in `spec.md` and names the exact fixture needed
from `research.md` section 8.

Pass means the test:
1. Fails today against unpatched code (proves it reproduces the bug)
2. Passes after the corresponding REQ is implemented

---

## AC-1 — MCP spoof attempt rejected

**Covers:** REQ-2, REQ-7, and the M1 + D1 chain.

**Setup:**
- knowledge-mcp is running with `KNOWLEDGE_INGEST_SECRET` set
- `user_a` holds a valid Zitadel JWT for `org_x`
- Fixture forwards a tool call as `user_a` but sets
  `X-User-ID = <user_b's UUID>` and `X-Org-ID = <org_y's UUID>` in the
  headers (simulating LibreChat-side spoof or compromised MCP client)

**When:**
- Test invokes `save_personal_knowledge` with those headers + a valid
  `X-Internal-Secret`

**Then (current — proves the bug):**
- knowledge-mcp forwards `{"org_id": "<org_y>", "user_id": "<user_b>"}`
  to knowledge-ingest, which writes into `user_b`'s personal KB

**Then (post-SPEC):**
- knowledge-mcp calls `/internal/identity/verify` with
  `(claimed_user_id=user_b, claimed_org_id=org_y, bearer_jwt=<user_a's JWT>)`
- portal-api returns `{"verified": false, "reason": "jwt_identity_mismatch"}`
- knowledge-mcp returns error to the MCP client, does NOT call
  knowledge-ingest, does NOT call klai-docs
- VictoriaLogs contains `event="identity_verify_decision"
  verified=false reason="jwt_identity_mismatch"
  caller_service="knowledge-mcp"`

**Alternate path (REQ-2.5 fallback — no JWT forwarded):**
- Same setup, but no `X-User-Token` header
- knowledge-mcp calls verify with `bearer_jwt=null`,
  `claimed_user_id=user_b`, `claimed_org_id=org_y`
- portal-api returns `verified=false, reason="no_membership"` (because
  `user_b` is not actually calling — this is an attacker claiming to be
  `user_b`; the *caller* is actually `user_a` but the `bearer_jwt=null`
  path cannot tell, so it checks the *claimed user's* membership in the
  *claimed org* — which *is* valid for real `user_b`)
- **The `bearer_jwt=null` fallback does NOT catch this pure spoof.** It
  catches the cross-tenant write scenario (AC-7 variant) but needs the
  JWT to catch identity-theft. This is documented in spec.md Risks and
  in research.md §3.3 as the migration trade-off.
- **The strong guarantee requires REQ-2.5's JWT path.**

---

## AC-2 — Scribe ingest with mismatched org_id rejected

**Covers:** REQ-3.

**Setup:**
- `user_a` holds a valid JWT for `org_x`
- A transcription exists with `Transcription.user_id = <user_a>`

**When:**
- Test POSTs `/v1/transcriptions/{id}/ingest` with JWT for `user_a` AND
  body `{"kb_slug": "team-notes", "org_id": "<org_y>"}`

**Then (current — proves the bug):**
- Scribe calls `ingest_scribe_transcript(org_id="<org_y>", …)` and writes
  the transcript into `org_y`'s knowledge-ingest pipeline

**Then (post-SPEC):**
- Scribe ignores (or rejects via `extra="forbid"`) the body `org_id`
  field
- Scribe derives `org_id = <user_a's resourceowner claim> = <org_x>`
  from the JWT directly (REQ-3.5 fast path) OR calls
  `/internal/identity/verify` with the JWT
- Scribe calls `ingest_scribe_transcript(org_id="<org_x>", …)` — the
  transcript lands in the correct tenant
- If `user_a` is not a member of any org, scribe returns HTTP 403
  `{"detail": "no_active_org_membership"}`

---

## AC-3 — Retrieval via internal-secret with mismatched org_id rejected

**Covers:** REQ-4, REQ-7.

**Setup:**
- `retrieval-api` is running with `INTERNAL_SECRET` set
- Attacker has the secret

**When:**
- Test POSTs `/retrieve` with `X-Internal-Secret: <secret>`,
  `X-Caller-Service: knowledge-mcp`, body
  `{"org_id": "<org_y>", "user_id": "<user_a>", "query": "ceo email",
  "scope": "org"}`
  — note `user_a` is in `org_x`, not `org_y`

**Then (current — proves the bug):**
- Middleware sees `auth.method == "internal"`, skips
  `verify_body_identity` (early return at `auth.py:336-337`)
- Qdrant search runs with `org_id = <org_y>` filter — the attacker
  gets `org_y`'s chunks back

**Then (post-SPEC, REQ-4.2 path):**
- Middleware calls `/internal/identity/verify` with
  `(claimed_user_id=user_a, claimed_org_id=org_y, bearer_jwt=null)`
- portal-api returns `verified=false, reason="no_membership"` because
  `user_a` has no active membership in `org_y`
- Middleware returns HTTP 403 `{"error": "identity_assertion_failed"}`
- Qdrant is NEVER queried
- VictoriaLogs contains both `event="identity_verify_decision"` and
  `cross_org_rejected_total` incremented by the retrieval side

**Then (post-SPEC, REQ-4.3 alternative):**
- The `/retrieve` route now requires `X-Caller-Service` + the verify
  call; the `/admin/retrieve` route is where no-identity calls go
- Posting to `/retrieve` without `X-Caller-Service` → HTTP 400
  `missing_caller_service`
- Otherwise same as above

---

## AC-4 — Personal notebook read by a different user in same org returns nothing

**Covers:** REQ-5.

**Setup:**
- `user_a` and `user_b` both in `org_x`
- Personal notebook `n_b` (owner `user_b`) in klai_focus collection,
  containing chunks with `tenant_id = <org_x>` and `user_id = <user_b>`
  (or equivalent user-ownership payload field)

**When:**
- `user_a` posts `/retrieve` with body
  `{"org_id": "<org_x>", "user_id": "<user_a>", "query": "confidential",
  "scope": "notebook", "notebook_id": "<n_b>"}` with a valid JWT for
  `user_a`

**Then (current — proves the bug):**
- `_search_notebook` filters only on `tenant_id = <org_x>` and
  `notebook_id = n_b`
- Qdrant returns `user_b`'s chunks — `user_a` reads `user_b`'s personal
  notebook

**Then (post-SPEC):**
- `_search_notebook` adds `user_id = <user_a>` to `must_conditions`
  when `notebook_visibility = "personal"`
- Qdrant returns zero chunks (no chunks match both
  `user_id = <user_a>` and `notebook_id = <n_b>`, because `n_b` is
  owned by `user_b`)
- Response: `{"chunks": []}` (the retrieval layer's equivalent of 404;
  downstream code can decide whether to turn this into a 404 or an
  empty response — the SPEC requires the Qdrant filter, not the HTTP
  status)

---

## AC-5 — portal-api `/internal/identity/verify` contract

**Covers:** REQ-1.

**Five sub-cases — each is a single parameterised test:**

**AC-5a (verified JWT + membership match):**
- Input: `{caller_service: "scribe", claimed_user_id: "<user_a>",
  claimed_org_id: "<org_x>", bearer_jwt: <user_a's valid JWT>}`
- Expected: HTTP 200, `{"verified": true, "user_id": "<user_a>",
  "org_id": "<org_x>", "cache_ttl_seconds": 60, "evidence": "jwt"}`
- Log: `event="identity_verify_decision" verified=true evidence="jwt"
  caller_service="scribe"`

**AC-5b (JWT sub ≠ claimed_user_id):**
- Input: `{..., claimed_user_id: "<user_b>",
  bearer_jwt: <user_a's valid JWT>}`
- Expected: HTTP 403, `{"verified": false,
  "reason": "jwt_identity_mismatch"}`
- No cache write on deny

**AC-5c (membership evidence, no JWT):**
- Input: `{caller_service: "knowledge-mcp",
  claimed_user_id: "<user_a>", claimed_org_id: "<org_x>",
  bearer_jwt: null}`
- Expected: HTTP 200, `{"verified": true, ..., "evidence": "membership"}`

**AC-5d (no_membership):**
- Input: `{..., claimed_user_id: "<user_a>",
  claimed_org_id: "<org_y>", bearer_jwt: null}`
- Expected: HTTP 403, `{"verified": false, "reason": "no_membership"}`

**AC-5e (cache hit):**
- Same as AC-5a
- First call: DB lookup occurs, latency > 5 ms
- Second call within 60 s: response identical, latency < 2 ms, no DB
  lookup occurred (assert via `portal_db_query_counter` metric or
  equivalent)

**AC-5f (cache TTL expiry):**
- Same as AC-5a
- First call: cached
- Wait 61 seconds
- Second call: DB lookup occurs again

**AC-5g (Redis unreachable fails closed):**
- Redis fixture pauses/drops connections
- Input: valid AC-5a payload
- Expected: HTTP 503, `{"verified": false,
  "reason": "cache_unavailable"}`

---

## AC-6 — `emit_event` uses verified tenant, not caller-supplied

**Covers:** REQ-6.

**Setup:**
- Retrieval call with valid JWT for `user_a` in `org_x`
- Body incorrectly carries `org_id = <org_x>`, `user_id = <user_a>`
  (matches JWT — this call would succeed at REQ-4)

**When:**
- `/retrieve` completes successfully; `emit_event("knowledge.queried",
  …)` is fired

**Then (current — proves the risk):**
- `product_events` row has `tenant_id = <org_x>` (from body) and
  `user_id = <user_a>` (from body). In this case matches the JWT — OK.
- But in a hypothetical where the caller tampered with body values AND
  retrieval-api failed to catch it upstream, the product_events row
  would be wrong.

**Then (post-SPEC):**
- `product_events` row `tenant_id` comes from
  `request.state.auth.verified_org_id` (the portal-verified value), not
  from `req.org_id`
- **Regression test:** a request with body `org_id = <org_y>` and
  JWT for `org_x` is rejected at REQ-4 before reaching the handler.
  The test asserts that `product_events` contains ZERO rows for the
  attempt (not one row with `<org_y>`, not one row with `<org_x>`).

---

## AC-7 — End-to-end happy path still works after migration

**Covers:** the integration as a whole; non-regression.

**Setup:**
- `user_a` in `org_x`, valid JWT
- LibreChat → knowledge-mcp path (MCP patched to forward
  `X-User-Token`)
- LibreChat → retrieval-api path via internal-secret caller
  (`X-Caller-Service: librechat-bridge`)

**When (three happy-path flows):**

1. **Save to personal KB:** `user_a` says "sla dit op" in LibreChat.
   knowledge-mcp receives tool call with matching claimed + verified
   identity. `/internal/identity/verify` returns
   `{verified: true, evidence: "jwt"}` (cache miss on first call,
   cache hit thereafter). `_save_to_ingest` is called with verified
   `user_id` and `org_id`. Knowledge-ingest writes the chunk into
   `user_a`'s personal KB.
2. **Scribe ingest:** `user_a` uploads a recording; after
   transcription completes, `user_a` clicks "Send to KB".
   `POST /v1/transcriptions/{id}/ingest` succeeds; transcript lands
   in `org_x`'s `team-notes` KB.
3. **Retrieval via LibreChat bridge:** `user_a` asks a question in
   LibreChat that triggers retrieval. The bridge posts
   `/retrieve` with `X-Internal-Secret`,
   `X-Caller-Service: librechat-bridge`, body
   `{org_id: <org_x>, user_id: <user_a>, query: "...",
   scope: "both"}`. Middleware verifies via portal, returns chunks
   from `org_x`.

**Expected (all three):**
- No HTTP 4xx / 5xx anywhere in the chain for the legitimate flow
- `identity_assert_call` logs show `cached=true` for the second and
  later verify lookups within the 60 s window
- `product_events` has exactly one row per query (AC-6 invariant holds)
- End-to-end p95 latency within 10 % of pre-SPEC baseline (perf
  regression guard — a failing test here catches an unexpectedly slow
  portal-api `/internal/identity/verify`)

---

## AC-8 — Consumer library `identity_assert_call` telemetry

**Covers:** REQ-7.5.

**Setup:**
- Any service consuming `klai-libs/identity-assert` makes one verify
  call

**Then:**
- A structlog entry exists with stable key `event="identity_assert_call"`
  and fields: `caller_service`, `verified`, `cached`, `latency_ms`,
  `reason` (on deny)
- Querying VictoriaLogs
  `event:"identity_assert_call" AND cached:false` returns every
  cache-miss across all three services — this is the operator-facing
  signal if portal-api starts getting hammered

---

## Performance acceptance

Not a functional AC but part of the SPEC's non-functional commitment:

- Median added latency per scribe ingest: < 20 ms (measured against
  baseline pre-SPEC)
- p95 added latency per scribe ingest: < 100 ms
- Cache hit rate in steady state: > 90 %

Grafana panel queries published with the SPEC for operator monitoring.

---

## Definition of Done

All 7 functional ACs (AC-1 through AC-8) pass in CI against the
implementation. The performance ACs are sampled in staging for 7
consecutive days before GA. A failing test exists for each current
finding (M1, S1, R1, R2, R3, D1-via-M1) that fails against unpatched
code — these are the regression guardrails per CLAUDE.md Rule 4
(Reproduction-First Bug Fixing).
