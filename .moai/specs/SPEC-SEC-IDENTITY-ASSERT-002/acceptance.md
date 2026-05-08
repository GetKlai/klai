# SPEC-SEC-IDENTITY-ASSERT-002 — Acceptance Criteria

Each scenario maps to one or more REQs in `spec.md`. All scenarios
MUST pass before the SPEC is considered complete.

---

## A. portal-api `verify_identity_claim` — REQ-1

### A1. JWT-bound, sub matches, single active membership → allow

- Given a Zitadel JWT with valid signature, valid expiry, `sub=U1`,
  AND no `urn:zitadel:iam:user:resourceowner:id` claim
- And `portal_users` has an active row `(zitadel_user_id=U1,
  org_id=O1, status='active')`
- And `portal_orgs` has `(id=O1, zitadel_org_id=Z1, slug='voys',
  deleted_at IS NULL)`
- When portal-api receives `POST /internal/identity/verify` with
  `caller_service="scribe"`, `claimed_user_id=U1`, `claimed_org_id=Z1`,
  `bearer_jwt=<the_jwt>`
- Then the response is 200 with `verified=true`, `user_id=U1`,
  `org_id=Z1`, `org_slug="voys"`, `evidence="jwt"`,
  `cache_ttl_seconds=60`

### A2. JWT-bound, sub matches, claimed_org not a membership → deny

- Given the same JWT as A1 (valid sub=U1)
- And `portal_users` has NO row for `(U1, Z2)`
- When portal-api receives the request with `claimed_org_id=Z2`
- Then the response is 403 with `verified=false`, `reason="no_membership"`

### A3. JWT-bound, sub does NOT match claimed_user_id → deny

- Given a JWT with `sub=U1`
- When portal-api receives `claimed_user_id=U2` (≠ U1)
- Then the response is 403 with `reason="jwt_identity_mismatch"`
- AND the membership lookup is NOT executed (verified by checking
  no `_resolve_active_membership_org_slug` call in test mock)

### A4. JWT-bound, signature invalid → deny (unchanged from v1)

- Given a JWT signed with a key not in Zitadel JWKS
- When portal-api receives the request
- Then the response is 403 with `reason="invalid_jwt"`
- AND no membership lookup is executed
- AND the deny does NOT fall through to the membership path (the
  v1 invariant: invalid JWT is strictly stronger than absent JWT)

### A5. JWT-bound, JWT EXPIRED → deny

- Given a JWT with valid signature but `exp` in the past
- When portal-api receives the request
- Then the response is 403 with `reason="invalid_jwt"`
- AND no membership lookup runs

### A6. JWT-bound, JWT contains resourceowner claim that DOES NOT match
claimed_org_id → allow (regression guard for REQ-1.2)

- Given a JWT with valid sub=U1 AND
  `urn:zitadel:iam:user:resourceowner:id="OTHER_ORG"` (legacy claim
  emission from a custom Zitadel project state)
- And `portal_users` has active `(U1, Z1)` matching `claimed_org_id=Z1`
- When portal-api receives the request with `claimed_org_id=Z1`
- Then the response is 200 with `verified=true`, `evidence="jwt"`
- AND no log line mentions the resourceowner value (the claim is
  ignored entirely)

### A7. JWT-less call, membership exists → allow (unchanged from v1)

- Given `bearer_jwt=null` in the request
- And `portal_users` has active `(U1, Z1)`
- When portal-api receives the request with `claimed_user_id=U1`,
  `claimed_org_id=Z1`
- Then the response is 200 with `verified=true`, `evidence="membership"`

### A8. Cache hit returns same decision without re-querying DB

- Given A1 succeeded and is cached
- When portal-api receives an identical request within 60s
- Then the response is 200 with `cache_hit=true` log field
- AND no DB query is observed (verified via `query_count` test fixture)
- AND no JWT signature re-validation is observed (Zitadel JWKS not hit)

### A9. Multi-org user, claimed_org_id picks the correct active org

- Given user U1 has TWO active memberships in `portal_users`:
  `(U1, O1)` slug=voys, `(U1, O2)` slug=acme
- And the JWT has sub=U1 (no resourceowner)
- When portal-api receives `claimed_org_id=Z2` (Acme's zitadel_org_id)
- Then the response is 200 with `org_id=Z2`, `org_slug="acme"`,
  `evidence="jwt"`
- When the same user retries with `claimed_org_id=Z1`
- Then the response is 200 with `org_id=Z1`, `org_slug="voys"`
- (This scenario was BROKEN under v1 REQ-1.3 if either org was the
  user's resourceowner; now both flows succeed.)

### A10. Soft-deleted org → deny

- Given `portal_users (U1, O1)` active AND `portal_orgs (O1)
  deleted_at = now() - 1 day` (soft-deleted)
- When portal-api receives request matching this org
- Then the response is 403 with `reason="no_membership"`

---

## B. BFF proxy verify-before-forward — REQ-2

### B1. /api/scribe/* with valid session → forwarded with verified headers

- Given an authenticated portal session with `user_id=U1`,
  `org_id=O1`, `access_token=<JWT>`, `org_slug="voys"`
- And `verify_identity_claim` would return allow for those values
- When the frontend calls `GET /api/scribe/v1/transcriptions`
- Then portal-api proxy:
  1. Calls `verify_identity_claim` in-process
  2. Receives a 200 allow decision (cached after first call)
  3. Forwards to `http://scribe-api:8020/v1/transcriptions` with:
     - `Authorization: Bearer <session.access_token>` (existing)
     - `X-Internal-Secret: <settings.internal_secret>`
     - `X-Klai-Verified-User-Id: U1`
     - `X-Klai-Verified-Org-Id: O1`
     - `X-Klai-Verified-Org-Slug: voys`
- And the upstream response is streamed back to the client

### B2. /api/scribe/* when verify denies → 403, no upstream call

- Given a session whose user has NO active membership in
  `session.org_id` (data drift, e.g. user removed from org while
  session active)
- When the frontend calls `/api/scribe/v1/transcriptions`
- Then portal-api proxy returns HTTP 403 with body
  `{"detail": "identity_verification_failed", "reason": "no_membership"}`
- AND scribe-api receives ZERO requests during this test
  (verified via httpx mock or scribe-side request counter)

### B3. Client-asserted X-Klai-Verified-* headers stripped

- Given the frontend sends `GET /api/scribe/v1/transcriptions` WITH
  a forged `X-Klai-Verified-User-Id: ATTACKER_ID` header
- When portal-api proxy processes the request
- Then the forged header is stripped before verification
- AND the upstream receives `X-Klai-Verified-User-Id: <session.user_id>`,
  not the attacker's value
- AND a structlog warning is emitted with
  `event="bff_proxy_inbound_verified_header_rejected"`

### B4. /api/docs/* receives same treatment

- Same as B1 but for the docs upstream
- All four headers (Authorization, X-Internal-Secret,
  X-Klai-Verified-*) are added before forwarding

### B5. Verify-call is in-process, not HTTP

- Given the test environment
- When portal-api proxy executes B1
- Then no outbound HTTP request to `localhost:<portal_port>/internal/...`
  is made (verified by httpx mock at the http-transport layer)
- AND `verify_identity_claim` is invoked directly from `proxy.py`

### B6. Cache prevents duplicate DB hits

- Given B1 was just executed (decision cached)
- When the frontend makes a SECOND `/api/scribe/v1/transcriptions`
  call within 60s with the same session
- Then portal-api proxy uses the cached decision
- AND no additional DB query against `portal_users` is observed
- AND the second call still emits `event="bff_proxy_verified"` with
  `cache_hit=true`

### B7. Verify runs BEFORE request body is consumed

- Given a `POST /api/scribe/v1/transcriptions/{id}/ingest` with a
  large body (5 MB simulated audio)
- And `verify_identity_claim` denies for this session
- When the request lands
- Then the body is NOT streamed upstream (verified via scribe-side
  byte counter staying at 0)
- AND the 403 response is returned immediately (before body read
  completes)

---

## C. scribe-api auth — REQ-3

### C1. Valid BFF call → 200

- Given a request with headers:
  `X-Internal-Secret: <correct>`,
  `X-Klai-Verified-User-Id: U1`,
  `X-Klai-Verified-Org-Id: O1`
- And no `Authorization` header (or any value — scribe ignores it)
- When scribe-api receives `GET /v1/transcriptions`
- Then `get_authenticated_caller` returns
  `CallerIdentity(user_id="U1", org_id="O1")`
- AND the request handler executes normally
- AND no JWT decoding occurs (verified by absence of any
  `jwt.decode` import in the new auth module)
- AND no outbound call to portal-api `/internal/identity/verify`
  is made (verified by httpx mock)

### C2. Wrong internal-secret → 401

- Given headers as C1 but `X-Internal-Secret: <wrong>`
- When scribe-api receives the request
- Then the response is 401 with body `{"detail": "unauthenticated"}`
- AND no `X-Klai-Verified-*` value is acted upon (defence-in-depth:
  even if user_id is set, mismatch on secret aborts immediately)

### C3. Missing X-Internal-Secret → 401

- Given the request has `X-Klai-Verified-User-Id` and
  `X-Klai-Verified-Org-Id` but NO `X-Internal-Secret`
- Then the response is 401, no auth happens

### C4. Missing X-Klai-Verified-User-Id → 401

- Given correct internal-secret but no user-id header
- Then the response is 401

### C5. Missing X-Klai-Verified-Org-Id → 401

- Given correct internal-secret + user-id but no org-id
- Then the response is 401

### C6. Empty-string verified header → 401

- Given correct secret but
  `X-Klai-Verified-User-Id: ""` (empty string)
- Then the response is 401, treated identically to missing header

### C7. JWT-decoding code is gone

- Static check (linter / grep): `klai-scribe/scribe-api/app/core/auth.py`
  contains NO references to `_decode_zitadel_token`, `_get_jwks`,
  `_fetch_jwks`, `_validate_sub`, `_jwks_cache`,
  `urn:zitadel:iam:user:resourceowner:id`, `IdentityAsserter`,
  `klai_identity_assert`
- Dependency check: `klai-scribe/scribe-api/pyproject.toml` does NOT
  list `klai_identity_assert` in `[project.dependencies]` or
  `[tool.uv.sources]`
- Build check: `docker build -f klai-scribe/scribe-api/Dockerfile .`
  succeeds without copying `klai-libs/identity-assert`

### C8. Authorization header is ignored for identity decisions

- Given a request with valid internal-secret + verified-user-id +
  verified-org-id AND `Authorization: Bearer <random-string>`
- Then the request succeeds (scribe ignores Authorization)
- AND no JWT decode is attempted on the random string
  (no `jwt.JWTError` log emitted)

### C9. Existing route handlers unchanged

- The endpoint catalogue at `klai-scribe/scribe-api/app/api/`
  remains identical (route paths, methods, request/response models)
- Only the dependency `Depends(get_authenticated_caller)` resolves
  through the new path; handler bodies don't change
- Existing tests in `klai-scribe/scribe-api/tests/test_*.py` that
  exercise route logic continue to pass with the new auth fixture

---

## D. End-to-end — Mark on platform-org

This is the trigger scenario for the SPEC. Must work after deploy.

### D1. Mark loads /app/transcribe on getklai.getklai.com

- Given Mark is logged into portal on `getklai.getklai.com`
  (platform-org admin, `org_id=1` slug=`getklai`)
- And his portal session is valid
- And his Zitadel JWT lacks the resourceowner claim
- When he navigates to `/app/transcribe`
- Then the page loads with a populated transcription list (200 OK
  from scribe-api via portal-api proxy)
- AND no 403 `unknown_user` is shown
- AND VictoriaLogs query
  `service:scribe-api AND event:identity_assert_call AND
  claimed_user_id_hash:6773f3d7cc6952ae` returns ZERO new entries
  (because scribe no longer calls portal-api directly — REQ-3.3)
- AND VictoriaLogs query
  `service:portal-api AND event:bff_proxy_verified AND verified:true`
  returns at least one new entry for Mark's request

### D2. Voys-tenant user keeps working

- Given a Voys-tenant user with `org_id=42` slug=`voys`
- And their JWT may or may not contain resourceowner (current
  state was unknown but irrelevant)
- When they load `voys.getklai.com/app/transcribe`
- Then the page loads with HTTP 200
- AND existing scribe transcription endpoints behave unchanged

### D3. Cross-tenant write attempt blocked

- Given Mark logged in on `getklai.getklai.com` (org_id=1)
- And Mark crafts a request to `/api/scribe/v1/transcriptions/X/ingest`
  with body `{"kb_slug": "voys-internal-kb"}` (Voys's slug, not Mark's)
- When portal-api proxy processes the request
- Then it forwards `X-Klai-Verified-Org-Id: 1` (Mark's session org,
  NOT the kb_slug's org)
- AND scribe-api's ingest handler resolves the kb_slug against
  `org_id=1` and returns 404 (kb not found in Mark's org)
- AND the cross-tenant write does NOT succeed

### D4. After deploy, Grafana alert REQ-6.2 stays green

- Given the deploy has been live for 30 minutes during business hours
- When the alert evaluates
- Then `caller_service:scribe AND verified:true` count > 0 in the
  rolling window (real users actively using transcribe)
- AND the alert state is OK

---

## E. Migration / rollout — REQ-7

### E1. Deploy order on staging

- Given a staging environment mirroring prod
- When the four-step rollout (REQ-7.1) is executed in order
- Then after step 1: portal-api accepts requests, NO scribe-side
  changes yet, and NO existing functionality regresses (knowledge-mcp
  + retrieval-api still work because they use the centralised
  verifier path that just got the fix)
- After step 2: portal-api BFF forwards `X-Klai-Verified-*` headers;
  scribe-api ignores them (still on direct-mode), continues working
  via its existing JWT-decode path
- After step 3: scribe-api drops direct-mode; ALL scribe traffic
  must now arrive via portal-api BFF proxy
- After step 4: ast-grep CI catches any future re-introduction of
  resourceowner usage

### E2. Rollback procedure works

- Given step 3 has just deployed and a regression is observed
- When the operator runs `git revert <step-3-commit> && gh run watch`
- Then scribe-api returns to JWT-decode mode
- AND existing portal-api BFF proxy still adds the verified-headers
  (harmless: scribe ignores them in direct-mode)
- AND functionality is restored within 5 minutes

### E3. No DB migration

- The diff in `klai-portal/backend/alembic/versions/` is empty
  for this SPEC

### E4. No SOPS env change

- The diff in `klai-infra/core-01/.env.sops` is empty for this SPEC

### E5. Step 3 PR fails CI if step 1+2 not on main

- Given a PR for step 3 that depends on REQ-2 having landed
- And REQ-2 is somehow missing from main (test scenario via
  rebase against an older base)
- Then the scribe-api integration test that exercises the
  `X-Klai-Verified-*` path fails because portal-api's BFF proxy
  isn't forwarding them
- AND the PR cannot merge until REQ-2 is on main

---

## F. Observability — REQ-6

### F1. Log field evidence_path appears

- Given any successful identity verification
- When the structlog line is emitted
- Then it contains `evidence_path` field with a value from
  `{"jwt+membership", "membership", "partner_key", "tenant_only"}`

### F2. Grafana alert config is in repo

- File `deploy/grafana/provisioning/alerting/scribe-no-success.yml`
  exists with a valid Grafana alert rule UID matching the
  klai prefix convention (per `process-rules.md`
  grafana-uid-40-char-limit guidance)
- The alert query asserts the success rate over a 30-minute window

### F3. Retro entry in process-rules.md

- File `.claude/rules/klai/pitfalls/process-rules.md` contains a
  new section titled exactly "claim-emission-vs-claim-consumption (HIGH)"
  with the prevention narrative described in REQ-6.4
