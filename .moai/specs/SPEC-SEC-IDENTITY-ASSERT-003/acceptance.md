# SPEC-SEC-IDENTITY-ASSERT-003 — Acceptance Criteria

Each scenario maps to one or more REQs in `spec.md`. All scenarios
MUST pass before the SPEC is considered complete.

The structure mirrors SPEC-SEC-IDENTITY-ASSERT-002's group-letter
convention: A. for the retrieval-api fix, B. for the klai-connector
fix, C. for the ast-grep glob extension, D. for Grafana alerts, E.
for migration / rollout.

---

## A. retrieval-api JWT path — REQ-1

### A1. JWT lacks resourceowner, X-Org-Id present, single active membership → allow

- Given a Zitadel JWT with valid signature, valid audience, valid expiry,
  `sub=U1`, AND no `urn:zitadel:iam:user:resourceowner:id` claim
- And `portal_users` has an active row `(zitadel_user_id=U1, org_id=O1)`
- And `portal_orgs` has `(id=O1, zitadel_org_id=Z1, slug='voys',
  deleted_at IS NULL)`
- And the request includes `X-Org-Id: Z1`
- When retrieval-api receives `POST /retrieve` with
  `Authorization: Bearer <the_jwt>` and the body for a normal retrieve
- Then the response is HTTP 200
- AND `request.state.verified_caller` equals
  `VerifiedCaller(user_id=U1, org_id=Z1)` from the portal response
- AND `IdentityAsserter.verify` was called with
  `caller_service="retrieval-api"`, `claimed_user_id=U1`,
  `claimed_org_id=Z1`, `bearer_jwt=<the_jwt>`
- AND no code path read `_ZITADEL_RESOURCEOWNER_CLAIM` (verified by
  static check: `_ZITADEL_RESOURCEOWNER_CLAIM` symbol is deleted from
  `klai-retrieval-api/retrieval_api/middleware/auth.py`)

### A2. JWT lacks resourceowner, X-Org-Id missing → 400

- Given the same JWT as A1
- And the request omits `X-Org-Id` (or sends an empty string)
- When retrieval-api receives the request
- Then the response is HTTP 400 with `{"error": "missing_org_id"}`
- AND `IdentityAsserter.verify` is NOT called (verified via mock)
- AND a structlog warning with `event="missing_org_id"` is emitted

### A3. JWT INCLUDES legacy resourceowner claim that does NOT match X-Org-Id → allow (claim ignored)

- Given a JWT with `sub=U1` AND
  `urn:zitadel:iam:user:resourceowner:id="OTHER_ORG"` (a custom-Zitadel
  emission case)
- And `portal_users` has active `(U1, Z1)` and the request sends
  `X-Org-Id: Z1`
- When retrieval-api receives the request
- Then the response is HTTP 200
- AND `IdentityAsserter.verify` was called with `claimed_org_id=Z1`,
  NOT with the resourceowner value
- AND no log line mentions `OTHER_ORG`
- AND `request.state.verified_caller.org_id == Z1` from the portal
  response

### A4. portal verify denies (no membership) → 403

- Given a valid JWT for U1 and a request with `X-Org-Id=Z2`
- And `IdentityAsserter.verify` returns
  `verified=false, reason="no_membership"`
- When retrieval-api receives the request
- Then the response is HTTP 403 with
  `{"error": "identity_assertion_failed"}`
- AND `cross_org_rejected_total` is incremented
- AND a structlog warning with `event="identity_assertion_failed"`
  and `reason="no_membership"` is emitted
- AND `request.state.verified_caller` is NOT set

### A5. JWT signature invalid → 401 (existing behaviour preserved)

- Given a JWT signed with a key not in Zitadel JWKS
- When retrieval-api receives the request
- Then the response is HTTP 401 with `{"error": "unauthorized"}`
- AND `IdentityAsserter.verify` is NOT called
- AND no membership lookup is executed
- AND the `_decode_jwt` rejection path (`error="invalid_jwt_signature"`)
  is unchanged from the pre-SPEC implementation

### A6. JWT expired → 401 (existing behaviour preserved)

- Given a JWT with valid signature but `exp` in the past
- When retrieval-api receives the request
- Then the response is HTTP 401 with `{"error": "unauthorized"}`
- AND `IdentityAsserter.verify` is NOT called

### A7. Internal-secret path unaffected (regression guard)

- Given a request with `X-Internal-Secret: <correct>`,
  `X-Caller-Service: knowledge-mcp`, and a body containing
  `org_id` + `user_id`
- When retrieval-api receives `POST /retrieve`
- Then the existing internal-secret path executes per SPEC-001 REQ-3
- AND `verify_body_identity` calls `IdentityAsserter.verify` with
  `bearer_jwt=None` (unchanged from pre-SPEC)
- AND the JWT-path code added in REQ-1 is NOT exercised

### A8. Cache hit on second JWT call (REQ-1.5 cache TTL)

- Given A1 succeeded and the `IdentityAsserter` cache holds the
  decision
- When retrieval-api receives a second identical request within 60s
- Then the response is HTTP 200
- AND `IdentityAsserter.verify` is called but the cache short-circuits
  to the cached decision (verified by no outbound HTTP call to
  portal-api on the second request)
- AND no DB query against `portal_users` is observed at portal-api
  for the second call

### A9. Multi-org user picks correct org via X-Org-Id

- Given user U1 has TWO active memberships:
  `(U1, O1)` slug=voys, `(U1, O2)` slug=acme
- And the JWT has `sub=U1` and no resourceowner
- When retrieval-api receives a request with `X-Org-Id=Z2` (Acme)
- Then `verified_caller.org_id == Z2`
- When the same user retries with `X-Org-Id=Z1` (Voys)
- Then `verified_caller.org_id == Z1`
- (Both flows succeed; the JWT alone does not pick one over the
  other.)

### A10. Static check — resourceowner symbol gone from retrieval-api

- Given the diff for the retrieval-api PR
- When CI runs the static check
  `grep -rn "_ZITADEL_RESOURCEOWNER_CLAIM\|urn:zitadel:iam:user:resourceowner" klai-retrieval-api/retrieval_api/`
- Then the result is empty
- AND `klai-retrieval-api/retrieval_api/middleware/auth.py`'s
  `AuthContext` dataclass no longer has a `resourceowner` field

---

## B. klai-connector middleware — REQ-2

### B1. JWT lacks resourceowner, X-Org-Id present, valid membership → allow

- Given a Zitadel access token whose introspection returns
  `active=true`, `sub=U1`, valid `aud`, AND no
  `urn:zitadel:iam:user:resourceowner:id` claim
- And `portal_users` has active `(U1, Z1)`
- And the request sends `X-Org-Id: Z1`
- When klai-connector middleware processes the request
- Then introspection runs (cached for 5 min thereafter)
- AND `IdentityAsserter.verify` is called with
  `caller_service="klai-connector"`, `claimed_user_id=U1`,
  `claimed_org_id=Z1`, `bearer_jwt=<the_token>`
- AND the response is HTTP 200 (or whatever the route handler returns)
- AND `request.state.org_id == "Z1"` from the portal response
- AND no code path reads
  `claims.get("urn:zitadel:iam:user:resourceowner:id")`

### B2. JWT lacks resourceowner, X-Org-Id missing → 400

- Given the same introspection result as B1
- And the request omits `X-Org-Id`
- And the request is NOT a portal-bypass call (no
  `_portal_secret` match)
- When klai-connector middleware processes the request
- Then the response is HTTP 400 with `{"error": "missing_org_id"}`
- AND `IdentityAsserter.verify` is NOT called

### B3. portal verify denies → 403 (NOT 401)

- Given a valid Zitadel token (introspection passes) and
  `X-Org-Id: Z9` for which the user has no membership
- And `IdentityAsserter.verify` returns
  `verified=false, reason="no_membership"`
- When klai-connector middleware processes the request
- Then the response is HTTP 403 with
  `{"error": "identity_assertion_failed"}`
- AND a structlog warning is emitted
- AND the response is NOT 401 (the user has a valid token; this is
  authorization, not authentication)

### B4. Token introspection fails → 401 (existing behaviour preserved)

- Given a Zitadel token whose introspection returns
  `active=false`
- When klai-connector middleware processes the request
- Then the response is HTTP 401 with `{"error": "unauthorized"}`
- AND `IdentityAsserter.verify` is NOT called
- AND the introspection cache does NOT receive the failed result
  (existing code at lines 121-141, unchanged)

### B5. Audience mismatch → 401 (existing behaviour preserved)

- Given a Zitadel token whose introspection succeeds but `aud` does
  NOT match `settings.zitadel_api_audience`
- When klai-connector middleware processes the request
- Then the response is HTTP 401
- AND the cache is NOT populated with the wrong-audience claims
  (the existing pre-cache audience check at lines 129-134 is
  preserved)

### B6. Portal-bypass path unaffected

- Given a request with `Authorization: Bearer <portal_caller_secret>`
  (the portal service-to-service secret)
- When klai-connector middleware processes the request
- Then the existing bypass at lines 107-116 takes effect
- AND `request.state.from_portal == True`
- AND `request.state.org_id == None`
- AND `IdentityAsserter.verify` is NOT called
- AND the `X-Org-Id` header is NOT required for this path

### B7. OAuth callback flow with resourceowner-less JWT

- Given Mark logs into Klai (Zitadel JWT lacks resourceowner per
  SPEC-002 §1.1 evidence) and clicks "Connect Google Drive"
- When the user completes Google's consent screen and is redirected
  back to klai-connector's OAuth callback
- And the redirect carries the user's portal-issued
  `Authorization: Bearer <jwt>` and `X-Org-Id: <his-org>`
- Then the callback succeeds (no 401 on the resourceowner-missing
  read, because that read is gone)
- AND `request.state.org_id` is set from the portal verify response
- AND the connector setup flow continues to the next step
- (This is the user-visible motivation for REQ-2.)

### B8. Cache hit on second introspection (REQ-2.6 cache preserved)

- Given B1 succeeded and the introspection token cache holds the
  result
- When klai-connector receives a second request with the same
  bearer token within 5 min
- Then introspection is NOT called again (cache hit)
- AND `IdentityAsserter.verify` IS called again, but its own
  60s cache short-circuits to the cached decision
- AND the response is HTTP 200

### B9. Static check — resourceowner literal gone from klai-connector

- Given the diff for the klai-connector PR
- When CI runs
  `grep -rn "urn:zitadel:iam:user:resourceowner" klai-connector/app/`
- Then the result is empty (test fixtures live under
  `klai-connector/tests/`, not `app/`)

---

## C. ast-grep glob extension — REQ-3

### C1. Glob extension lands in the same PR as the service refactor

- Given a PR that refactors `klai-retrieval-api/retrieval_api/middleware/auth.py`
  per REQ-1
- When the PR is opened
- Then the same PR diff also includes:
  - `rules/no-zitadel-resourceowner-claim.yml` `files:` adds
    `klai-retrieval-api/retrieval_api/**/*.py`
  - `rules/no-zitadel-resourceowner-claim.yml` `ignores:` adds
    `klai-retrieval-api/tests/**/*.py`
- AND CI ast-grep job passes on the PR (no surviving resourceowner
  reads in retrieval-api source after the refactor)

### C2. Same for klai-connector

- Given a PR that refactors `klai-connector/app/middleware/auth.py`
  per REQ-2
- When the PR is opened
- Then the same PR diff also includes:
  - `rules/no-zitadel-resourceowner-claim.yml` `files:` adds
    `klai-connector/app/**/*.py`
  - `rules/no-zitadel-resourceowner-claim.yml` `ignores:` adds
    `klai-connector/tests/**/*.py`
- AND CI ast-grep job passes on the PR

### C3. Future reintroduction blocked at PR time

- Given a hypothetical future PR that adds
  `claims.get("urn:zitadel:iam:user:resourceowner:id")` anywhere in
  `klai-retrieval-api/retrieval_api/` or `klai-connector/app/`
- When the PR is opened
- Then the CI ast-grep job fails with a clear error pointing to this
  SPEC and SPEC-002 REQ-5
- AND the PR cannot merge until the offending read is removed

### C4. Cross-service grep returns only test fixtures and historical SPECs

- Given both PRs (REQ-1 + REQ-2) have landed on main
- When the audit grep runs:
  ```
  grep -rn "urn:zitadel:iam:user:resourceowner" \
    klai-portal/backend/ klai-scribe/ klai-retrieval-api/ \
    klai-connector/ klai-knowledge-mcp/ klai-libs/ \
    klai-mailer/
  ```
- Then ALL hits fall into ONE of these categories:
  - Test fixtures under `tests/` directories
  - Historical SPEC documents (none in source paths)
  - The `.claude/rules/klai/platform/zitadel.md` rule file
- AND no active source-code line in any service references the claim

---

## D. Grafana alerts — REQ-4

### D1. retrieval-api identity-assertion-failure alert exists

- Given the deploy of REQ-4
- When `deploy/grafana/provisioning/alerting/` is synced via the
  `deploy-compose.yml` workflow
- Then a file named `identity-verify-failures-iam-003.yml` (or a
  similarly-named file) exists with a Grafana alert rule
- AND the rule UID begins with `spec-iam-003-` and is at most 40
  characters
- AND the rule fires at threshold > 5 events/minute over a 5-minute
  rolling window for the retrieval-api stream

### D2. klai-connector identity-assertion-failure alert exists

- Same as D1 but for the klai-connector stream
- AND the rule UID is distinct from the retrieval-api UID and also
  prefixed `spec-iam-003-`
- AND both UIDs pass the canary-CI check
  `scripts/audit-alert-uid-length.sh`

### D3. Alert payloads include service / caller_service / reason

- Given the alert fires on staging
- When the on-call engineer opens the alert detail
- Then the payload includes `service`, `caller_service`, and
  `reason` fields drawn from the structlog event (or labelled
  Prometheus counter, depending on REQ-4.5 implementation choice)
- AND the engineer can immediately see whether the deny is
  `no_membership` (legitimate, but unexpected at scale) versus
  `invalid_jwt` (likely deploy issue) without opening VictoriaLogs
  separately

### D4. Existing Prometheus counter still ticks

- Given retrieval-api has been live with REQ-1 for at least 1 hour
- When `cross_org_rejected_total{reason="identity_assertion_failed"}`
  is queried via Prometheus
- Then the metric increments on every `verified=false` decision
  (unchanged from pre-SPEC behaviour, line 420 of
  `klai-retrieval-api/retrieval_api/middleware/auth.py`)

---

## E. Migration / rollout — REQ-6

### E1. SPEC-002 must be live first

- Given a clean staging environment
- When step 1 (retrieval-api REQ-1) is deployed BEFORE SPEC-002
  portal-api changes are live
- Then the deploy succeeds (the code itself runs)
- BUT every JWT-bound retrieve request returns HTTP 403
  `identity_assertion_failed` because portal-api still rejects on the
  resourceowner equality check
- AND the operator MUST verify SPEC-002 portal-api is on main + deployed
  before proceeding (REQ-6.1 step 1)
- AND attempting to deploy this SPEC against pre-SPEC-002 portal-api
  is a hard incompatibility that is observed within 1 minute via the
  REQ-4 alert

### E2. Deploy order: retrieval-api first

- Given SPEC-002 is live in production
- When step 2 (retrieval-api REQ-1 + REQ-3 + REQ-5.1-5.3) is deployed
- Then retrieval-api restarts cleanly
- AND existing internal-secret callers continue to work (regression
  guard A7)
- AND the JWT path begins routing through portal verify
- AND the REQ-4 alert remains green for a 30-minute observation window
- (REQ-6.3 mandates this observation window before step 3.)

### E3. Deploy order: klai-connector second

- Given step 2 has been observed green for 30 minutes
- When step 3 (klai-connector REQ-2 + REQ-3 + REQ-5.4-5.6) is deployed
- Then klai-connector restarts cleanly
- AND `alembic upgrade head` runs successfully on the connector
  entrypoint (per the auto-migrate pattern documented in
  `process-rules.md` `scribe-deploy-no-alembic`); this SPEC adds no
  migrations of its own, so the alembic head should match
  pre-deploy
- AND existing portal-bypass calls work (B6)
- AND OAuth callback flow works for resourceowner-less JWTs (B7)

### E4. Rollback procedure works (per service)

- Given step 3 has just deployed and a regression is observed
- When the operator runs `git revert <step-3-commit> && gh run watch`
- Then klai-connector reverts to the pre-SPEC behaviour
- AND retrieval-api (still on REQ-1) keeps working — no
  co-dependency between the two services
- AND SPEC-002 portal-api stays untouched
- (Mirror: rolling back step 2 leaves klai-connector unaffected
  because step 3 only deploys after step 2 is green for 30 min,
  so a rollback of step 2 happens before step 3 ships.)

### E5. No DB migration

- The diff in `klai-retrieval-api/.../alembic/` (if any) is empty
  for this SPEC
- The diff in `klai-connector/.../alembic/versions/` is empty for
  this SPEC

### E6. No SOPS env change

- The diff in `klai-infra/core-01/.env.sops` is empty for this SPEC
- The deploy-compose workflow does NOT run with
  `allow_removal=I-CONFIRM-REMOVAL` for any commit in this SPEC

### E7. Step 3 PR fails CI if SPEC-002 not on main

- Given a PR for step 3 that depends on SPEC-002 + step 2 having
  landed
- And SPEC-002 is somehow missing from main (test scenario via
  rebase against an older base)
- Then the klai-connector integration test that exercises the
  `IdentityAsserter` path fails because portal-api's
  `/internal/identity/verify` still enforces the resourceowner
  equality check
- AND the PR cannot merge until SPEC-002 is on main

### E8. Cross-service ast-grep CI guard

- Given both services have shipped REQ-1 + REQ-2 + REQ-3
- When CI runs the ast-grep job on a clean main checkout
- Then the job passes
- AND the audit grep from C4 returns only test fixtures and historical
  SPEC documents
- AND adding `urn:zitadel:iam:user:resourceowner` to any active
  source path fails CI on the next PR
