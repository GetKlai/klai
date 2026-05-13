# SPEC-SEC-IDENTITY-ASSERT-002 — Membership-authoritative identity, BFF-verified ingest

Status: Draft
Author: Mark Vletter (with assistant)
Supersedes parts of: SPEC-SEC-IDENTITY-ASSERT-001 (REQ-1.3, REQ-3.2, REQ-3.5)
Related: SPEC-AUTH-008, SPEC-SEC-INTERNAL-001
Created: 2026-05-08

---

## 1. Background

SPEC-SEC-IDENTITY-ASSERT-001 introduced `/internal/identity/verify`
on portal-api as the source of truth for "is this claimed (user, org)
tuple authentic and authorised?". The portal-api implementation
chose to enforce, on every JWT-bound call, that the JWT's
`urn:zitadel:iam:user:resourceowner:id` claim equals the caller's
`claimed_org_id` (REQ-1.3). Scribe-api was wired to read that same
claim from its incoming JWT and pass it as `claimed_org_id`
(REQ-3.2 + REQ-3.5).

In production this never worked. Three independent reasons converged:

1. **Klai's BFF requests the wrong scope set.** `bff_oidc.py` requests
   only `openid profile email offline_access`. Per Zitadel's spec
   (https://zitadel.com/docs/apis/openidoauth/scopes) the
   `urn:zitadel:iam:user:resourceowner:id` claim is emitted **only**
   when scope `urn:zitadel:iam:user:resourceowner` (or
   `urn:zitadel:iam:org:id:{id}`) is in the authorize request. Klai
   requests neither. The claim is therefore absent from every Klai
   access token.
2. **Klai already documented this as unreliable.**
   `.claude/rules/klai/platform/zitadel.md` lines 99-100 state:
   "Never use `urn:zitadel:iam:user:resourceowner:id` — not always
   present. Use `sub` (OIDC subject) → `portal_users` → `portal_orgs`
   join for reliable org resolution." SPEC-SEC-IDENTITY-ASSERT-001
   landed in direct contradiction to that rule.
3. **VictoriaLogs evidence.** Between 2026-04-01 and 2026-05-08 the
   `caller_service:scribe` path returned `verified=true` zero times.
   Every attempt produced `reason=invalid_jwt` because the
   resourceowner equality check tripped. The latency was hidden:
   nobody actually exercised `/app/transcribe` in production until
   Mark did on 2026-05-08, surfacing the regression for all tenants
   simultaneously.

The conclusion is not "fix Zitadel". The conclusion is that the
resourceowner-equality check is architecturally wrong for Klai's
data-model and threat-model, and must be retired.

## 2. Why the resourceowner check is non-load-bearing

The threat model in SPEC-SEC-IDENTITY-ASSERT-001 (lines 326-396)
defines three scenarios:

- **S1 — malicious internal-secret holder.** Mitigated by `sub ==
  claimed_user_id` + caller-asserted-header rejection. Resourceowner
  irrelevant.
- **S2 — stolen internal-secret.** Mitigated by `sub ==
  claimed_user_id` + the membership lookup that already runs when
  `bearer_jwt is None`. Resourceowner irrelevant.
- **S3 — cross-tenant write via knowledge-mcp.** Mitigated by membership
  resolution on the verified `sub`. Resourceowner irrelevant.

The SPEC also lists explicit non-goals (line 388):
> Defending against an attacker who has a valid Zitadel JWT for the
> victim user. This is the Zitadel auth boundary — out of scope here.

So the resourceowner-equality check (`jwt.resourceowner ==
claimed_org_id`) does not close any in-scope attack vector. It is a
parallel "the user's primary org should equal the target org"
constraint that:

- Conflates two distinct concepts: **identity** (sub) and **active org
  context** (claimed_org_id). Zitadel's resourceowner is the user
  account's PRIMARY org, immutable per user-account, not "the org the
  user wants to act on right now".
- Breaks Klai's multi-org data-model. `portal_users` is `(zitadel_user_id,
  org_id)` — one user can be an active member of multiple orgs. Their
  Zitadel resourceowner is fixed at one of those (or none, in the
  platform-org case). The equality check rejects legitimate operations
  on non-primary orgs.
- Couples Klai to one specific IdP behaviour. Future SAML federation,
  custom OIDC IdPs, or social login may not produce this claim at all.

The `sub == claimed_user_id` check (the actual cross-user defence) is
unaffected by this SPEC and stays.

## 3. Goals

1. Make `/app/transcribe` (and every other JWT-bound `verify_identity_claim`
   path) work for every authenticated portal user, on every subdomain,
   regardless of Zitadel claim emission.
2. Make `portal_users` membership the single authoritative source for
   "which orgs can this user act on", aligned with the existing
   `zitadel.md` rule.
3. Reduce scribe-api complexity by removing its own JWT-decode +
   portal-roundtrip path. Scribe becomes a downstream that trusts
   portal-api's BFF identity assertion.
4. Preserve all in-scope threat coverage from SPEC-SEC-IDENTITY-ASSERT-001.

## 4. Threat-model addendum

This SPEC inherits the threat model from
SPEC-SEC-IDENTITY-ASSERT-001 §"Threat Model" verbatim. Two additions:

### Addendum-1: BFF as identity boundary

Portal-api BFF is already trusted as the orchestrator of the user's
session (SPEC-AUTH-008). Forwarding portal-verified identity headers
to downstream services on `klai-net` is consistent with how the BFF
already forwards `Authorization`. The trust boundary does not widen.

### Addendum-2: scribe direct-mode removal

Scribe-api currently accepts external Bearer tokens directly (no
portal-api involvement). This SPEC removes that path. After this
SPEC ships, scribe-api accepts only requests from portal-api BFF
(authenticated via `X-Internal-Secret`). The threat surface "external
caller with a valid Zitadel JWT bypasses BFF and writes to scribe"
disappears entirely; that scenario was already explicitly out of the
v1 threat model (line 388) but removing the path closes it
incidentally.

## 5. Requirements

### REQ-1: portal-api `/internal/identity/verify` — drop resourceowner equality

The system SHALL retire the JWT-resourceowner equality check from
`verify_identity_claim`. Membership lookup becomes the authoritative
org-resolver for both JWT-bound and JWT-less calls.

- **REQ-1.1:** WHEN `bearer_jwt` is present in the request body,
  THE endpoint SHALL validate JWT signature + issuer + expiry + `sub`
  presence (unchanged from v1 REQ-1.3) AND SHALL require
  `jwt.sub == claimed_user_id`. IF mismatch THE endpoint SHALL return
  HTTP 403 with `reason="jwt_identity_mismatch"`.
- **REQ-1.2:** THE endpoint SHALL NO LONGER read or compare
  `urn:zitadel:iam:user:resourceowner:id`. The claim
  SHALL be ignored even if present. The `_ZITADEL_RESOURCEOWNER_CLAIM`
  constant in `klai-portal/backend/app/services/identity_verifier.py`
  SHALL be deleted; reads of the claim in any other module within
  Klai (knowledge-mcp, scribe-api, retrieval-api) SHALL be deleted in
  the same release.
- **REQ-1.3:** AFTER the `sub` check passes, THE endpoint SHALL resolve
  the org via `_resolve_active_membership_org_slug(zitadel_user_id=
  jwt.sub, zitadel_org_id=claimed_org_id)` — the same helper used
  today for the `bearer_jwt is None` path. On no match THE endpoint
  SHALL return HTTP 403 with `reason="no_membership"`. On match THE
  evidence field SHALL be `"jwt"` (the JWT proves authenticity; the
  membership lookup proves authorisation).
- **REQ-1.4:** THE response shape SHALL be unchanged from v1 REQ-1.1.
  Only the internal logic changes; the `evidence` field values
  remain `"jwt"` | `"membership"` | `"partner_key"` | `"tenant_only"`.
- **REQ-1.5:** Cache semantics from v1 REQ-1.5 / REQ-1.6 are
  unchanged. The cache key
  `(caller_service, claimed_user_id, claimed_org_id, evidence)`
  remains valid; the only thing that changes is which code path
  populates it.
- **REQ-1.6:** The "fast path" from v1 REQ-3.5 (JWT-resourceowner
  matches single membership → skip portal-api roundtrip) SHALL be
  retired. All callers go through `/internal/identity/verify`.
  Cache (REQ-1.5, 60s TTL) absorbs the latency hit; the median
  steady-state cost stays under the v1 latency budget.

### REQ-2: portal-api BFF proxy — verify-before-forward

The system SHALL move identity verification into the BFF proxy
boundary for every JWT-bound upstream that today requires
`/internal/identity/verify`.

- **REQ-2.1:** WHEN portal-api proxies a request from
  `/api/scribe/*` or `/api/docs/*` (the BFF endpoints in
  `klai-portal/backend/app/api/proxy.py`), THE proxy SHALL call
  `verify_identity_claim` IN-PROCESS (not via HTTP) with
  `caller_service="portal-api"`,
  `claimed_user_id=session.user_id`,
  `claimed_org_id=session.org_id`,
  `bearer_jwt=session.access_token`.
- **REQ-2.2:** IF `verify_identity_claim` denies, THE proxy SHALL
  return HTTP 403 to the frontend with a body that includes the
  reason code AND SHALL NOT forward the request to the upstream.
- **REQ-2.3:** IF `verify_identity_claim` allows, THE proxy SHALL
  forward the request to the upstream WITH the existing
  `Authorization: Bearer <session.access_token>` header AND ADD
  `X-Klai-Verified-User-Id: <decision.user_id>`,
  `X-Klai-Verified-Org-Id: <decision.org_id>`,
  `X-Klai-Verified-Org-Slug: <decision.org_slug>`,
  `X-Internal-Secret: <settings.internal_secret>`. The proxy SHALL
  strip any inbound `X-Klai-Verified-*` headers from the client
  request before adding its own (defence-in-depth — never trust a
  client-asserted `Verified-*` value).
- **REQ-2.4:** THE proxy SHALL emit a structlog line at level `info`
  with `event="bff_proxy_verified"` AND fields
  `caller_service`, `path`, `verified` (bool), `evidence`,
  `latency_ms`. On deny THE log SHALL include `reason`.
- **REQ-2.5:** THE in-process `verify_identity_claim` call SHALL
  reuse the existing Redis cache (REQ-1.5). Calls within the 60s
  TTL window SHALL NOT re-hit the database.
- **REQ-2.6:** THE proxy SHALL apply this verification BEFORE any
  request body is read or streamed upstream. A denied call SHALL
  not consume the upstream's request budget.

### REQ-3: scribe-api — accept BFF-verified identity, remove direct-mode

The system SHALL retire scribe-api's standalone JWT-decode path.
Scribe accepts only requests that arrive through portal-api BFF.

- **REQ-3.1:** `klai-scribe/scribe-api/app/core/auth.py::get_authenticated_caller`
  SHALL be rewritten. The new contract: required headers are
  `X-Internal-Secret` (matches `settings.portal_internal_secret`),
  `X-Klai-Verified-User-Id`, `X-Klai-Verified-Org-Id`. WHEN all three
  are present and the secret matches, THE handler SHALL build
  `CallerIdentity(user_id=<header>, org_id=<header>)` and return.
  Missing or mismatching → HTTP 401.
- **REQ-3.2:** THE existing local Zitadel-JWKS fetcher
  (`_get_jwks`, `_fetch_jwks`, `_decode_zitadel_token`,
  `_validate_sub`) SHALL be deleted. Scribe-api SHALL NOT decode JWTs
  directly. The `_jwks_cache` module-level state SHALL be removed.
- **REQ-3.3:** THE module-level `IdentityAsserter` singleton (`_asserter`
  in `auth.py`) SHALL be deleted. Scribe-api SHALL NOT call portal-api
  `/internal/identity/verify` itself — portal-api has already verified
  by the time the request arrives.
- **REQ-3.4:** THE `klai_identity_assert` library import and dependency
  SHALL be removed from scribe-api (`pyproject.toml`,
  `requirements.txt`, Dockerfile path-deps). The library remains in
  use by knowledge-mcp and retrieval-api; only scribe loses the
  dependency.
- **REQ-3.5:** THE `Authorization: Bearer <jwt>` header that
  portal-api forwards (REQ-2.3) SHALL be ignored by scribe-api's
  auth path. Optionally scribe MAY pass the JWT through to a
  downstream Vexa/Whisper transcription provider (which has its
  own auth model); that is unchanged by this SPEC. The header is
  not consulted for identity decisions.
- **REQ-3.6:** Existing fail-closed semantics SHALL be preserved:
  any deviation (missing internal-secret, wrong secret, missing
  verified-headers) returns HTTP 401 with no information leakage.
  The Dutch error message `"Ongeldig of verlopen token"` SHALL be
  replaced with `"unauthenticated"` (English, machine-readable —
  this is now a service-internal error, not user-facing).

### REQ-4: knowledge-mcp + retrieval-api — inherit the fix

The system SHALL apply REQ-1's `verify_identity_claim` change
uniformly. Knowledge-mcp and retrieval-api MUST require zero
behavioural changes; they receive the fix transparently because the
verification logic is centralised.

- **REQ-4.1:** `klai-knowledge-mcp` SHALL continue to use
  `klai_identity_assert.IdentityAsserter` unchanged. Its calls to
  `/internal/identity/verify` already work for users whose JWT
  contains the resourceowner claim AND for users without it once
  REQ-1 lands. No change in this service.
- **REQ-4.2:** `klai-retrieval-api` SHALL continue to use
  `klai_identity_assert.IdentityAsserter` unchanged. Same rationale.
- **REQ-4.3:** Both services' contract tests against the v1 endpoint
  semantics SHALL be updated where they assert on the
  `jwt_identity_mismatch` reason code path that depended on
  resourceowner. New tests cover the membership-only path.
  See `acceptance.md`.
- **REQ-4.4:** WHEN a future SPEC moves these services behind
  portal-api BFF (analogous to REQ-2 for scribe), they MAY adopt
  the `X-Klai-Verified-*` header pattern. That is out of scope
  for this SPEC; today they remain on the
  `IdentityAsserter` → portal-api path.

### REQ-5: Klai-rule alignment

The system SHALL re-affirm `.claude/rules/klai/platform/zitadel.md`
lines 99-100 by leaving the rule intact AND removing all in-tree
violations.

- **REQ-5.1:** Grep `urn:zitadel:iam:user:resourceowner` across
  `klai-portal/backend/`, `klai-scribe/`, `klai-retrieval-api/`,
  `klai-knowledge-mcp/`, `klai-libs/identity-assert/`,
  `klai-connector/`, `klai-mailer/`. The only acceptable remaining
  references are: (a) test fixtures that build JWTs INCLUDING this
  claim to verify it is harmless when present (regression guard),
  (b) the rule file itself, (c) historical SPEC documents. All
  active code references SHALL be deleted.
- **REQ-5.2:** Add an `ast-grep` rule
  `rules/no-zitadel-resourceowner-claim.yml` that fails CI when a
  Klai service file references the claim string outside an allow-list
  of test files. The rule prevents reintroduction.
- **REQ-5.3:** The `zitadel.md` rule SHALL be updated with a note
  pointing to this SPEC as the canonical implementation.

### REQ-6: Operational + observability

- **REQ-6.1:** The `event="identity_verify_decision"` log
  (v1 REQ-1.7) SHALL stay. A new field `evidence_path` is added with
  values `"jwt+membership"` (REQ-1.3 happy path), `"membership"`
  (bearer_jwt=None path), `"partner_key"`, `"tenant_only"`.
- **REQ-6.2:** A Grafana alert SHALL fire when
  `caller_service:scribe AND verified:true` is zero for a 30-minute
  rolling window during business hours. This alert would have
  caught the v1 regression on day one. Threshold tuned after
  one-week observation.
- **REQ-6.3:** Existing
  `cross_org_rejected_total{reason=identity_assertion_failed}`
  metric in retrieval-api stays. Prometheus dashboards unchanged.
- **REQ-6.4:** A retro entry SHALL be added to
  `.claude/rules/klai/pitfalls/process-rules.md` capturing the
  pattern: "claim-based authorization without verifying the claim is
  actually emitted in production = latent bug". Specifically:
  > Before designing an auth check around a JWT claim, verify the
  > scope set actually requests that claim AND there exists a
  > production-traffic test that exercises the path. If neither
  > is true, the design is not yet validated regardless of test
  > coverage.

### REQ-7: Migration + rollout

- **REQ-7.1:** Land in this order on `main`:
  1. portal-api: REQ-1 amendment (drop resourceowner equality,
     route everything through membership).
  2. portal-api: REQ-2 BFF proxy verification.
  3. scribe-api: REQ-3 (delete direct-mode + JWT decoding) AS A
     SINGLE PR — partial deploy of REQ-3 before REQ-2 lands would
     break scribe entirely.
  4. REQ-5 grep+ast-grep + REQ-6 alert in follow-up commits.
- **REQ-7.2:** Each service's deploy workflow SHALL be triggered in
  the order above. The scribe-api deploy (step 3) MUST follow a
  successful portal-api deploy (steps 1-2). A staggered deploy is
  acceptable.
- **REQ-7.3:** Rollback procedure: if step 1 or step 2 misbehaves,
  revert the merge commit and redeploy. Scribe (step 3) is dependent;
  rollback step 3 first, then step 1/2.
- **REQ-7.4:** No DB migration required. No SOPS env-var change. No
  Zitadel console operation.

## 6. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `verify_identity_claim` in-process call from BFF proxy adds latency to every `/api/scribe/*` request | Med | Cache (60s TTL) absorbs > 99% of calls. First-hit cost ≈ 5-15ms (single membership query, no JWT signature re-check needed because session.access_token is already validated by BFF). Acceptable. |
| Multi-org users discover edge cases in `_resolve_active_membership_org_slug` they couldn't reach before | Low | Helper has been in production since v1 SPEC, exercised by every `bearer_jwt is None` path. Behaviour known. |
| scribe-api forgets to validate `X-Internal-Secret` and trusts `X-Klai-Verified-*` from any caller | Low (dev mistake) | REQ-3.1 makes the secret check mandatory. Tests in `acceptance.md` verify forged headers without secret are rejected. |
| Future feature wants direct external scribe access (B2B partner, public API) | Low (no use-case today) | Re-introduce direct-mode behind explicit flag in a separate SPEC when the use-case lands. Re-architecting from the BFF baseline is cheaper than maintaining always-on direct-mode for a use-case that doesn't exist. |
| Knowledge-mcp / retrieval-api have edge case where REQ-1 amendment changes deny-reason from `jwt_identity_mismatch` to `no_membership` and a downstream relies on the exact reason code | Low | REQ-4.3 audit + amend tests. Reason codes are internal; no public client depends on them. |

## 7. Out of scope

- Adding `urn:zitadel:iam:user:resourceowner` scope to BFF authorize
  request. Explicitly NOT done — would re-introduce dependency on an
  IdP-specific claim that the rule says not to use.
- BFF-verified-headers extension to knowledge-mcp and retrieval-api.
  Tracked as future work; current `IdentityAsserter` path keeps working.
- Scribe public API (external Bearer-token callers). No use-case today;
  build when needed.
- Zitadel-side configuration changes. None required.
- SOPS env / deploy-compose changes. None required.

## 8. Traceability

| REQ | Files affected | Test files |
|---|---|---|
| REQ-1.1, 1.2, 1.3, 1.4, 1.5 | `klai-portal/backend/app/services/identity_verifier.py` | `klai-portal/backend/tests/test_identity_verifier.py`, `tests/test_internal_identity_verify.py`, `tests/test_identity_verify_contract.py` |
| REQ-1.6 | `klai-portal/backend/app/services/identity_verifier.py`, `klai-scribe/scribe-api/app/core/auth.py` | `klai-scribe/scribe-api/tests/test_auth.py`, `tests/test_identity_assert.py` |
| REQ-2 | `klai-portal/backend/app/api/proxy.py` | `klai-portal/backend/tests/test_proxy_bff_verified.py` (new) |
| REQ-3 | `klai-scribe/scribe-api/app/core/auth.py`, `klai-scribe/scribe-api/pyproject.toml`, `klai-scribe/scribe-api/Dockerfile` | `klai-scribe/scribe-api/tests/test_auth.py` (rewritten) |
| REQ-4 | none (transitive) | `klai-knowledge-mcp/tests/...`, `klai-retrieval-api/tests/...` test updates |
| REQ-5 | grep + new `rules/no-zitadel-resourceowner-claim.yml`, `.claude/rules/klai/platform/zitadel.md` | CI ast-grep job |
| REQ-6 | `klai-portal/backend/app/services/identity_verifier.py` (log field), `deploy/grafana/provisioning/alerting/` | manual Grafana review |

## 9. References

- SPEC-SEC-IDENTITY-ASSERT-001 (`.moai/specs/SPEC-SEC-IDENTITY-ASSERT-001/spec.md`) — predecessor
- SPEC-AUTH-008 — BFF model
- `.claude/rules/klai/platform/zitadel.md` lines 99-100 — claim unreliability rule
- Zitadel docs: https://zitadel.com/docs/apis/openidoauth/scopes
- Zitadel docs: https://zitadel.com/docs/apis/openidoauth/claims
- VictoriaLogs evidence (2026-04-01 → 2026-05-08): zero `caller_service:scribe verified:true` events
- CodeIndex memory: bug `600dae17-6ec3-4d0b-9fbd-6001c7556317` — initial diagnosis
