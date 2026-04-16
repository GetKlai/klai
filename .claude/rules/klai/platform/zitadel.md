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
