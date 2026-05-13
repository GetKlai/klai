# SPEC-PORTAL-AUTH-EMAIL-LINKS-001 — Acceptance Criteria

Each criterion is binary: pass or fail. No partial credit.

## AC-1: Password reset link points at Klai UI

**Setup**
1. On staging, request a password reset for a test user: `POST /api/auth/password/reset` with `{"email": "e2e@getklai.com"}`.
2. Wait for the email to arrive in the test inbox (SMTP relay).

**Verify**
- The "Nieuw wachtwoord instellen" button URL in the rendered HTML mail starts with `https://my.getklai.com/password/set?` (NOT `https://auth.getklai.com/ui/login/`).
- The URL contains `userID=`, `code=`, `orgID=` query parameters with non-empty values.
- Clicking the URL lands on Klai's `/password/set` page (not Zitadel's hosted UI).

**Pass condition** All three checks true.

---

## AC-2: New-user invite link points at Klai UI

**Setup**
1. As an admin in staging, invite a new user: `POST /api/admin/users/invite` with a fresh email address.
2. Wait for the email.

**Verify**
- The "Account activeren" button URL starts with `https://my.getklai.com/password/set?` (NOT `https://auth.getklai.com/ui/login/`).
- Clicking it lands on Klai's `/password/set` page.
- The user can set a password and is then redirected to `/` (per existing `password/set.tsx` behaviour, file:69).
- A subsequent login attempt succeeds.

**Pass condition** All four checks true.

---

## AC-3: Resend invite link points at Klai UI

**Setup**
1. Invite a user (AC-2 setup).
2. Before the user clicks, the admin clicks "Resend invite" in the portal.
3. Wait for the second email.

**Verify**
- Both emails contain `my.getklai.com/password/set?` URLs.
- The codes in the two URLs differ (Zitadel rotates the code on resend).
- The latest code works in `/password/set`; the previous code yields a 400 "expired/invalid code" toast.

**Pass condition** All three checks true.

---

## AC-4: Mid-deploy backward compatibility

**Setup**
1. Pre-deploy: generate a password-reset link using the current Zitadel-default URL.
2. Save the link.
3. Deploy this SPEC.
4. Within 72 hours, click the saved link (which points at `auth.getklai.com/ui/login/...`).

**Verify**
- The pre-deploy link still works — Zitadel's hosted UI accepts the password and sets it.
- The user can log in to Klai afterwards.

**Pass condition** Both checks true. (This proves no rollback is needed and codes are URL-template-agnostic.)

---

## AC-5: Partial-failure handling

**Setup** (chaos test, dev only)
1. Patch `ZitadelClient.send_invite_code` to raise `httpx.HTTPStatusError(500)`.
2. Invite a user via the admin UI.

**Verify**
- The HTTP response is 502 with body `{"detail": "invite_partial_failure", "user_id": "..."}`.
- A `event:invite_partial_user_created` log line appears in VictoriaLogs at ERROR level with the userId.
- The Zitadel user exists (queryable via `find_user_id_by_email`) but has no active invite_code.
- Calling `POST /api/admin/users/{id}/resend-invite` on that user successfully issues an invite mail with a `my.getklai.com` URL.

**Pass condition** All four checks true.

---

## AC-6: CI lint rule catches regressions

**Setup**
1. On a test branch, add a new file `klai-portal/backend/app/api/test_offender.py` containing:

```python
await client.post(f"/v2/users/{uid}/password_reset")
```

2. Push and open a PR.

**Verify**
- The `Alerting provisioning checks` job in `.github/workflows/portal-api.yml` fails with an ast-grep error pointing at the offending line.
- The PR cannot be merged with the lint failure.

**Pass condition** Both checks true.

---

## AC-7: Boot assertion catches misconfiguration

**Setup** (dev only)
1. Set `FRONTEND_URL=http://example.com` (an obviously-wrong value).
2. Restart portal-api locally.

**Verify**
- The container's startup fails (does not pass lifespan).
- The error log contains `assert_auth_link_template_ready` or similar, with the misconfigured URL printed.

**Pass condition** Both checks true. (Mirrors `assert_portal_users_rls_ready` behaviour.)

---

## AC-8: Observability fields present

**Setup**
1. Invite a test user (AC-2).
2. Query VictoriaLogs.

**Verify**
- `service:portal-api AND event:invite_user` returns a log with field `url_template_host` equal to `my.getklai.com`.
- `service:klai-mailer AND eventType:InviteUser` returns a log whose `templateData.url` contains `my.getklai.com`.
- A `request_id:<uuid>` query joining both services shows the same trace ID end-to-end.

**Pass condition** All three checks true.

---

## AC-9: No regression in other Zitadel calls

**Setup** Run the existing portal-api test suite.

**Verify**
- `pytest klai-portal/backend/tests/` exits 0.
- Specifically the following pre-existing test files still pass with no modification beyond signature-update parity:
  - `tests/test_user_lifecycle.py`
  - `tests/test_zitadel_session_create.py`
  - `tests/test_auth_mfa_fail_closed.py`

**Pass condition** Suite passes.

---

## AC-10: Application name appears in invite mail

**Setup**
1. Trigger an invite (AC-2 setup).
2. Inspect the rendered email subject AND the `args.ApplicationName` field in klai-mailer's webhook payload (use `klai-mailer/app/main.py` `/debug` endpoint on staging).

**Verify**
- The webhook payload's `args.ApplicationName` (`klai-mailer/app/models.py:39`) equals `"Klai"`, NOT `"ZITADEL"`.
- This guarantees any future use of `{{.ApplicationName}}` in the Zitadel message-text template renders "Klai" instead of "ZITADEL".

**Pass condition** Field equals `"Klai"`.
