---
id: SPEC-PORTAL-AUTH-EMAIL-LINKS-001
version: "0.1.0"
status: draft
created: "2026-05-13"
updated: "2026-05-13"
author: MoAI
priority: P1
supersedes: null
related: [SPEC-AUTH-008, SPEC-SEC-MAILER-INJECTION-001]
---

## HISTORY

| Date       | Version | Change                                                                         |
|------------|---------|--------------------------------------------------------------------------------|
| 2026-05-13 | 0.1.0   | Initial draft — Klai-branded UI for invite, password reset, email verification |

# SPEC-PORTAL-AUTH-EMAIL-LINKS-001: Klai-branded URLs in Zitadel notification emails

## Context

Klai's auth architecture is **"Klai UI on the front, Zitadel v2 API on the back"**: for every user-facing interaction the portal has its own frontend route in `klai-portal/frontend/src/routes/` and its own portal-api endpoint in `klai-portal/backend/app/api/auth.py` that delegates to the Zitadel v2 API via the `ZitadelClient`. The user never sees Zitadel's hosted UI. The complete inventory of flows that already follow this pattern:

| Klai UI                  | portal-api endpoint                                  | Zitadel v2 call                                                        |
|--------------------------|------------------------------------------------------|------------------------------------------------------------------------|
| `/login`                 | `/api/auth/login`                                    | `POST /v2/sessions`                                                    |
| `/login` (TOTP)          | `/api/auth/totp-login`                               | `POST /v2/sessions/{id}` with TOTP                                     |
| `/login` (Google SSO)    | `/api/auth/idp-intent` + `/idp-callback`             | `POST /v2/idp_intents` with `urls.successUrl` pointing at portal-api  |
| `/login` (SSO complete)  | `/api/auth/sso-complete`                             | sessions API                                                           |
| `/signup`                | `/api/auth/signup` + `/api/auth/idp-intent-signup`   | `POST /v2/users/human`, `POST /v2/idp_intents`                         |
| `/verify`                | `/api/auth/verify-email`                             | `POST /v2/users/{id}/otp_email/_verify`                                |
| `/setup/mfa`, `/setup/2fa` | `/api/auth/{totp,passkey,email-otp}/{setup,confirm}` | `POST /v2/users/{id}/{totp,passkeys,otp_email}`                        |
| `/password/forgot`       | `/api/auth/password/reset`                           | `POST /v2/users/{id}/password_reset`                                   |
| `/password/set`          | `/api/auth/password/set`                             | `POST /v2/users/{id}/password`                                         |

Two routing mechanisms keep Zitadel pointed at Klai's UI:

1. **Pre-auth login flows** — Zitadel Login V2 with instance `base_uri = https://my.getklai.com` (`.claude/rules/klai/platform/zitadel.md` § Login V2 base_uri). Every `oauth/v2/authorize` flow routes to `my.getklai.com/login?authRequest=…`.
2. **IDP intent flows (Google SSO)** — the `create_idp_intent` call passes `urls.successUrl = "{settings.portal_url}/api/auth/idp-callback?…"` (`zitadel.py:574`, callsite `auth.py:1797`). Zitadel calls back into portal-api directly.

There is one remaining surface where Zitadel still owns the URL: **email-link flows**. When a user is invited, requests a password reset, or (future) confirms a primary-email change, Zitadel pre-renders the message text, fills `templateData.url` with a link to **its own hosted UI** (`https://auth.getklai.com/ui/login/user/init?…`), and webhooks the payload to klai-mailer. klai-mailer wraps the content in the Klai HTML template and sends via SMTP. The mail is Klai-branded; the *destination* of the call-to-action button is not.

Discovered 2026-05-13 by user activation through invite: the "Activeer User" landing page is the stock Zitadel UI, not `my.getklai.com/password/set` — even though `klai-portal/frontend/src/routes/password/set.tsx` and `POST /api/auth/password/set` (`auth.py:1051`) plus `ZitadelClient.set_password_with_code` (`zitadel.py:324`) already implement the full Klai-side flow.

The Zitadel v2 API has native support for redirecting these links to a custom URL via the `url_template` field on `SendInviteCode`, `SendPasswordResetLink`, and `SendEmailVerificationCode`. Klai already uses the parallel `urls.successUrl` parameter on `create_idp_intent`. This SPEC completes the pattern for the three remaining email-link flows.

This is a **consistency fix**, not an architecture change. It does not introduce a new abstraction layer, does not change klai-mailer, and does not migrate any other auth flow.

---

## Scope

| Layer                | Changes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
|----------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Backend (portal-api) | `ZitadelClient.send_password_reset(user_id)` → add required `url_template: str` parameter. `ZitadelClient.resend_init_mail(org_id, user_id)` → add required `url_template: str` and optional `application_name: str = "Klai"`. New helper `ZitadelClient.send_invite_code(user_id, url_template, application_name="Klai")` invoking `POST /v2/users/{user_id}/invite_code` with `sendCode.urlTemplate`. `ZitadelClient.invite_user` callers split into two calls: `human/_import` with `sendCodes=False`, then `send_invite_code`. |
| Callers              | `auth.py::password_reset` (`auth.py:1037`) passes the URL template. `admin/users.py::invite_user` (`admin/users.py:228`) and `admin/users.py::resend_invite` (`admin/users.py:506`) pass the template. Any other callsite is updated in the same PR. URL template values are derived from `settings.frontend_url`.                                                                                                                                                                                                                     |
| CI / Lint            | New ast-grep rule `rules/no-zitadel-mail-without-url-template.yml` rejecting any `POST /v2/users/.../{password_reset,invite_code,invite_code/resend,email/_verify}` call whose JSON body does not contain `urlTemplate`. Wired into `.github/workflows/portal-api.yml`.                                                                                                                                                                                                                                                                  |
| Tests                | Unit tests asserting payload shape for each of the three call sites. Existing `tests/test_user_lifecycle.py` updated. Integration smoke test in `klai-portal/backend/tests/test_zitadel_email_link_urls.py` that exercises the wire-level payload against a recorded fixture. E2E covered by `klai-portal/frontend/e2e/` invite + reset flow when the seeded storage-state fixture is active.                                                                                                                                            |
| Configuration        | No new env-var. `settings.frontend_url` is the existing source of truth per `.claude/rules/klai/projects/portal-backend.md` § "FRONTEND_URL controls OAuth redirect URIs (CRIT)". Tenant-subdomain users still land on `my.getklai.com` per SPEC-AUTH-008 (Login V2 base_uri is the login domain, not the tenant-portal domain).                                                                                                                                                                                                            |
| Klai-mailer          | No change. Continues to receive Zitadel webhooks with pre-rendered `templateData.url` and forwards them to the existing Klai HTML wrapper. The only difference: the URL inside the payload now points at `my.getklai.com` instead of `auth.getklai.com`.                                                                                                                                                                                                                                                                                |
| Zitadel              | No instance-config change. Login V2 `base_uri` stays `my.getklai.com`. Message-text templates in Zitadel console stay unchanged. Notification provider URL stays `http://klai-mailer:8000/notify`.                                                                                                                                                                                                                                                                                                                                       |

## Out of Scope

- **Email-change flow** — no end-user-facing primary-email change endpoint exists in portal-api today (verified 2026-05-13 via grep across `klai-portal/backend/app/api/`). If such a flow is added in the future, REQ-4 contains the pattern to follow.
- **Email-OTP signup flow** (`/api/auth/email-otp/resend`) — uses a 6-digit code the user types into `/verify`, not a click-link. No URL to override.
- **InitCode legacy flow** — Zitadel's `InitCode` mail (event-type `InitCode` in klai-mailer/zitadel-message-texts/) is triggered by the v1 Management API's `users/human/_import` with `sendCodes: True`. This SPEC migrates to the v2 invite flow (REQ-2) where InitCode is replaced by `InviteUser`, retiring the InitCode mail. The InitCode message-text in `nl.yaml` / `en.yaml` is kept for backward-compat; once the migration is verified in production, removal is a follow-up.
- **klai-mailer rewrite** — the alternative of having klai-mailer parse and rewrite `templateData.url` was considered and rejected as it places routing decisions outside the layer that owns them (portal-api).
- **`returnCode` + Klai-rendered mail** — the alternative of receiving the code in the response and mailing it ourselves via klai-mailer's `/internal/send` was considered and rejected as it duplicates the Zitadel-notification-provider pipeline that already works for all other notification types.
- **Removal of `auth.getklai.com/ui/login/user/init` access** — Zitadel's hosted UI remains technically reachable but no Klai-issued email points to it anymore. Disabling the route on Zitadel's side is a follow-up SPEC.

---

## Requirements

### REQ-1: Password reset link template

**WHEN** `auth.py::password_reset` (`POST /api/auth/password/reset`) successfully resolves a Zitadel user-id from the supplied email,
**THEN** the portal SHALL invoke `ZitadelClient.send_password_reset(user_id, url_template)` with `url_template = f"{settings.frontend_url}/password/set?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"`.

**AND** the wire-level JSON body to `POST /v2/users/{user_id}/password_reset` SHALL contain exactly:

```json
{
  "sendLink": {
    "notificationType": "NOTIFICATION_TYPE_Email",
    "urlTemplate": "https://my.getklai.com/password/set?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"
  }
}
```

**AND** `ZitadelClient.send_password_reset` SHALL have signature `(self, user_id: str, *, url_template: str) -> None` — `url_template` is keyword-only and required. The previous parameter-less form is removed in the same commit; no callers may rely on Zitadel's default URL.

### REQ-2: Invite link template (new-user invite)

**WHEN** an admin invites a new user via `admin/users.py::invite_user`,
**THEN** the portal SHALL perform two sequential calls:

1. `POST /management/v1/users/human/_import` with `sendCodes: False` (no mail).
2. `POST /v2/users/{user_id}/invite_code` with body:

```json
{
  "sendCode": {
    "urlTemplate": "https://my.getklai.com/password/set?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}",
    "applicationName": "Klai"
  }
}
```

**AND** `ZitadelClient.invite_user` SHALL no longer pass `sendCodes: True`. A new helper `ZitadelClient.send_invite_code(user_id: str, *, url_template: str, application_name: str = "Klai") -> None` SHALL encapsulate call (2).

**AND** the two calls SHALL be wrapped so that if call (2) fails after call (1) succeeds, the partial user is logged as `event="invite_partial_user_created"` with the userId at ERROR level and the HTTP response to the admin returns 502 with body `{"detail": "invite_partial_failure", "user_id": "..."}`. The orphan user can be remediated via `admin/users.py::resend_invite` (REQ-3) which re-issues only the invite_code call.

### REQ-3: Invite link template (resend)

**WHEN** an admin triggers `admin/users.py::resend_invite` (`POST /api/admin/users/{zitadel_user_id}/resend-invite`),
**THEN** the portal SHALL invoke `ZitadelClient.send_invite_code(user_id, url_template=..., application_name="Klai")` with the same `url_template` as REQ-2.

**AND** the old `ZitadelClient.resend_init_mail` method SHALL be deleted in the same commit. Its single caller is updated to call `send_invite_code` directly.

### REQ-4: Email-verification link template (forward-compat)

**WHEN** any future code path triggers Zitadel's `SendEmailVerificationCode` flow (`POST /v2/users/{user_id}/email`, `POST /v2/users/{user_id}/email/_resend`, or `POST /v2/users/{user_id}/email/_send_code`),
**THEN** that call SHALL pass `sendCode.urlTemplate = f"{settings.frontend_url}/verify?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"`.

**AND** the CI lint in REQ-6 SHALL enforce presence of `urlTemplate` on these endpoints from day one, even though no code path triggers them today, to prevent future drift.

### REQ-5: URL-template construction helper

**WHEN** any caller composes a URL template for one of the three flows,
**THEN** it SHALL use the helper `app.services.auth_links.build_url_template(route: AuthLinkRoute) -> str` defined in a new module `klai-portal/backend/app/services/auth_links.py`.

```python
class AuthLinkRoute(str, Enum):
    PASSWORD_SET = "/password/set"
    VERIFY_EMAIL = "/verify"

def build_url_template(route: AuthLinkRoute) -> str:
    """Compose the Zitadel url_template for an email-link flow.

    Returns a URL with Zitadel placeholders {{.UserID}}, {{.Code}}, {{.OrgID}}
    that Zitadel will substitute server-side before emitting the notification.
    """
    base = settings.frontend_url.rstrip("/")
    return (
        f"{base}{route.value}"
        f"?userID={{{{.UserID}}}}&code={{{{.Code}}}}&orgID={{{{.OrgID}}}}"
    )
```

**AND** the helper SHALL be the only place in `klai-portal/backend/app/` that composes Zitadel placeholder URLs. The CI lint in REQ-6 enforces this.

### REQ-6: CI lint rule

**WHEN** a PR modifies any Python file under `klai-portal/backend/app/`,
**THEN** `tests/test_zitadel_email_link_lint.py` SHALL fail the build if any
`<client>.post(<path>, …)` call has a path matching one of the following AND
the call body does not contain the literal string `urlTemplate`:

- `/v2/users/[^/]+/password_reset`
- `/v2/users/[^/]+/invite_code(/resend)?`
- `/v2/users/[^/]+/email/(_send_code|_resend)`

**AND** the lint SHALL be implemented in pure Python `ast` module against
`klai-portal/backend/app/**/*.py`. Pivoted from an ast-grep YAML rule
during implementation (2026-05-13): ast-grep's `not: has: regex:` semantics
do not reliably express "call without a literal substring in the body" at
sub-expression granularity, while a 50-line `ast.walk` does so trivially.
The pytest implementation:

- Runs in the existing `pytest` step of `.github/workflows/portal-api.yml`
  with zero additional CI wiring (the existing portal-api workflow already
  runs `pytest tests/`).
- Includes self-tests with 5 bad fixtures and 5 good fixtures so the
  lint cannot silently become a no-op if the scanner drifts.
- Walks the AST so it handles both `f"/v2/users/{uid}/..."` and constant
  string variants.

### REQ-7: Boot-time assertion

**WHEN** portal-api starts up,
**THEN** a lifespan-phase assertion SHALL verify that `build_url_template(AuthLinkRoute.PASSWORD_SET)` produces a string that:

1. Starts with `https://my.getklai.com` in production (`settings.frontend_url` matches), or `https://localhost` / `http://localhost` in dev.
2. Contains all three placeholders `{{.UserID}}`, `{{.Code}}`, `{{.OrgID}}` literally (Zitadel does not URL-decode them).
3. Is at most 200 characters (Zitadel `url_template` validation cap per proto).

The assertion fails fast at startup. Pattern: same as `assert_portal_users_rls_ready()` in `klai-portal/backend/app/main.py`.

### REQ-8: Observability

**WHEN** any of the three Zitadel email-link calls succeed or fail,
**THEN** the existing `_emit_auth_event` (`auth.py`) and `_slog.exception` paths SHALL be preserved — no new events. Additionally, on success of `send_invite_code` the existing `event="invite_user"` / `event="resend_invite"` logs in `admin/users.py` SHALL include the field `url_template_host` equal to `settings.frontend_url`'s host, so a VictoriaLogs query can prove the link host across all invites.

### REQ-9: Backward compatibility of `/password/set`

**WHEN** a user clicks a link in an in-flight Zitadel-default mail (sent before this SPEC's deploy) and lands on `auth.getklai.com/ui/login/user/init?…`,
**THEN** the user SHALL still be able to set their password on Zitadel's hosted UI for the duration of the code's 72-hour TTL. The Klai-side `/password/set` route accepts the same `userID + code + orgID` payload and SHALL continue to accept codes that were generated by Zitadel-default URLs (the code itself is generated by Zitadel and is identical regardless of the URL template).

**AND** no rollback mechanism is required — the change is forward-compatible. A re-deploy without the SPEC reverts new mails to Zitadel's default URL; existing codes keep working in either UI.

### REQ-10: Caching gotcha mitigation

**WHEN** `send_invite_code` is called for a user that already had an invite_code generated previously (e.g. an admin clicking "Resend invite"),
**THEN** the portal SHALL ALWAYS pass an explicit `urlTemplate`, never relying on Zitadel's per-user cache.

Per Zitadel proto comment (`user.proto:341`): *"If no template is set and no previous code was created, the default Zitadel url will be used."* This means: if a previous code WAS created (e.g. by a pre-SPEC deploy of `resend_init_mail` that did not pass `urlTemplate`), the cached previous template wins. The portal SHALL pass `urlTemplate` explicitly on every call to defeat the cache and converge stale templates within one resend cycle.

---

## Acceptance Criteria

See `acceptance.md`.

---

## Deployment

1. Land the code change. Image is built by `.github/workflows/portal-api.yml`.
2. Smoke-test on staging: invite a test user, click the link in the mail, assert browser URL matches `https://my.getklai.com/password/set?userID=…&code=…&orgID=…`. Set password. Log in.
3. Promote to production. No DB migration. No SOPS env-var change. No Zitadel instance-config change.
4. Verify in VictoriaLogs:
   - `service:klai-mailer AND eventType:InviteUser` → `templateData.url` field contains `my.getklai.com` (not `auth.getklai.com`).
   - `service:portal-api AND event:invite_user AND url_template_host:my.getklai.com` count matches the number of admin invites.
5. Watch for `event:invite_partial_user_created` (REQ-2). Zero is the expected count; any occurrence is an ops alert.

---

## Risks

| Risk                                                                                                                                | Likelihood | Mitigation                                                                                                                                                                                                                |
|-------------------------------------------------------------------------------------------------------------------------------------|------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `urlTemplate` field name in JSON body differs between Zitadel versions (proto vs HTTP gateway)                                       | Low        | Verified via raw proto inspection (`zitadel/user/v2/{user,password,email}.proto`). HTTP gateway uses camelCase: `urlTemplate`, `applicationName`, `notificationType`. Integration test in REQ-1 unit-tests the exact JSON. |
| Invite partial-success leaves orphan Zitadel users without invite mail (REQ-2 call 1 succeeds, call 2 fails)                         | Low        | REQ-2 documents the failure response and recovery path (`resend_invite` re-issues call 2). Operator playbook in `docs/runbooks/`. Monitoring: VictoriaLogs alert on `event:invite_partial_user_created`.                   |
| Login V2 base_uri vs frontend_url drift across environments (dev/staging/prod)                                                       | Low        | REQ-7 boot assertion catches drift at startup. Existing `FRONTEND_URL` pitfall is documented (`.claude/rules/klai/projects/portal-backend.md`).                                                                            |
| Zitadel caches the previous `url_template` per user (REQ-10)                                                                         | Medium     | REQ-10 forces explicit `urlTemplate` on every call; first new call converges the cache. Smoke-test in deployment step 4 verifies link host.                                                                                |
| Mid-deploy: some users have in-flight Zitadel-default mails, others get Klai-mails                                                   | Low        | REQ-9 documents forward-compat — both UIs accept the same code. Zitadel-default UI keeps working for the 72h TTL of any in-flight code.                                                                                    |
| ast-grep rule (REQ-6) misses a future Zitadel SDK migration that changes call shape                                                  | Low        | Rule is keyed on HTTP path strings, which are stable across SDK versions. Boot assertion (REQ-7) is a second line of defence.                                                                                              |

---

## Open Verification Tasks (research phase)

- [ ] Run an integration test against the actual Klai dev Zitadel instance: invite a synthetic user, assert the email body contains the `my.getklai.com` URL. Required to falsify the assumption that `urlTemplate` is honoured by our specific Zitadel version (currently v4.x — `klai-infra/SERVERS.md`).
- [ ] Verify that `klai-mailer` passes through the substituted URL byte-identically (no rewrites in `renderer.py::_append_lang_to_url`). The `lang` query-param appending in `klai-mailer/app/renderer.py:28` adds `?lang=...` to the URL; verify this composes correctly when our URL already has `?userID=...` (should use `&lang=...`). Spot-check by reading `_append_lang_to_url`.
- [ ] Confirm `klai-portal/frontend/src/routes/password/set.tsx` happily accepts the URL produced when both `?` and `&` separators are in play (Zitadel substitutes the placeholders, then klai-mailer may append `&lang=nl`). The frontend validator on `search` params already accepts unknown keys per file:13-23.
