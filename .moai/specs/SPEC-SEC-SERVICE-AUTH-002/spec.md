---
id: SPEC-SEC-SERVICE-AUTH-002
version: "0.1"
status: draft
created: 2026-05-02
updated: 2026-05-02
author: Mark Vletter
priority: high
issue_number: 0
predecessor: SPEC-SEC-SERVICE-AUTH-001 (Phase A bootstrap incomplete — see Context)
---

# SPEC-SEC-SERVICE-AUTH-002: Zitadel project + audience + role grant for `svc-litellm`

## Context

SPEC-SEC-SERVICE-AUTH-001 shipped Phase A code (klai-libs/service-auth +
bootstrap script), Phase B receiver-side scope check (retrieval-api), and
Phase C-1 caller migration (LiteLLM hook prefers JWT). The operator runbook
in that SPEC covered:

1. Create the `svc-litellm` machine user in Zitadel.
2. Generate + SOPS-encrypt the client_secret.

It did **not** cover the IdP-side configuration that makes a token actually
acceptable to the receiver:

- `svc-litellm` is currently a free-floating machine user with no project
  membership. Its tokens come back with `aud=["svc-litellm"]` (the user's
  own name) and no `scope` claim — neither is what retrieval-api expects.
- retrieval-api validates `aud == settings.zitadel_api_audience`. With
  `ZITADEL_API_AUDIENCE` empty (current state) the JWT path always 401s
  with `invalid_jwt_audience`.
- Even if audience were aligned, the `@require_scope("klai:internal:retrieval:query")`
  decorator on `/retrieve` would 403 because the token has no `scope` claim
  to match.

In production today (2026-05-02) the LiteLLM hook silently falls back to
`X-Internal-Secret` on every receiver 401 (klai#263 added the receive-side
fallback). Knowledge retrieval works, but the JWT path is dormant and the
SPEC's Phase D cleanup is blocked.

This SPEC closes the IdP-config gap so the JWT path can carry real traffic.

## Requirements (EARS — draft)

### REQ-1 (Receiver Application registered in Klai Platform project)

Each Phase C receiver SHALL be registered as an Application of type "API"
in the Zitadel "Klai Platform" project (id `362771533686374406`):

- App name convention: `svc-<receiver>` (e.g. `svc-retrieval-api`).
- App auth method: JWT bearer.
- The Application's `clientId` becomes the canonical audience that the
  receiver validates JWTs against.

### REQ-2 (Project roles for klai-internal scopes)

The "Klai Platform" project SHALL define one project role per Phase C
scope. Role keys MUST equal the scope strings in
`klai-libs/service-auth/scopes.py`:

- `klai:internal:retrieval:query`
- `klai:internal:ingest:write`
- `klai:internal:ingest:crawl`
- `klai:internal:ingest:read`
- `klai:internal:connector:invoke`
- `klai:internal:portal:callback`
- `klai:internal:purge`

### REQ-3 (User grants — caller → role mapping)

Per Phase C-n migration, the caller machine user SHALL receive a UserGrant
binding it to the relevant role(s) on the Klai Platform project:

| Caller | Role granted | Phase |
|---|---|---|
| `svc-litellm` | `klai:internal:retrieval:query` | C-1 (this SPEC unblocks it) |
| `svc-portal-api` | `klai:internal:ingest:write`, `klai:internal:connector:invoke` | C-2/C-3 |
| `svc-knowledge-ingest` | `klai:internal:portal:callback` | C-4 |
| `svc-klai-connector` | `klai:internal:portal:callback`, `klai:internal:ingest:crawl` | C-5/C-6 |
| `svc-klai-knowledge-mcp` | `klai:internal:ingest:read`, `klai:internal:retrieval:query` | C-7 |

### REQ-4 (Action hook — role-to-scope mapping)

Zitadel's default token does not include role keys in the `scope` claim.
Either:

- (4a) Configure a Zitadel Action hook on `pre_access_token_creation` that
  reads the user's project roles and emits them as space-separated scope
  strings in the `scope` claim, OR
- (4b) Update receiver-side `require_scope` to read from
  `urn:zitadel:iam:org:project:{projectId}:roles` claim instead of the
  standard `scope` claim, and document this divergence from RFC 6749.

REQ-4 MUST pick one approach and apply it consistently to all receivers.
4a keeps receivers RFC-compliant; 4b avoids JS in Zitadel. Decision
documented in this SPEC's Decision Log before implementation.

### REQ-5 (Caller scope-request includes project audience binding)

For each Phase C caller, the `ZitadelTokenClient` invocation SHALL request
the project's audience binding alongside the scope:

```python
client = ZitadelTokenClient(
    ...,
    scope=f"openid {SCOPE_RETRIEVAL_QUERY} "
          f"urn:zitadel:iam:org:project:id:362771533686374406:aud",
)
```

This forces Zitadel to include the project's app clientIds in the token's
`aud` claim, so receiver audience validation succeeds.

### REQ-6 (Receiver `ZITADEL_API_AUDIENCE` = its own clientId)

Each receiver's `ZITADEL_API_AUDIENCE` env var SHALL be set to its
Application's clientId from REQ-1. Validated at startup
(retrieval-api already enforces non-empty via `zitadel_jwt_enabled`).

### REQ-7 (End-to-end soak verification)

After REQ-1..6 are deployed:

- Trigger one Voys chat. Verify retrieval-api logs `auth_path=jwt,
  sub=<svc-litellm-userId>, scopes=[klai:internal:retrieval:query]`.
- Verify ZERO `KlaiKnowledgeHook: jwt rejected by receiver` warnings for
  7 consecutive days.
- After 7-day green soak, file Phase D cleanup PR: remove the
  receive-side fallback from `klai_knowledge.py::_retrieve_with_dual_auth`
  and the legacy `X-Internal-Secret` middleware path from retrieval-api.

## Out of scope

- Migration of the remaining caller-receiver pairs (C-2 through C-7) —
  those each get their own Phase C-n SPEC. This SPEC unblocks ONLY the
  LiteLLM → retrieval-api pair.
- Replacing X-Internal-Secret with JWT for non-Phase-C internal endpoints.

## Risks

| Risk | Mitigation |
|---|---|
| Zitadel Action hooks (REQ-4a) require Zitadel Cloud Custom Actions feature | Verify availability on `https://auth.getklai.com` before deciding 4a vs 4b. Self-hosted Zitadel ≥ v2.40 supports Actions. |
| Bumping `ZITADEL_API_AUDIENCE` on receivers triggers startup validation; bad value breaks chat | Per `validator-env-parity` pitfall: deploy env var BEFORE code change, with the same SOPS PR including the SOPS-encrypted value. Verify locally with `docker compose config retrieval-api` before push. |
| Audience claim format differs between v1 and v2 Zitadel APIs | Use the same v1 management API used by `zitadel-create-service-account.py` (klai#263) for project + role + grant operations. v2 user search ≠ v2 machine support — already known. |

## Lessons (carryover from SPEC-SEC-SERVICE-AUTH-001)

1. "Operator-run script" is not a runbook. The Phase A runbook should have
   listed every IdP-side config touch needed for the receiver to accept
   the token — not just user creation + secret encryption.
2. Receive-side rejection is a different failure mode from mint failure.
   REQ-5 of the predecessor SPEC implicitly assumed they were equivalent.
   klai#263 fixed that for the LiteLLM hook; future Phase C-n callers
   inherit `_retrieve_with_dual_auth` as the canonical pattern.

## References

- SPEC-SEC-SERVICE-AUTH-001 (predecessor)
- klai#263 (receive-side fallback that unblocked production while this
  SPEC is unwritten)
- klai-infra@663c33b (re-add of `KLAI_LITELLM_*` env vars)
