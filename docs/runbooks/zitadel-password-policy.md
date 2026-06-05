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

If the portal deploy must be rolled back, roll back the portal image first. If
Zitadel also needs to be restored to the previous production policy, set:

- `minLength`: `8`
- `hasUppercase`: `true`
- `hasLowercase`: `true`
- `hasNumber`: `true`
- `hasSymbol`: `true`

Do not leave a new portal image running against the old Zitadel policy: the
startup guard will reject that configuration.
