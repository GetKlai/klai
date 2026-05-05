# Note 02 — Zitadel app shape + Login V2 finding

**Status:** ⏳ Deferred to M2 — but the recommended path is now clear.

## Question (original)

Should `vmauth-vlogs` be a separate Zitadel API project, or an audience
claim on the existing Klai platform project?

## What M1 actually surfaced (the bigger issue)

When a Native + Device-Code app is created via the Management API, the
device-code authorization page (`auth.getklai.com/device?user_code=XXX`)
redirects to `auth.getklai.com/ui/login/login` — the **legacy V1 login
console** — and renders an empty "Welcome Back!" page with no form
fields. The flow is structurally broken in Klai's setup because Login V2
(`my.getklai.com/login`, the portal-hosted UI) does not auto-engage for
freshly-created apps.

Per `.claude/rules/klai/platform/zitadel.md` (Login V2 section), the
instance-level `loginV2.baseUri` is correctly set to
`https://my.getklai.com`, but device-code flows do not appear to use
it — possibly because device-code auth happens at the Zitadel UI layer
before the OIDC app routing, or because Login V2 needs explicit
opt-in per app.

## Reproduction (for M2 follow-up)

1. PAT-authenticated POST to `/management/v1/projects` creates project.
2. POST to `/management/v1/projects/{id}/apps/oidc` with
   `appType=OIDC_APP_TYPE_NATIVE` + `grantTypes=[..., DEVICE_CODE]` succeeds, returns `clientId`.
3. POST to `/oauth/v2/device_authorization` with that `clientId` returns
   a valid `verification_uri_complete`.
4. Opening `verification_uri_complete` in any browser redirects to
   `auth.getklai.com/ui/login/login` — V1 UI — empty form. No login
   possible. Authorization stays in `authorization_pending` state until
   `device_code` expiry.

## Recommended path forward (rewrites Q5 of `spec.md`)

**Switch from device-code grant to authorization-code-with-PKCE +
localhost redirect.** This is the canonical pattern for desktop CLIs
(used by `gh auth login`, `aws sso login`, `gcloud auth login`,
`heroku login`, etc.):

1. `klai-login` starts a tiny localhost HTTP server on a random port.
2. Opens `https://auth.getklai.com/oauth/v2/authorize?response_type=code
   &client_id=...&redirect_uri=http://localhost:NNNN/callback&scope=
   openid+offline_access&code_challenge=...&code_challenge_method=S256`.
3. Browser opens, Login V2 routes through `my.getklai.com/login`
   (which DOES work in Klai's setup — every developer uses it daily).
4. After login + consent, Zitadel redirects to `localhost:NNNN/callback`
   with `?code=...`.
5. `klai-login` swaps the code for `access_token` + `refresh_token`
   via `POST /oauth/v2/token` with PKCE verifier.
6. Refresh-token written to OS keychain.

App configuration becomes:
- `appType=OIDC_APP_TYPE_NATIVE`
- `grantTypes=[OIDC_GRANT_TYPE_AUTHORIZATION_CODE, OIDC_GRANT_TYPE_REFRESH_TOKEN]`
- `responseTypes=[OIDC_RESPONSE_TYPE_CODE]`
- `authMethodType=OIDC_AUTH_METHOD_TYPE_NONE` (PKCE-only, no secret)
- `redirectUris=["http://localhost:CONSTANT_PORT/callback"]`
  (or wildcard `http://127.0.0.1/*` if Zitadel allows it)

**Trade-off vs device-code:** developer's machine must briefly host a
localhost listener, and the redirect URI in the Zitadel app must match
the listener URL. We pick a fixed port (e.g. `8765`) and document it.

## Audience claim — original question

Still relevant. Decision: **separate Zitadel project for `klai-vmauth`**
(not an audience on the existing platform project) so that:
- The vmauth-aud namespace is isolated from app-token bleed
- Project lifecycle (create / disable / delete) doesn't entangle with
  the main platform project
- Audit trails are cleaner (one project = one purpose)

## Action items for M2

- Verify auth-code + PKCE on a fresh Native app passes through Login V2
  on `my.getklai.com` (smoke-test in browser).
- Confirm Zitadel allows `http://localhost:NNNN/callback` as a
  redirectUri for Native apps (it should — RFC 8252 §7).
- Decide fixed port for `klai-login` listener (proposal: 8765).
- Pin Zitadel project id + client id to a SOPS-managed config so that
  `klai-login` script doesn't have hardcoded values per dev.
