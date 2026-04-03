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
