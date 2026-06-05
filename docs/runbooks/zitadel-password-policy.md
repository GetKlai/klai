# Zitadel Password Policy Migration

Klai validates onboarding passwords locally before calling Zitadel. Production
startup also checks Zitadel's password-complexity policy. If Zitadel still
requires legacy composition rules when the portal is deployed, the portal API
will fail startup instead of accepting passwords that Zitadel later rejects.

## Target Policy

- `minLength`: `15`
- `hasUppercase`: `false`
- `hasLowercase`: `false`
- `hasNumber`: `false`
- `hasSymbol`: `false`

## Deploy Order

1. Update Zitadel first.
2. Verify the Zitadel policy.
3. Deploy the portal backend and frontend.
4. Verify the public Klai endpoint returns only the modern policy fields.

Do not deploy the portal code before step 2 succeeds. The startup guard is
fail-loud by design and can crashloop the whole portal API if Zitadel is still
on the legacy policy.

## Commands

Use an IAM/Admin PAT with access to the Zitadel Admin API. Do not use a browser
token.

```bash
cd klai-portal/backend
export ZITADEL_ADMIN_PAT=...
export ZITADEL_BASE_URL=https://auth.getklai.com

uv run python scripts/update_zitadel_password_policy.py
uv run python scripts/update_zitadel_password_policy.py --apply
uv run python scripts/update_zitadel_password_policy.py
```

After the portal deploy, verify the public contract:

```bash
curl -fsS https://my.getklai.com/api/auth/password-policy
```

Expected response:

```json
{"min_length":15,"min_score":3}
```

## Rollback

Rolling the portal image back across this change is not a plain app rollback.
Older portal builds validated shorter passwords locally. If those builds run
while Zitadel still requires `minLength: 15`, a user can submit a password that
old Klai accepts, have the one-time invite code consumed, and then be rejected
by Zitadel. That recreates the original stuck-invite failure mode.

If you must roll back to a pre-modern-policy portal image, first set Zitadel to
a policy that is no stricter than the rollback image validates locally. For the
pre-2026-06-05 production portal that means:

- `minLength`: `12` or lower
- `hasUppercase`: `true`
- `hasLowercase`: `true`
- `hasNumber`: `true`
- `hasSymbol`: `true`

Then verify the old portal's public contract after rollback:

```bash
curl -fsS https://my.getklai.com/api/auth/password-policy
```

Never run:

- a new portal image against old stricter composition policy; the startup guard
  rejects this and the portal API can crashloop by design.
- an old portal image against the new `minLength: 15` Zitadel policy; that can
  consume invite links before Zitadel rejects the password.

Before any rollback, verify the current policy compatibility so the operator
knows which side of the deploy/rollback boundary they are on:

```bash
cd klai-portal/backend
export ZITADEL_ADMIN_PAT=...
export ZITADEL_BASE_URL=https://auth.getklai.com
uv run python scripts/update_zitadel_password_policy.py
```
