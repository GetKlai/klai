---
paths:
  - "klai-portal/backend/app/services/zitadel*"
  - "klai-portal/backend/app/api/auth*"
  - "deploy/docker-compose*.yml"
---
# Zitadel

## PAT rotation (CRIT)
- PAT can become invalid after Zitadel upgrades. Symptom: `Errors.Token.Invalid (AUTH-7fs1e)`.
- Rotate: Zitadel console → Service Accounts → Portal API → + New PAT.
- Update in SOPS + restart portal-api.
- Full procedure: `runbooks/platform-recovery.md#zitadel-pat-rotation`.

## Login V2 deadlock (CRIT)
- Login V2 routes ALL OIDC flows (including admin console) through portal login.
- If portal login is broken: delete Login V2 row from PostgreSQL.
- Full procedure: `runbooks/platform-recovery.md#zitadel-login-v2-recovery`.

## Login V2 base_uri must be my.getklai.com, NOT tenant subdomain (CRIT)

The instance-level Login V2 feature flag (`projections.instance_features5 WHERE key='login_v2'`) has a `value.base_uri.Host` field. This controls WHERE Zitadel sends users BEFORE authentication on every OIDC flow — regardless of which OIDC app initiated the flow.

**Must be:** `my.getklai.com` (the portal login per SPEC-AUTH-008 / SERVERS.md).
**Never:** `getklai.getklai.com` (a tenant subdomain) or any `{tenant}.getklai.com`.

**Why:** Even when the portal OIDC app has perfect `redirect_uris` and `post_logout_redirect_uris` set to `my.getklai.com`, Login V2 sits BEFORE the OIDC app in the flow. A wrong `base_uri.Host` short-circuits every login to the tenant subdomain and confuses users ("why am I on getklai.getklai.com before I even logged in?").

**Verification:**
```bash
curl -s -o /dev/null -w "%{redirect_url}\n" \
  "https://auth.getklai.com/oauth/v2/authorize?response_type=code&client_id=369262708920483857&redirect_uri=https%3A%2F%2Fmy.getklai.com%2Fapi%2Fauth%2Foidc%2Fcallback&scope=openid&state=x&code_challenge=x&code_challenge_method=S256"
# Expected: https://my.getklai.com/login?authRequest=V2_...
# NOT:      https://getklai.getklai.com/login?authRequest=V2_...
```

**Fix (only as klai DB user, direct SQL — no Zitadel restart needed):**
```sql
UPDATE projections.instance_features5
SET value = '{"base_uri": {"Host": "my.getklai.com", "Path": "", "User": null, "Opaque": "", "Scheme": "https", "RawPath": "", "Fragment": "", "OmitHost": false, "RawQuery": "", "ForceQuery": false, "RawFragment": ""}, "required": true}'::jsonb,
    change_date = NOW(),
    sequence = sequence + 1
WHERE instance_id = '362757920133218310' AND key = 'login_v2';
```

**Prevention:** When running the zitadel-login-v2-recovery runbook Step 3, verify the Host in the SQL matches `my.getklai.com`. The recovery SQL is the most common place this bug gets reintroduced.

## Org per tenant
One Zitadel Organization per customer. Org ID is the primary tenant identifier — stored in PostgreSQL alongside LibreChat container name and MongoDB database name.

## User grants (not project grants)
- `POST /management/v1/users/{userId}/grants` — assigns role to a user (correct).
- `POST /management/v1/projects/{projectId}/grants` — grants project to an ORG (wrong for individual users).
- Role must be defined on the project first. Token claim: `urn:zitadel:iam:org:project:roles`.

## User lookup
- Never use `urn:zitadel:iam:user:resourceowner:id` — not always present.
- Use `sub` (OIDC subject) → `portal_users` → `portal_orgs` join for reliable org resolution.

## portal_users = mapping only
No email/name columns. Identity always fetched live from Zitadel. No drift, no sync job needed.

## SSO cache
- `_sso_cache` and `_pending_totp` are in-memory dicts — single instance only.
- When scaling: replace with Redis-backed cache.

## Management API: /oidc vs /oidc_config (HIGH)

`PUT /management/v1/projects/{projectId}/apps/{appId}/oidc` returns 404.
The read endpoint is `/oidc`; the write endpoint is `/oidc_config`.

**Why:** Zitadel splits GET (read) and PUT (update) onto different path suffixes. Easy to confuse when scripting redirect URI changes.

**Prevention:** Always use `/oidc_config` for Management API PUT calls that update OIDC app settings. Reference script: `klai-infra/scripts/zitadel-add-signup-redirect.py`.

## Management API: X-Zitadel-Orgid required for org-scoped calls (HIGH)

Management API calls without `X-Zitadel-Orgid` succeed but target the service account's default org (portal org), not the intended org.

**Why:** The Zitadel Management API uses `X-Zitadel-Orgid` header to scope operations to a specific org. Without it, the service account's own org (`362757920133283846`) is used as context.

**Prevention:** Always include `X-Zitadel-Orgid: {target_org_id}` in any Management API call that must operate on a specific org.
