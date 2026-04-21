---
id: SPEC-AUTH-007
version: "0.2.0"
status: research
created: "2026-04-19"
updated: "2026-04-19"
author: MoAI
priority: P2
related: [SPEC-AUTH-008]
---

## HISTORY

| Date       | Version | Change                                                     |
|------------|---------|------------------------------------------------------------|
| 2026-04-19 | 0.1.0   | Initial research memo — BFF migration exploration          |
| 2026-04-19 | 0.2.0   | Add pointer to SPEC-AUTH-008 (implementation SPEC)         |

> **This SPEC is a research memo: it documents the *why*. The implementation is
> tracked in [SPEC-AUTH-008](../SPEC-AUTH-008/spec.md) — the *how*. Treat this
> file as context for that SPEC, not as an action list.**

# SPEC-AUTH-007: Research — Backend-for-Frontend (BFF) Auth Migration

> **Status: research memo, not an implementation spec.** Captures the architectural
> pressure on the current "per-origin localStorage token" model so we can revisit it
> with full context when the product has real paying users.

---

## Problem

The portal currently uses the "public client + tokens in localStorage" OAuth SPA pattern:

- One Zitadel OIDC app with a growing list of registered `redirectUris` (one per tenant subdomain, added at provisioning time via [`add_portal_redirect_uri`](klai-portal/backend/app/services/zitadel.py#L497)).
- Each subdomain completes its own OIDC code exchange and stores `access_token` + `refresh_token` in its own `localStorage` (keyed on origin).
- `automaticSilentRenew: true` via [`oidc-client-ts`](https://authts.github.io/oidc-client-ts/) — uses the refresh token when available, falls back to an iframe with `prompt=none` when not.
- Tenant hand-off is `window.location.replace(workspace_url)` → tenant origin has empty storage → `/` route fires `signinRedirect` → silently completes because of the Zitadel SSO cookie on `auth.getklai.com`.

The current fix (SPEC-AUTH-006 follow-up commits `0caed060`, `b41fe888`, upcoming typed-error refactor) closes the silent-renew boot-loop symptom and hardens the post-login flow. Those changes are correct for today, but they do not address the underlying architectural fragility documented below.

---

## Risks of the current architecture

| Risk                                    | Cause                                                                                                          |
|-----------------------------------------|----------------------------------------------------------------------------------------------------------------|
| Silent renew fails when OP session cookie is gone | Iframe fallback needs an active Zitadel session on `auth.getklai.com`. Short lifetimes, Safari ITP, browser "clear cookies" all break it. |
| XSS reads tokens from `localStorage`    | Any script injection on a tenant subdomain exfiltrates both access and refresh tokens.                         |
| Refresh token rotation is invisible     | Without httpOnly cookies we can't enforce rotation server-side; revocation relies on OP-only TTLs.             |
| Redirect-URI list grows unbounded       | One entry per tenant. Zitadel does not support wildcards safely.                                               |
| Third-party cookie restrictions         | Chrome 2024+ third-party cookie phase-out breaks silent iframe renew in all major browsers over 2026-2027.     |
| Per-origin token storage fragments state | A logged-in user on `my.getklai.com` is a different authenticated session from the same user on `getklai.getklai.com`. Hand-off re-does the full OIDC dance. |

---

## Industry standard (2026)

OAuth 2.1 browser-based apps BCP and OWASP ASVS 5.0 both recommend the **Backend-for-Frontend (BFF) pattern** for browser SPAs:

- Tokens live server-side (portal-api) — never in browser JS.
- Browser holds one `__Host-session` cookie, `HttpOnly; Secure; SameSite=Lax` on `.getklai.com` — shared across all tenant subdomains.
- portal-api proxies authorised requests to downstream services using the server-side access token; frontend uses same-origin fetches that automatically carry the session cookie.
- Silent renew becomes a server-side refresh-token exchange, no iframe.

Trade-offs:

- (+) XSS cannot reach tokens — only session cookies, bounded by `HttpOnly`.
- (+) One cookie on `.getklai.com` replaces the whole hand-off flow. No per-origin localStorage, no double OIDC dance.
- (+) Redirect URI list shrinks to one entry (`my.getklai.com/callback`).
- (+) Aligns with Chrome third-party-cookie deprecation and Safari ITP.
- (−) portal-api becomes stateful (session store — Redis).
- (−) Every API call hits portal-api even for direct-to-downstream flows. Latency budget needs review.
- (−) Migration touches every frontend `apiFetch` call + all downstream services' auth.

---

## Scope sketch (for implementation, NOT committed)

| Layer             | Change                                                                                                         |
|-------------------|----------------------------------------------------------------------------------------------------------------|
| Backend (portal-api) | Session store (Redis); `/api/auth/callback` exchanges code for tokens, stores under session ID, sets cookie; `/api/auth/logout` revokes + clears cookie. |
| Backend            | Middleware resolves session cookie → access_token → bearer to downstream (replaces direct client-side bearer). |
| Frontend           | Drop `react-oidc-context`. `/callback` just fetches `/api/me` (cookie already set). Remove localStorage token storage. |
| Zitadel            | Portal OIDC app becomes confidential (client_secret). Redirect URI list collapses to `my.getklai.com/callback`. |
| Caddy              | Add cookie-friendly CORS + `Cookie` forwarding on tenant subdomains.                                           |
| Migration          | Hard cutover feasible while system has no paying users. Otherwise dual-run with feature flag per tenant.        |

---

## Decision deferred

Reasons to defer:

1. **Not used by third parties yet** — blast radius of today's silent-renew failures is zero.
2. **Recent fix restores correctness** — `isReauthenticationRequired`, typed fetch errors, exponential backoff in provisioning, Sentry fingerprinting. Current pattern works for 99% of cases for the next 12+ months.
3. **Chrome third-party cookie deadline has been extended multiple times** — 2027 at earliest. We have runway.
4. **Real cost is the migration, not the pattern** — every downstream service's auth layer changes.

Reasons to revisit:

- First paying tenant onboarded.
- First silent-renew-related user-visible incident in Sentry that isn't covered by the current retry/fingerprint.
- Chrome third-party cookie deprecation confirmed for calendar quarter.
- Any security review that flags `localStorage` tokens as audit-fail.

---

## Observability prerequisites

Before deciding, collect data via the instrumentation added in commits following this memo:

- Sentry tag `domain=auth, phase=silent-renew` — count of silent-renew failures per week.
- Sentry tag `domain=auth, phase=post-login, error_kind=network` — incidence of the `/api/me` `TypeError: Failed to fetch` on tenant subdomains (adblocker vs. real infra).
- Sentry tag `domain=auth, phase=provisioning-poll` — provisioning polling health.

One month of data should tell us whether silent renew is a theoretical concern or an actual source of user pain.

---

## References

- [OAuth 2.0 for Browser-Based Applications (BCP 2026)](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-browser-based-apps)
- [OWASP ASVS 5.0 — Session Management](https://owasp.org/www-project-application-security-verification-standard/)
- [Philippe De Ryck — SPA Authentication patterns 2026](https://pragmaticwebsecurity.com)
- [authts/oidc-client-ts — refresh-token behaviour](https://github.com/authts/oidc-client-ts/blob/main/src/UserManager.ts)
- [Zitadel OAuth App types & silent SSO](https://zitadel.com/docs/apis/openidoauth/scopes)

---

## Out of scope for this memo

- No code changes implied by this document.
- No timeline committed.
- No migration plan — that is a separate SPEC-AUTH-008 if/when we decide to proceed.
