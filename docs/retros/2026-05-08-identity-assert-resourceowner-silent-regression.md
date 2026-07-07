# 2026-05-08 — `resourceowner` JWT claim was never emitted; every JWT-bound identity check denied for 5 weeks

**Pitfall (now live in):**
- `.claude/rules/klai/pitfalls/process-rules.md` § `claim-emission-vs-claim-consumption (HIGH)`

**Severity:** HIGH — from 2026-04-01 until 2026-05-08, **every** JWT-bound
`/internal/identity/verify` call denied with `invalid_jwt`. This silently broke
scribe transcription (`/app/transcribe`), and the same latent fault sat unfired
in knowledge-mcp and retrieval-api. No crash, no 5xx, no user-facing error — the
feature simply produced nothing for anyone who tried it. It went unnoticed for
5+ weeks because nobody had actually exercised the path in production until Mark
opened `/app/transcribe` on `getklai.getklai.com`.

**SPEC / PRs involved:**
- SPEC-SEC-IDENTITY-ASSERT-001 (landed ~2026-04-01) — REQ-1.3 enforced
  `jwt.sub == claimed_user_id` **AND** `jwt.resourceowner == claimed_org_id` on
  every JWT-bound `/internal/identity/verify` call. This is the regression
  source.
- SPEC-SEC-IDENTITY-ASSERT-002 (PR #545, merged + live 2026-05-08) — made
  `portal_users` membership the authoritative source, retired the resourceowner
  check, moved scribe behind the portal-api BFF proxy.
- SPEC-SEC-IDENTITY-ASSERT-003 (PR #550, merged + live 2026-05-08) — applied the
  same membership-authoritative refactor to retrieval-api and klai-connector.

## What happened

SPEC-SEC-IDENTITY-ASSERT-001 designed the JWT-bound authorization check around
the Zitadel claim `urn:zitadel:iam:user:resourceowner:id`. The intent: prove
both *who* the caller is (`sub`) and *which org* they act on (`resourceowner`),
so a compromised internal caller could not assert "I am Mark on org X" against a
JWT that belonged to a different user or org.

The design was never valid, because Klai's BFF requests only the scope
`openid profile email offline_access`. Per the
[Zitadel scope docs](https://zitadel.com/docs/apis/openidoauth/scopes), the
`resourceowner:id` claim is emitted **only** when the authorize request carries
`urn:zitadel:iam:user:resourceowner` or `urn:zitadel:iam:org:id:{id}` scope.
Klai requests neither. So the claim was absent from every access token — for
every user, on every org, tenant and platform alike.

The SPEC's unit tests built JWT fixtures with the claim baked in, so they passed
green. Production tokens never had it. The check therefore evaluated
`jwt_resourceowner` as absent → failed the `isinstance(..., str)` guard →
returned `invalid_jwt` on 100% of JWT-bound calls.

## Root cause

A JWT-claim-based auth check was designed and merged without confirming (a) that
the requested OIDC scope actually emits the claim, and (b) that a real
production token carries it. Both facts are independent of the test suite;
mocked-claim fixtures establish neither.

Compounding it: Klai's own rule
`.claude/rules/klai/platform/zitadel.md:99-100` already said **"never use
`urn:zitadel:iam:user:resourceowner:id` — not always present."** The SPEC
contradicted a standing rule without justifying the deviation.

## Why nobody noticed for 5 weeks

- The failure was **100% deny**, not partial — but the only thing on the other
  side was a feature (`/app/transcribe`) that no production user had exercised
  yet. A total outage of an unused path looks identical to "path not used."
- There was **no alert on a `verified=true` floor**. A regression that produces
  zero successes is invisible unless something watches for the *absence* of
  success. Nothing did.
- knowledge-mcp and retrieval-api shared the same central verifier, so they
  carried the same latent fault — but their live traffic happened to travel the
  internal-secret path, not the JWT path, so they did not surface it either.

Discovered when Mark became the first user to click `/app/transcribe`, hit a
403, and the investigation ran the VictoriaLogs query
`service:scribe-api AND event:identity_assert_call AND verified:true` — **zero
hits in 5+ weeks**.

## The fix (SPEC-002 + SPEC-003, both live 2026-05-08)

1. **Retired the resourceowner check.** `verify_identity_claim` now validates
   `jwt.sub == claimed_user_id` (the real cross-user defence) and resolves the
   org via `_resolve_active_membership_org_slug(sub, claimed_org_id)` against
   `portal_users`. Membership is the single source of truth for org access —
   which also makes multi-org users work natively (they were previously pinned
   to their one immutable Zitadel resourceowner).
2. **Moved scribe behind the BFF proxy.** portal-api verifies in-process
   *before* forwarding and passes `X-Klai-Verified-User-Id/Org-Id/Org-Slug`
   (gated by `X-Internal-Secret`); inbound `X-Klai-Verified-*` from clients are
   stripped. scribe-api dropped its own JWT-decode + portal-roundtrip entirely —
   the fix removed more code than it added.
3. **Extended the same refactor** to retrieval-api and klai-connector (SPEC-003),
   so no Klai service reads the claim any more. Only explanatory comments remain.
4. **Three defence layers against reintroduction:**
   - **Code** — membership-only, no claim read anywhere.
   - **CI lint** — `rules/no-zitadel-resourceowner-claim.yml` (ast-grep) blocks
     new reads of the claim; its `files:` glob covers all refactored services.
   - **Observability** — Grafana alerts `spec-iam-002-*` / `spec-iam-003-*` fire
     on deny-spikes and on the "verified=true count = 0" floor, so a future
     100%-deny regression pages within a business-hours window instead of
     hiding for weeks.

## Production confirmation (not just tests)

- `event:bff_proxy_verified verified:true evidence_path:jwt+membership` for
  Mark's `user_id 362760545968848902` on `org_id 1` (getklai platform-org),
  latency 0.59–1.51 ms — the exact flow that used to 403.
- `service:retrieval-api AND event:identity_assertion_failed` → 0 hits since
  deploy. Same for klai-connector.
- ast-grep with the full service glob → zero matches. Alerts online.

## Opportunity cost

The scribe transcription feature shipped ~2026-04-01 and returned nothing to
every user who tried it for the following five weeks. Because the failure was
silent, there was no signal to prioritise a fix — the cost was entirely
invisible until a founder happened to be the first real user. The lesson is not
"write more tests" (the tests were green and wrong); it is **an auth check is
not validated until a real production token has traversed it and returned
`verified=true`, and an alert watches for that number falling to zero.**

## If a `spec-iam-002-*` / `spec-iam-003-*` alert fires now

1. Confirm scope: a deny-spike on `identity_verify_decision reason:*` points at
   the membership lookup, not the (now-removed) claim. Check whether the caller's
   `claimed_org_id` matches an **active** row in `portal_users` for that `sub`.
2. `verified=true count = 0` over the window means the whole path is dark again —
   treat as a repeat of this incident: decode a real production access token and
   confirm the BFF is sending `X-Klai-Verified-*` + `X-Internal-Secret`
   correctly, and that portal-api's in-process verify is not denying upstream.
3. Never "fix" it by re-adding a Zitadel claim read — the ast-grep rule will
   block it, and `zitadel.md:99-100` explains why.

## References

- Pitfall: `.claude/rules/klai/pitfalls/process-rules.md` § `claim-emission-vs-claim-consumption`
- Rule: `.claude/rules/klai/platform/zitadel.md:99-100`
- SPECs: `.moai/specs/SPEC-SEC-IDENTITY-ASSERT-002/`, `.moai/specs/SPEC-SEC-IDENTITY-ASSERT-003/`
- [Zitadel — Scopes](https://zitadel.com/docs/apis/openidoauth/scopes) · [Zitadel — Claims](https://zitadel.com/docs/apis/openidoauth/claims)
