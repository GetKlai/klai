# SPEC-SEC-IDENTITY-ASSERT-003 — retrieval-api + klai-connector membership-authoritative auth

Status: Draft
Author: Mark Vletter (with assistant)
Follow-up to: SPEC-SEC-IDENTITY-ASSERT-002 (PR #545)
Related: SPEC-SEC-IDENTITY-ASSERT-001, SPEC-AUTH-008, SPEC-SEC-INTERNAL-001
Created: 2026-05-08

---

## 1. Background

SPEC-SEC-IDENTITY-ASSERT-002 retired the
`urn:zitadel:iam:user:resourceowner:id` JWT claim as a source of truth
inside `portal-api` (REQ-1) and `scribe-api` (REQ-3). Membership lookup
in `portal_users` is now the canonical authority for "which orgs can
this user act on", aligned with `.claude/rules/klai/platform/zitadel.md`
lines 99-100 ("Never use `urn:zitadel:iam:user:resourceowner:id` —
not always present. Use `sub` (OIDC subject) → `portal_users` →
`portal_orgs` join for reliable org resolution").

Two services were not yet covered by SPEC-002 because they are not
proxied through portal-api BFF and therefore needed a different fix
shape. Both still read the resourceowner claim from the inbound JWT
and reject the request when the claim is absent. After SPEC-002 landed,
they are the only two remaining latent occurrences of the same bug
class:

1. **`klai-retrieval-api`**:
   `klai-retrieval-api/retrieval_api/middleware/auth.py` line 61 defines
   `_ZITADEL_RESOURCEOWNER_CLAIM` and lines 339-353 read it into
   `AuthContext.resourceowner` for every JWT-bound request. Lines
   478-490 in `verify_body_identity` then reject any call where
   `body_org_id != auth.resourceowner`. For Klai users whose JWT lacks
   the claim (the production reality per SPEC-002 §1.1), the field is
   `None` and the equality check silently skips — but the JWT path
   never populates `request.state.verified_caller` for those users
   (line 508 requires `auth.resourceowner is not None`), so any
   downstream code that relies on `verified_caller` runs with no
   identity and either crashes or denies. End-state: JWT-bound
   retrieve calls hit a fail-soft / fail-loud edge that is not
   load-bearing security and breaks legitimate users.

2. **`klai-connector`**:
   `klai-connector/app/middleware/auth.py` line 138 reads
   `claims.get("urn:zitadel:iam:user:resourceowner:id")` after Zitadel
   token introspection and returns HTTP 401 when the claim is missing
   (lines 139-141). This is a hard 401: the OAuth callback flow for
   Google Drive, Microsoft 365, Notion, etc. fails outright for any
   user whose JWT lacks the claim. It is the strongest form of the
   bug: not a soft skip, an outright deny.

Production evidence already documented in SPEC-002 §1.3
(VictoriaLogs 2026-04-01 → 2026-05-08, zero `verified=true`
events for `caller_service:scribe`) generalises to these two
services: anywhere the resourceowner claim is required, it isn't
emitted, so the path is dead code in the success direction and a
reject path in the failure direction.

Internal-secret callers into retrieval-api are NOT affected by the
bug fixed here (they use `verify_body_identity` with `bearer_jwt=None`
which already routes through the membership-authoritative
`/internal/identity/verify` after SPEC-002 REQ-1). This SPEC fixes
ONLY the JWT-bound paths.

## 2. Why retire the claim from these two services

SPEC-002 §2 establishes that the resourceowner-equality check is
non-load-bearing for Klai's threat model: it does not close any
in-scope attack vector and conflates **identity** (`sub`) with
**active org context** (`claimed_org_id`). That entire argument
applies verbatim to retrieval-api and klai-connector — there is no
service-specific reason to keep reading the claim in either of
them.

Additional service-specific motivation:

- `klai-retrieval-api` is on the chat hot path. Every LibreChat or
  partner-API completion flows through here. Soft-failing on
  resourceowner-absence means a class of legitimate end-users get
  empty retrieval responses with no actionable error.
- `klai-connector` is the OAuth callback target. Failing 401 here
  means a user clicks "Connect Google Drive", goes through Google's
  consent screen, is redirected back to Klai, and the connector
  setup fails with no visible cause. The user has no path forward
  except to ask support.

The fix is structurally identical to SPEC-002 REQ-1: route the JWT
path through `portal-api /internal/identity/verify` and let the
already-deployed membership-authoritative resolver decide which org
the user is acting on. Both services already import the
`klai_identity_assert.IdentityAsserter` library; the change is to
extend its usage from the internal-secret path to the JWT path.

## 3. Goals

1. Make every JWT-bound entry-point on `klai-retrieval-api` and
   `klai-connector` work for every authenticated portal user,
   regardless of Zitadel claim emission.
2. Remove the last two in-tree consumers of
   `urn:zitadel:iam:user:resourceowner:id` (per the
   2026-05-08 audit — see REQ-7).
3. Extend the `rules/no-zitadel-resourceowner-claim.yml` ast-grep
   rule's `files:` glob to cover both services so future
   reintroduction is blocked at CI time.
4. Extend Grafana alerting so the equivalent of SPEC-002 REQ-6.2
   (`bff_proxy_verify_failures`) covers retrieval-api and
   klai-connector deny rates.
5. Preserve all in-scope threat coverage from
   SPEC-SEC-IDENTITY-ASSERT-001 §"Threat Model".

## 4. Threat-model addendum

This SPEC inherits the threat model from
SPEC-SEC-IDENTITY-ASSERT-001 §"Threat Model" verbatim and
SPEC-SEC-IDENTITY-ASSERT-002 §4 (BFF-as-identity-boundary,
scribe direct-mode removal) where it applies.

After SPEC-003 ships, the
`urn:zitadel:iam:user:resourceowner:id` claim has zero consumers
in active Klai service code. The only remaining references are
test fixtures that build JWTs INCLUDING this claim to verify it is
harmless when present (regression guard), historical SPEC documents,
and the `.claude/rules/klai/platform/zitadel.md` rule itself. The
ast-grep rule from SPEC-002 REQ-5.2 prevents reintroduction.

Service-specific addenda:

### Addendum-1: retrieval-api JWT path trust boundary

retrieval-api's JWT path verifies the JWT signature, issuer,
audience, and `sub` cryptographically (unchanged by this SPEC).
After this SPEC, the org-resolution step delegates to portal-api
membership lookup over a portal-internal HTTP call authenticated
by `INTERNAL_SECRET`. The trust boundary stays where it already
was for the internal-secret path: portal-api is the authoritative
membership store, retrieval-api is a downstream that asks.

### Addendum-2: klai-connector JWT path trust boundary

klai-connector receives Zitadel access tokens that the Zitadel
introspection endpoint already validates (signature, expiry,
audience). After this SPEC, the org-resolution step delegates to
portal-api membership lookup. The introspection cache (5 minute
TTL, lines 32-72 of `auth.py`) is preserved; portal-api adds a
~5-15ms steady-state cost on cache miss, but the existing
`klai_identity_assert.IdentityAsserter` cache (60s TTL) absorbs
near-equivalents.

## 5. Requirements

### REQ-1: retrieval-api — JWT path drops resourceowner read

The system SHALL retire `_ZITADEL_RESOURCEOWNER_CLAIM` consumption from
`klai-retrieval-api/retrieval_api/middleware/auth.py` and route every
JWT-bound identity decision through `portal-api /internal/identity/verify`.

- **REQ-1.1:** WHEN `AuthMiddleware.dispatch` (lines 318-392 of
  `klai-retrieval-api/retrieval_api/middleware/auth.py`) decodes a JWT,
  THE middleware SHALL stop reading
  `payload.get(_ZITADEL_RESOURCEOWNER_CLAIM)`. The `resourceowner`
  field on `AuthContext` (line 86) SHALL be deleted; downstream
  consumers in `verify_body_identity` SHALL be rewritten per REQ-1.3.
- **REQ-1.2:** THE constant `_ZITADEL_RESOURCEOWNER_CLAIM` (line 61)
  SHALL be deleted. No code path SHALL read the claim string from the
  JWT payload after this REQ lands.
- **REQ-1.3:** WHEN `verify_body_identity` (line 431) is called for a
  JWT-bound caller (`auth.method == "jwt"`), THE function SHALL call
  `klai_identity_assert.IdentityAsserter.verify` with
  `caller_service="retrieval-api"`,
  `claimed_user_id=auth.sub`,
  `claimed_org_id=<org-id-from-header>`,
  `bearer_jwt=<the-bearer-token>`,
  `request_headers=dict(request.headers)`.
  The `claimed_org_id` SHALL be sourced from the inbound
  `X-Org-Id` request header set by the caller (LibreChat hook,
  knowledge-mcp proxy, docs-app). Sourcing claimed_org_id from a
  caller-set header is symmetrical with the existing internal-secret
  path on the same middleware (which sources `caller_service` from
  `X-Caller-Service`, lines 525-544) and keeps the trust boundary
  identical: portal-api is the authority, the header is a hint.
- **REQ-1.4:** IF the inbound JWT path lacks a non-empty `X-Org-Id`
  header, THE middleware SHALL return HTTP 400 with
  `{"error": "missing_org_id"}` and emit a structlog warning with
  `event="missing_org_id"`. This is a loud config error rather than
  a silent fail-open.
- **REQ-1.5:** IF `IdentityAsserter.verify` returns
  `verified=false`, THE middleware SHALL return HTTP 403 with
  `{"error": "identity_assertion_failed"}` (matching the existing
  internal-secret deny shape, lines 425-428) AND SHALL increment
  `cross_org_rejected_total` AND SHALL emit
  `event="identity_assertion_failed"` with the portal-side reason.
- **REQ-1.6:** WHEN `IdentityAsserter.verify` returns
  `verified=true`, THE middleware SHALL pin
  `request.state.verified_caller = VerifiedCaller(
    user_id=result.user_id, org_id=result.org_id)` from the portal
  response, NOT from JWT claims. This replaces lines 508-511 of
  the current implementation.
- **REQ-1.7:** Existing JWT validity checks (signature, audience,
  issuer, `sub` presence — `_decode_jwt`, lines 189-238) SHALL be
  preserved unchanged. The fix is org-resolution only; the JWT itself
  is still cryptographically verified against Zitadel JWKS.
- **REQ-1.8:** Rate-limiting (`_rate_limit_key`, lines 303-306) SHALL
  be preserved unchanged. The JWT path key still hashes
  `auth.sub`; no change to the rate-limit identity.

### REQ-2: klai-connector — middleware drops resourceowner read

The system SHALL retire the `urn:zitadel:iam:user:resourceowner:id`
read from `klai-connector/app/middleware/auth.py` line 138 and route
the JWT path through `portal-api /internal/identity/verify`.

- **REQ-2.1:** WHEN `AuthMiddleware.dispatch`
  (`klai-connector/app/middleware/auth.py` line 95) processes a Zitadel
  introspection result, THE middleware SHALL stop reading
  `claims.get("urn:zitadel:iam:user:resourceowner:id")` (line 138).
  The literal string SHALL be deleted from the file.
- **REQ-2.2:** AFTER token introspection succeeds AND the audience
  check passes (lines 121-135, unchanged), THE middleware SHALL call
  `klai_identity_assert.IdentityAsserter.verify` with
  `caller_service="klai-connector"`,
  `claimed_user_id=claims.get("sub")`,
  `claimed_org_id=<org-id-from-header>`,
  `bearer_jwt=<the-bearer-token>`,
  `request_headers=dict(request.headers)`.
  The `claimed_org_id` SHALL be sourced from the inbound `X-Org-Id`
  request header set by the caller (portal-api when proxying OAuth
  callbacks; the connector UI when invoking sync endpoints). Same
  rationale as REQ-1.3.
- **REQ-2.3:** IF the request lacks a non-empty `X-Org-Id` header on
  any non-`/health` non-portal-bypass path, THE middleware SHALL
  return HTTP 400 with `{"error": "missing_org_id"}`. Portal-bypass
  callers (line 113, `from_portal=True`) are exempt because they
  already set `org_id=None` per the existing contract.
- **REQ-2.4:** IF `IdentityAsserter.verify` returns
  `verified=false`, THE middleware SHALL return HTTP 403 with
  `{"error": "identity_assertion_failed"}` (NOT 401 — the user has
  a valid Zitadel token, but no membership for the claimed org;
  that is an authorization failure, not authentication).
- **REQ-2.5:** WHEN `IdentityAsserter.verify` returns
  `verified=true`, THE middleware SHALL set
  `request.state.org_id = str(result.org_id)` from the portal
  response, NOT from the JWT claim. This replaces line 143.
- **REQ-2.6:** THE token introspection cache (lines 32-72) SHALL be
  preserved unchanged. The cache stores Zitadel introspection
  results, not portal verify results; the `IdentityAsserter` library
  has its own 60s cache for the verify call.
- **REQ-2.7:** THE `_portal_secret` bypass (lines 107-116) SHALL be
  preserved unchanged. Portal-api service-to-service calls continue
  to bypass the verify path because portal-api IS the verifier; a
  self-recursion would be circular.
- **REQ-2.8:** THE `IdentityAsserter` SHALL be instantiated at module
  level following the same lazy pattern as
  `klai-retrieval-api/retrieval_api/middleware/auth.py` lines 400-410
  (singleton, constructed on first use). Settings already expose
  `portal_api_url` and `portal_caller_secret`; reuse those.

### REQ-3: ast-grep rule glob extension

The system SHALL extend the `files:` glob in
`rules/no-zitadel-resourceowner-claim.yml` (the file introduced by
SPEC-002 REQ-5.2) to include `klai-retrieval-api/` and
`klai-connector/` source paths AFTER REQ-1 and REQ-2 land.

- **REQ-3.1:** THE `files:` list SHALL add:
  - `klai-retrieval-api/retrieval_api/**/*.py`
  - `klai-connector/app/**/*.py`
- **REQ-3.2:** THE `ignores:` list SHALL add:
  - `klai-retrieval-api/tests/**/*.py`
  - `klai-connector/tests/**/*.py`
- **REQ-3.3:** THE rule extension SHALL land in the SAME PR as the
  service refactor for that service. Landing the glob extension
  before the service refactor breaks CI on the existing
  resourceowner reads; landing it after creates a temporal window
  where reintroduction is undetected.
- **REQ-3.4:** AFTER both REQs ship, a final cross-service grep
  `grep -rn "urn:zitadel:iam:user:resourceowner" \
   klai-portal/backend/ klai-scribe/ klai-retrieval-api/ \
   klai-connector/ klai-knowledge-mcp/ klai-libs/` SHALL return ONLY
  test fixtures and historical SPEC document references. Any active
  code reference SHALL fail the CI ast-grep job.

### REQ-4: Grafana alerts — extend identity-verify failure observability

The system SHALL extend the Grafana alert family introduced by
SPEC-002 REQ-6.2 to cover deny rates on the two new caller-services.

- **REQ-4.1:** A new alert rule with UID prefix `spec-iam-003-`
  (compliant with the 40-char limit per
  `.claude/rules/klai/pitfalls/process-rules.md`
  `grafana-uid-40-char-limit`) SHALL be added at
  `deploy/grafana/provisioning/alerting/identity-verify-failures-iam-003.yml`.
- **REQ-4.2:** THE alert SHALL fire WHEN the rate of
  `event="identity_assertion_failed" AND service:retrieval-api`
  events exceeds 5 per minute over a 5-minute rolling window.
- **REQ-4.3:** A second rule SHALL fire WHEN the rate of
  `event="identity_assertion_failed" AND service:klai-connector`
  events exceeds 5 per minute over a 5-minute rolling window.
  klai-connector deny rates are typically 0; a sudden non-zero rate
  signals either deploy-window misconfiguration or a probing
  attacker.
- **REQ-4.4:** Alert payloads SHALL include the
  `service`, `caller_service`, and `reason` fields so the on-call
  engineer can immediately see whether the deny is `no_membership`
  (legitimate, but unexpected at scale) versus `invalid_jwt`
  (likely deploy or Zitadel issue).
- **REQ-4.5:** Existing
  `cross_org_rejected_total{reason=identity_assertion_failed}`
  Prometheus counter on retrieval-api (line 420) SHALL stay. The
  Grafana rule MAY use either the structlog event stream
  (VictoriaLogs) or the Prometheus counter; prefer the counter for
  alerting because it does not depend on log retention.

### REQ-5: Tests — JWT-without-resourceowner regression coverage

The system SHALL ship a regression test on every JWT-bound entry
path that currently consumes the resourceowner claim.

- **REQ-5.1:** `klai-retrieval-api/tests/` SHALL contain a test that
  POSTs to `/retrieve` with:
  - A Zitadel JWT that has valid signature, valid audience, valid
    `sub`, AND lacks `urn:zitadel:iam:user:resourceowner:id`
  - Header `X-Org-Id: <user-org-id>`
  - Mock `IdentityAsserter.verify` to return
    `verified=true, user_id=<sub>, org_id=<expected>`
  - Assert HTTP 200
  - Assert `request.state.verified_caller` is pinned from the
    portal response, NOT from the JWT
- **REQ-5.2:** `klai-retrieval-api/tests/` SHALL contain a test where
  the same JWT (no resourceowner) is sent WITHOUT `X-Org-Id`
  header. Assert HTTP 400 `{"error": "missing_org_id"}`.
- **REQ-5.3:** `klai-retrieval-api/tests/` SHALL contain a test where
  the JWT INCLUDES a (legacy) resourceowner claim that DOES NOT
  match the `X-Org-Id` header. Assert the claim is ignored and the
  membership lookup runs (mock the asserter to return verified=true
  for the header value). Mirror SPEC-002 acceptance scenario A6.
- **REQ-5.4:** `klai-connector/tests/` SHALL contain a test that
  exercises the OAuth callback path with a Zitadel-introspected
  token whose claims dict lacks `urn:zitadel:iam:user:resourceowner:id`.
  Mock the introspection and the IdentityAsserter; assert HTTP 200
  and `request.state.org_id == result.org_id` from the asserter.
- **REQ-5.5:** `klai-connector/tests/` SHALL contain a test that
  asserts HTTP 400 when `X-Org-Id` is missing on a non-portal-bypass
  call.
- **REQ-5.6:** `klai-connector/tests/` SHALL preserve the existing
  portal-bypass test (no `X-Org-Id` required when
  `from_portal=True`).
- **REQ-5.7:** Test fixtures that build JWTs SHALL NOT include the
  resourceowner claim except in the SPEC-002 A6-mirror regression
  test (REQ-5.3).

### REQ-6: Migration / rollout

- **REQ-6.1:** Land in this order on `main`:
  1. SPEC-002 must be live in production (PR #545 deployed) before
     any work in this SPEC starts. SPEC-002 portal-api changes are
     the prerequisite — REQ-1 and REQ-2 below depend on
     `/internal/identity/verify` resolving via membership.
  2. retrieval-api: REQ-1 + REQ-3 (the glob extension for retrieval
     paths only) + REQ-5.1-5.3 in a single PR.
  3. klai-connector: REQ-2 + REQ-3 (the glob extension for connector
     paths) + REQ-5.4-5.6 in a single PR.
  4. REQ-4 Grafana alerts as a follow-up commit on klai-infra (or
     wherever `deploy/grafana/provisioning/alerting/` is tracked).
- **REQ-6.2:** Service deploys: retrieval-api FIRST (it is stateless;
  rollback is a revert + redeploy with no data implications);
  klai-connector SECOND (it has Alembic migrations queued in normal
  development that are unrelated to this SPEC, but the deploy
  workflow ordering matters because the connector entrypoint
  auto-runs `alembic upgrade head` per the
  `alembic-stamped-past-skipped-migration` lesson — a failed deploy
  here can stall later infra work).
- **REQ-6.3:** Each service deploy SHALL be observed for at least 30
  minutes before the next service deploys, to ensure the per-service
  identity-assertion-failure metric (REQ-4) does not spike.
- **REQ-6.4:** Rollback procedure: per service, revert the merge
  commit and redeploy. The two services do not share any files; no
  co-dependency between them. SPEC-002 must remain live for the
  rolled-back service to keep working — never roll back SPEC-002
  while SPEC-003 is partially deployed.
- **REQ-6.5:** No DB migration. No SOPS env-var change. No Zitadel
  console operation.

### REQ-7: Out-of-scope inventory + audit attestation

The system SHALL document the cross-service audit completed during
this SPEC's authoring.

- **REQ-7.1:** As of 2026-05-08, the only two active in-tree
  consumers of `urn:zitadel:iam:user:resourceowner:id` outside test
  fixtures and historical SPEC documents are
  `klai-retrieval-api/retrieval_api/middleware/auth.py` (this SPEC,
  REQ-1) and `klai-connector/app/middleware/auth.py` (this SPEC,
  REQ-2). No other Klai service references the claim in a
  load-bearing path. SPEC-002 already removed the references from
  portal-api, scribe-api, knowledge-mcp, and the
  `klai_identity_assert` library.
- **REQ-7.2:** AFTER this SPEC's REQ-1, REQ-2, and REQ-3 land, the
  ast-grep rule SHALL prevent reintroduction at PR time. Future
  audits MAY rely on the rule and skip a manual cross-service grep.
- **REQ-7.3:** Out of scope for this SPEC:
  - Migrating retrieval-api or klai-connector behind portal-api BFF
    (analogous to SPEC-002 REQ-2 for scribe). That is a separate
    architecture decision that may or may not happen; the current
    SPEC's design preserves direct-mode for both services.
  - Removing the `klai_identity_assert` library dependency from
    either service. Both services USE it more after this SPEC, not
    less.
  - Adding the `urn:zitadel:iam:user:resourceowner` scope to BFF
    authorize requests. Explicitly NOT done — would re-introduce
    dependency on an IdP-specific claim that the rule (and now
    SPEC-002) says not to use.

## 6. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Adding a portal `/internal/identity/verify` round-trip on every retrieval-api JWT request adds latency to the chat hot path | Med | Cache (60s TTL via `IdentityAsserter`) absorbs >99% of calls. First-hit cost ≈ 5-15ms (single membership query at portal-api; cached locally for 60s). Measure on staging under realistic load before production deploy; rollback via REQ-6.4 if p95 increases by >10ms. |
| klai-connector OAuth callback adds verify roundtrip on top of the existing OAuth provider roundtrip | Low | OAuth callbacks are user-initiated, infrequent (one per provider connect), and already round-trip Google/Microsoft/Notion. One extra portal call is negligible. The `IdentityAsserter` cache is irrelevant here because callbacks are first-time. |
| `X-Org-Id` header sourcing means a misconfigured caller (LibreChat hook, docs-app) sends the wrong header | Low | The header is a hint; portal-api still validates membership against the JWT's `sub`. A wrong `X-Org-Id` produces `verified=false` (no_membership for that user against that org), not a privilege escalation. The deny path is loud (REQ-4 alert). |
| Multi-org users discover edge cases in `_resolve_active_membership_org_slug` they couldn't reach before from these surfaces | Low | Helper has been in production since SPEC-001 and is exercised by every internal-secret call into retrieval-api today. SPEC-002 also exercises it for portal-api JWT paths. Behaviour is well-understood. |
| Partial deploy: SPEC-003 retrieval-api deployed but ast-grep glob extension forgotten | Low | REQ-3.3 makes the glob extension a same-PR concern. CI fails the PR if the rule is not extended. |
| ast-grep rule glob extension lands BEFORE the service refactor | Low | REQ-3.3 explicitly orders glob-after-refactor. Any PR that lands the glob first will fail the rule on its own diff. |

## 7. Out of scope

- Migrating retrieval-api / klai-connector to BFF-proxy mode
  (SPEC-002 REQ-2 pattern). Tracked as future work; not needed
  to fix the bug class addressed here.
- Removing the local Zitadel introspection cache from
  klai-connector. The cache is independent of the org-resolution
  fix; touching it is a separate optimisation.
- Removing the local Zitadel JWKS cache from retrieval-api. Same
  rationale.
- Adding new `caller_service` allowlist entries beyond
  `retrieval-api` and `klai-connector` to
  `klai_identity_assert.KNOWN_CALLER_SERVICES`. They are already
  present (used by the existing internal-secret path).
- Zitadel-side configuration changes. None required.
- SOPS env / deploy-compose changes. None required.
- Renaming `X-Org-Id` to a verified header (e.g.
  `X-Klai-Verified-Org-Id` per SPEC-002 REQ-2.3). The verified
  header pattern only applies behind the BFF; for direct-mode
  services the inbound header is a hint, not a verified value.

## 8. Traceability

| REQ | Files affected | Test files |
|---|---|---|
| REQ-1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8 | `klai-retrieval-api/retrieval_api/middleware/auth.py` (lines 61, 86, 339-353, 431-512) | `klai-retrieval-api/tests/test_auth_middleware.py` (new or extended), `klai-retrieval-api/tests/test_request_id_validation.py` (header propagation regression check) |
| REQ-2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8 | `klai-connector/app/middleware/auth.py` (lines 32-144) | `klai-connector/tests/test_auth_middleware.py` (new or extended) |
| REQ-3 | `rules/no-zitadel-resourceowner-claim.yml` (created by SPEC-002 REQ-5.2) | CI ast-grep job |
| REQ-4 | `deploy/grafana/provisioning/alerting/identity-verify-failures-iam-003.yml` (new) | manual Grafana review on staging |
| REQ-5 | `klai-retrieval-api/tests/test_auth_middleware.py`, `klai-connector/tests/test_auth_middleware.py` | (the tests themselves) |
| REQ-6 | `.github/workflows/retrieval-api.yml`, `.github/workflows/klai-connector.yml` | manual deploy observation |
| REQ-7 | this SPEC (audit attestation only) | none |

## 9. References

- SPEC-SEC-IDENTITY-ASSERT-002 (`.moai/specs/SPEC-SEC-IDENTITY-ASSERT-002/spec.md`) — predecessor; PR #545
- SPEC-SEC-IDENTITY-ASSERT-001 (`.moai/specs/SPEC-SEC-IDENTITY-ASSERT-001/spec.md`) — original threat model
- SPEC-AUTH-008 — BFF model (referenced for trust-boundary alignment, not extended)
- `.claude/rules/klai/platform/zitadel.md` lines 99-100 — claim unreliability rule
- `.claude/rules/klai/pitfalls/process-rules.md` `grafana-uid-40-char-limit` — UID convention for REQ-4.1
- `.claude/rules/klai/pitfalls/process-rules.md` `alembic-stamped-past-skipped-migration` — referenced in REQ-6.2
- `klai-retrieval-api/retrieval_api/middleware/auth.py` — REQ-1 target
- `klai-connector/app/middleware/auth.py` — REQ-2 target
- `klai-portal/backend/app/services/identity_verifier.py` — `verify_identity_claim` and `_resolve_active_membership_org_slug` consumed via `IdentityAsserter` library
- `klai-libs/identity-assert/...` — the `IdentityAsserter` library both services already depend on
- Zitadel docs: https://zitadel.com/docs/apis/openidoauth/scopes
- Zitadel docs: https://zitadel.com/docs/apis/openidoauth/claims
