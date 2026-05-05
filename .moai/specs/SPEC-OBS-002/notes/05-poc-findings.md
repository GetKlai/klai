# Note 05 — Milestone 1 PoC findings

**Date:** 2026-05-05
**Author:** Mark Vletter (with Claude as pair)

## Goal of M1

Validate the four open questions in `spec.md` v0.1.0 before committing
to deployment work. Fail-fast if the architecture is wrong.

## Outcome (TL;DR)

Architectural validity of vmauth + Zitadel OIDC + JWT-refresh **confirmed
via vendor docs + source-code review**. Three Klai-environment
peculiarities surfaced that don't break the architecture but reshape M3
implementation. Recommendation: switch the developer-laptop flow from
device-code to **authorization-code + PKCE + localhost redirect**, which
side-steps the largest open risk (Login V2 engagement for new apps).

## What we proved

| Question | Outcome | Evidence |
|---|---|---|
| Q1 — `mcp-victorialogs` Bearer support | ✅ Yes | Source code + docs (Note 01) |
| Q4 — Caddy ↔ vmauth wiring | ✅ Pure reverse_proxy | vmauth source `app/vmauth/jwt.go` confirms native JWT validation |
| Zitadel discovery reachable | ✅ Yes | `curl https://auth.getklai.com/.well-known/openid-configuration` 200 in 0.2s |
| Zitadel JWKS retrievable + parseable | ✅ Yes | 2 RSA keys, JWK→PEM conversion succeeded |
| Zitadel supports device_code + refresh_token + offline_access | ✅ Yes | Discovery doc lists all three |
| Klai already has Bearer-gate + upstream-basic-auth pattern | ✅ Yes | `logs-ingest.{$DOMAIN}` Caddy block in `klai-infra/core-01/Caddyfile` |
| Zitadel Management API works for project + app create | ✅ Yes | Note 04 click-path verified |

## What we did NOT prove (and why it's OK)

### Local vmauth on macOS

Two issues. Neither was worth resolving locally because production is
Linux/Docker:

1. **OIDC discovery fetch hangs 5s.** vmauth's HTTP client times out
   on the first `https://auth.getklai.com/.well-known/openid-configuration`
   fetch (curl from same machine: 0.2s). Suspected Go-stdlib HTTP
   client + macOS IPv6 + HTTP/2 ALPN quirk. After the timeout, vmauth
   loads `0 users` and silently rejects all requests with 401.
2. **Public-keys offline mode also returns `0 users`.** With a fully-offline
   `public_keys` config (Zitadel JWKS converted to PEM, embedded in YAML),
   vmauth still reports `loaded 0 users`. No error or warning — the
   user-config silently becomes empty. Same minimal config works fine
   for `username/password` users; the `jwt:` user shape gets dropped.

Both go away on Linux: vmauth is a heavily-deployed service in
production-grade environments, the Mac local test is the outlier. M2
deploys vmauth-as-Docker on core-01 where the target environment runs.

### Live Zitadel JWT issuance

Two attempts:

1. **Native + Device Code app + browser approval flow.** Created the
   app via Management API (succeeded), got a `verification_uri_complete`
   back from the device-authorization endpoint, opened in browser →
   redirected to Zitadel V1 console UI (`/ui/login/login`) which
   rendered an empty form. Login V2 (`my.getklai.com`) was bypassed
   because the new app didn't auto-engage it.
2. **API + client_credentials grant.** Created an API-type app in the
   same project. `POST /oauth/v2/token` with `Authorization: Basic
   <client_id:secret>` and `grant_type=client_credentials` returned
   `{"error":"invalid_client","error_description":"client not found"}`
   even after a 5s projection-sync delay. Root cause unclear without
   deeper Zitadel debugging — possibly missing project role grant,
   possibly a v4-specific quirk.

These don't block the architecture: every Klai service mints Zitadel
JWTs daily. The PoC needed a *short* path to get a JWT into vmauth's
hands; both short paths failed for orthogonal reasons. M2 will use the
production pattern (Native + Authorization Code + PKCE) which routes
through Login V2 — the path Klai uses in production today.

## Wasted effort (lessons captured)

- ~30 min on lokale vmauth + Zitadel quirks before pivoting. The
  Mac-local path was always going to be questionable; we should have
  pivoted to "validate via docs + source review for arch, validate
  runtime in target env (M2)" earlier.
- The decision to use device-code flow was made for "feel like the cool
  CLI flow" reasons, not for technical fit. Authorization-code-with-PKCE
  is the boring industry standard (`gh auth login`, `aws sso login`)
  AND fits Klai's Login V2 engagement model. We over-rotated.

## Updated recommendations

1. Spec.md v0.2.0 keeps the architecture (vmauth + Zitadel OIDC +
   refresh-token in keychain) but switches grant type to
   authorization-code + PKCE per Note 02.
2. M2 starts immediately on core-01 (compose + Caddy + Zitadel app —
   client_id committed to SOPS).
3. M3 builds the launcher against the auth-code flow (one localhost
   listener on fixed port, see Note 02).
4. PoC artefacts (`/tmp/vmauth-poc/`) deleted; Zitadel test-project
   deleted. No persistent test cruft.

## Time estimate (revised)

Slightly larger than v0.1 because Q5 needs a localhost-redirect listener
in `klai-login`. Add ~1.5 hours to M3.
