# SPEC-PORTAL-AUTH-EMAIL-LINKS-001 — Research Notes

## Problem framing

Discovered 2026-05-13: a user activating their Klai account via invite lands on Zitadel's stock hosted UI (`https://auth.getklai.com/ui/login/user/init?...`) instead of Klai's branded `/password/set` page. Klai-mailer wraps the email body in the Klai HTML template, but the call-to-action link points at Zitadel's UI rather than Klai's.

Same issue applies to the password-reset email flow (`POST /api/auth/password/reset` → Zitadel mails a link to the user → link points at `auth.getklai.com`).

## Existing Klai pattern (mapped 2026-05-13)

Klai already has a fully-branded UI for every authenticated user interaction, with portal-api as the exclusive Zitadel-v2-API client. The "Klai UI on the front, Zitadel v2 API on the back" pattern is the canonical Klai answer to "how do we avoid Zitadel UI".

Routing mechanisms for keeping Zitadel pointed at Klai UI (all Zitadel-native):

1. **OIDC pre-auth login** — Zitadel Login V2 with `base_uri = https://my.getklai.com`. SPEC-AUTH-008 documents this. Login V2 sits BEFORE the OIDC app so every `oauth/v2/authorize` request is forwarded to Klai's `/login`.
2. **IDP intent flows** — `urls.successUrl` parameter on `POST /v2/idp_intents`. Klai uses this in `klai-portal/backend/app/services/zitadel.py:574` for Google SSO. The successUrl points at `{settings.portal_url}/api/auth/idp-callback` — Zitadel calls back into portal-api directly.
3. **Email-link flows** — `url_template` parameter on `SendInviteCode`, `SendPasswordResetLink`, `SendEmailVerificationCode`. **Not currently used by Klai. This is the gap.**

## Zitadel v2 API verification (proto-level)

Source: `https://raw.githubusercontent.com/zitadel/zitadel/main/proto/zitadel/user/v2/{user_service,user,email,password}.proto` (downloaded 2026-05-13).

### SendPasswordResetLink (password.proto:38-53)

```proto
message SendPasswordResetLink {
  NotificationType notification_type = 1;
  // Optionally set a url_template, which will be used in the password reset mail
  // sent by Zitadel to guide the user to your password change page.
  // If no template is set, the default Zitadel url will be used.
  //
  // The following placeholders can be used: UserID, OrgID, Code
  optional string url_template = 2 [
    (validate.rules).string = {min_len: 1, max_len: 200},
    example: "https://example.com/password/changey?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"
  ];
}

enum NotificationType {
  NOTIFICATION_TYPE_Unspecified = 0;
  NOTIFICATION_TYPE_Email = 1;
  NOTIFICATION_TYPE_SMS = 2;
}
```

JSON shape: `{"sendLink": {"notificationType": "NOTIFICATION_TYPE_Email", "urlTemplate": "..."}}`.

### SendInviteCode (user.proto:339-363)

```proto
message SendInviteCode {
  // Optionally set a url_template, which will be used in the invite mail
  // sent by Zitadel to guide the user to your invitation page.
  // If no template is set and no previous code was created, the default Zitadel url will be used.
  //
  // The following placeholders can be used: UserID, OrgID, Code
  optional string url_template = 1 [
    (validate.rules).string = {min_len: 1, max_len: 200},
    example: "https://example.com/user/invite?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"
  ];
  // Optionally set an application name, which will be used in the invite mail.
  // If no application name is set and no previous code was created, Zitadel will be used as default.
  optional string application_name = 2 [
    (validate.rules).string = {min_len: 1, max_len: 200},
    example: "CustomerPortal"
  ];
}

message ReturnInviteCode {}
```

JSON shape: `{"sendCode": {"urlTemplate": "...", "applicationName": "Klai"}}`.

**Critical:** the proto comment "If no template is set **and no previous code was created**, the default Zitadel url will be used" → Zitadel caches the `url_template` per user. If a previous invite-code was generated without `urlTemplate`, that cached default wins until a new call passes an explicit template. REQ-10 in spec.md forces explicit `urlTemplate` on every call to defeat this cache.

### SendEmailVerificationCode (email.proto:41-55)

```proto
message SendEmailVerificationCode {
  // Optionally set a url_template, which will be used in the verification mail
  // sent by Zitadel to guide the user to your verification page.
  // If no template is set, the default Zitadel url will be used.
  //
  // The following placeholders can be used: UserID, OrgID, Code
  optional string url_template = 1 [
    (validate.rules).string = {min_len: 1, max_len: 200},
    example: "https://example.com/email/verify?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"
  ];
}
```

JSON shape: `{"sendCode": {"urlTemplate": "..."}}`. No `application_name` field (email-verification mail does not use the app-name).

### AddHumanUserRequest (user_service.proto:1992+ + user.proto:22-70)

`POST /v2/users/human` accepts:
- `username` (optional)
- `profile.given_name` (required, ≤200)
- `profile.family_name` (required, ≤200)
- `profile.nick_name` (optional)
- `profile.display_name` (optional)
- `profile.preferred_language` (optional, ≤10)
- `profile.gender` (optional, `Gender` enum)
- `email.email` (required, RFC-822) + email-verification oneof: `is_verified: true` | `send_code: SendEmailVerificationCode` | `return_code: ReturnEmailVerificationCode`
- `phone` (optional, similar shape)
- `metadata` (optional)
- `idpLinks` (optional)
- `totpSecret` (optional)

**Cannot trigger invite-mail directly.** The `email.send_code` oneof triggers an **email-verification** mail (not an invite mail). To send an invite, a separate `POST /v2/users/{user_id}/invite_code` is required. This is why REQ-2 mandates two sequential calls.

## Decision matrix (3 alternatives considered)

| Option | Description                                                                                     | Pros                                                          | Cons                                                                                                                                                                                                  | Decision |
|--------|-------------------------------------------------------------------------------------------------|----------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------|
| A      | `url_template` on each Zitadel v2 call                                                          | Single-layer change. Zitadel-native. Mirrors `successUrl` pattern already used for IDP intents. Smallest blast radius. | Three call-sites need parameter. Requires lint rule to enforce.                                                                                                                                       | **PICKED** |
| B      | klai-mailer parses incoming `templateData.url`, swaps host to `my.getklai.com`, preserves query params | No portal-api change. Works even if someone hits Zitadel directly. | Brittle (Zitadel URL format may change between versions). Places routing decisions outside the layer that owns them. Adds complexity to klai-mailer beyond its "wrap content in HTML" responsibility. | Rejected |
| C      | `returnCode: true`, portal-api receives the code in response, renders mail itself via klai-mailer's `/internal/send` | Maximum control. Independent of Zitadel notification provider. | Duplicates klai-mailer's existing Zitadel-notification-provider pipeline. Requires implementing retry, rate-limit, template-variables again. Two pipelines to maintain.                              | Rejected |

Option A picked because it **completes the existing Klai pattern**. `urls.successUrl` (used today for IDP intents) and `url_template` (this SPEC) are the same architectural lever: portal-api injects its own URL into a Zitadel API call. Options B and C would *invent* a new layer where Klai already has the Zitadel-native answer.

## Risks investigated

### Risk 1: Login V2 base_uri interaction

Login V2 instance feature has `base_uri = https://my.getklai.com`. The URL the user clicks lands them on `my.getklai.com/password/set?userID=…&code=…&orgID=…`. The Klai `/password/set` page does NOT initiate a Zitadel session; it only calls `POST /api/auth/password/set` which uses `ZitadelClient.set_password_with_code` (`zitadel.py:324`) — a `POST /v2/users/{userId}/password` with `verificationCode`. No OIDC flow is initiated from `/password/set`. Therefore Login V2 base_uri is irrelevant to this flow. ✅ No conflict.

### Risk 2: Mail-template `{{.URL}}` consumer behaviour

The button URL in the email body is `templateData.url` (per `klai-mailer/app/models.py:31` and `:54-55`). klai-mailer's renderer does:

```python
button_url=_append_lang_to_url(payload.button_url(), lang)  # renderer.py:120
```

`_append_lang_to_url` (renderer.py:28-44) appends `&lang=<lang>` if the URL already has `?`, else `?lang=<lang>`. Since the Zitadel-substituted URL is `https://my.getklai.com/password/set?userID=…&code=…&orgID=…` (contains `?`), klai-mailer appends `&lang=nl` → final URL `https://my.getklai.com/password/set?userID=…&code=…&orgID=…&lang=nl`. The frontend `password/set.tsx` validator (file:13-23) accepts unknown search params, so the extra `lang` is harmless. ✅ No conflict.

### Risk 3: Email-change flow conflict

Verified 2026-05-13 via grep across `klai-portal/backend/app/api/`: no end-user-facing email-change endpoint exists. The only `users/{id}/email/_verify` callsite (`zitadel.py:196`) is for admin/migration scripts. REQ-4 documents the forward-compat pattern in case a change-email flow is added; no current scope.

### Risk 4: Tenant-subdomain users

Per SPEC-AUTH-008 (Login V2 base_uri) and `klai-infra/SERVERS.md`, login always happens on `my.getklai.com`. Tenant subdomains like `voys.getklai.com` are the per-tenant chat UI, not the login UI. URL template hardcoded to `my.getklai.com` (via `settings.frontend_url`) is correct for all tenants. ✅ No per-tenant variation needed.

### Risk 5: Existing in-flight Zitadel-default mails

Per REQ-9 and AC-4: codes are URL-template-agnostic. The Klai `/password/set` route accepts the same `userID + code + orgID` payload as Zitadel's hosted UI; either UI completes the flow with the same Zitadel API call (`set_password_with_code`). No deploy-window blackout, no migration step.

## Klai-mailer template inventory (current)

From `klai-mailer/zitadel-message-texts/nl.yaml`:

| Event-type     | Mail template title             | Triggered by                                                  | Current URL host          | Post-SPEC URL host   |
|----------------|----------------------------------|---------------------------------------------------------------|----------------------------|------------------------|
| `InitCode`     | "Account activeren"              | v1 `users/human/_import` with `sendCodes: True` (legacy)      | `auth.getklai.com`         | retired (REQ-2 split) |
| `InviteUser`   | "Je bent uitgenodigd voor Klai"  | v2 `/v2/users/{id}/invite_code` with `sendCode`               | `auth.getklai.com`         | `my.getklai.com` ✅   |
| `PasswordReset`| "Nieuw wachtwoord instellen"     | v2 `/v2/users/{id}/password_reset` with `sendLink`            | `auth.getklai.com`         | `my.getklai.com` ✅   |
| `VerifyEmail`  | "Bevestig je e-mailadres bij Klai" | Future: `/v2/users/{id}/email/_send_code` with `sendCode`     | (not currently triggered)  | `my.getklai.com` (REQ-4 forward-compat) |
| `PasswordChange` | "Je wachtwoord is gewijzigd"   | Automatic on password-set                                      | No button (informational)  | unchanged             |

Post-SPEC: every email Klai issues that contains a click-link points at `my.getklai.com`. `PasswordChange` has no button (it's a security notification, not an action), so unaffected.

## Open verification tasks (deferred to research phase of the SPEC run)

1. **Live curl test against dev Zitadel.** Build an invite_code call with `sendCode.urlTemplate`, watch the resulting mail in dev SMTP, confirm `templateData.url` contains the Klai host. Falsifies the assumption that our Zitadel version (v4.x per SERVERS.md) honours `url_template`.
2. **`_append_lang_to_url` spot-check.** Read `renderer.py:28-44` and verify the `&lang=` appending composes correctly with the multi-param Klai URL. Likely a no-op test (regex appends `&`), but worth a unit test to lock in.
3. **Frontend search-param tolerance.** Confirm `password/set.tsx`'s `validateSearch` (file:13-23) ignores `lang`, `loginname`, `passwordset`, `authRequestID`, `organization` (the extras Zitadel might inject) without error. Spot-check from a manual reset.

## References

- Zitadel v2 user-service proto: https://github.com/zitadel/zitadel/blob/main/proto/zitadel/user/v2/user_service.proto
- Zitadel v2 user proto (SendInviteCode): https://github.com/zitadel/zitadel/blob/main/proto/zitadel/user/v2/user.proto#L339
- Zitadel v2 password proto (SendPasswordResetLink): https://github.com/zitadel/zitadel/blob/main/proto/zitadel/user/v2/password.proto#L38
- Zitadel v2 email proto (SendEmailVerificationCode): https://github.com/zitadel/zitadel/blob/main/proto/zitadel/user/v2/email.proto#L41
- Zitadel docs — Password Reset API: https://zitadel.com/docs/apis/resources/user_service_v2/user-service-password-reset
- Zitadel docs — Custom Login UI password reset: https://zitadel.com/docs/guides/integrate/login-ui/password-reset
- Klai SPEC-AUTH-008 (Login V2 + BFF): `.moai/specs/SPEC-AUTH-008/spec.md`
- Klai rule — Zitadel platform: `.claude/rules/klai/platform/zitadel.md`
- Klai rule — Mailer patterns: `.claude/rules/klai/projects/mailer.md`
- Klai rule — Portal backend FRONTEND_URL pitfall: `.claude/rules/klai/projects/portal-backend.md`
