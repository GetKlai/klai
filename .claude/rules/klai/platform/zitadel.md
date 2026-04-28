---
paths:
  - "klai-portal/backend/app/services/zitadel*"
  - "klai-portal/backend/app/api/auth*"
  - "deploy/docker-compose*.yml"
---
# Zitadel

## Service accounts & PATs (CRIT)

Two machine users carry Zitadel authority; keep them separate.

| SA | ID | Role | SOPS key | Used by |
|---|---|---|---|---|
| `portal-api` | `362780577813757958` | IAM_OWNER + IAM_LOGIN_CLIENT | `PORTAL_API_ZITADEL_PAT` | portal-api runtime: tenant provisioning, login session finalize |
| `klai-admin-sa` | `369320953139691537` | IAM_OWNER | `ZITADEL_ADMIN_PAT` | Runbooks/scripts: instance features, OIDC app lifecycle, IAM |

**Never** use `PORTAL_API_ZITADEL_PAT` for admin-only operations (features,
app lifecycle). That breaks scope separation — a compromised portal-api
container would yield instance-admin control. Use `ZITADEL_ADMIN_PAT` via
`sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env` on core-01.

**Rotation (both PATs, via API — no console clicking):**
```bash
# Use klai-admin-sa to rotate any PAT, including its own.
ADMIN_PAT=$(ssh core-01 "sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env | cut -d= -f2-")
SA_ID="362780577813757958"   # portal-api, or 369320953139691537 for klai-admin-sa
# 1. Generate new PAT (1-year expiry)
curl -s -X POST "https://auth.getklai.com/management/v1/users/$SA_ID/pats" \
  -H "Authorization: Bearer $ADMIN_PAT" \
  -H "X-Zitadel-Orgid: 362757920133283846" \
  -H "Content-Type: application/json" \
  -d '{"expirationDate": "2027-04-19T00:00:00Z"}'
# 2. Update SOPS, push — GitHub Action auto-syncs
# 3. Recreate container: docker compose up -d portal-api  (for portal-api only)
# 4. Revoke old token: DELETE /management/v1/users/$SA_ID/pats/$OLD_TOKEN_ID
```

Full runbook: `runbooks/platform-recovery.md#zitadel-pat-rotation`.

PAT invalidation symptom (triggers rotation): `Errors.Token.Invalid (AUTH-7fs1e)`.

Current PAT expiry: both PATs expire **2027-04-19**. Rotate at least quarterly
regardless, per `runbooks/credential-rotation.md`.

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

**Fix (via Zitadel v2 Feature API — writes event + updates projection atomically):**
```bash
PAT=$(ssh core-01 "sudo grep '^ZITADEL_ADMIN_PAT=' /opt/klai/.env | cut -d= -f2-")
curl -sf -X PUT "https://auth.getklai.com/v2/features/instance" \
  -H "Authorization: Bearer $PAT" \
  -H "Content-Type: application/json" \
  -d '{"loginV2": {"required": true, "baseUri": "https://my.getklai.com"}}'
```

**Why NOT direct projection UPDATE:** Zitadel is event-sourced. A direct
`UPDATE projections.instance_features5` works temporarily but leaves the
original wrong event in `eventstore.events2`. On the next projection rebuild
(upgrade, `projection truncate`, disaster recovery) the bug returns.
The Feature API writes a new `feature.instance.login_v2.set` event so the
fix survives rebuilds. Payload format is `{"Value": {"base_uri": {...}}}`
in Zitadel v4.12+ (not the older `baseURI` string).

**Prevention:** Never write projection tables directly for config that is
event-sourced. Always use the Zitadel API for features, OIDC apps, users,
and policies. See `runbooks/platform-recovery.md` § zitadel-login-v2-recovery
Step 3 for the full procedure including verification.

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

## Project roles and JWT claims

Canonical mapping between portal `InviteRequest.role` (Pydantic Literal),
the Zitadel project-role string passed to `grant_user_role`, and the
JWT `urn:zitadel:iam:org:project:roles` claim a downstream service
receives. Source of truth for `_extract_role` admin-equivalence audits
(SPEC-SEC-TENANT-001 REQ-3, REQ-4).

| Portal role | Zitadel grant role | JWT claim shape |
|---|---|---|
| `admin` | `org:owner` | `{"org:owner": {}}` |
| `group-admin` | `org:group-admin` | `{"org:group-admin": {}}` |
| `member` | `org:member` | `{"org:member": {}}` |

The mapping is implemented as `_ZITADEL_ROLE_BY_PORTAL_ROLE` in
`klai-portal/backend/app/api/admin/users.py` (frozen module-level
`Final[Mapping[str, str]]`, REQ-2.2). Adding a new portal role requires
updating BOTH the InviteRequest Literal AND the mapping AND this table
in the same change — REQ-2.3 raises HTTP 500 at runtime if the schema
diverges from the mapping.

### Admin equivalence in retrieval-api

`klai-retrieval-api/retrieval_api/middleware/auth.py::_extract_role`
treats the following claim values as admin-equivalent for the
`verify_body_identity` cross-org / cross-user bypass (SPEC-SEC-010
REQ-3.1):

- `admin` — bare role label, not produced by the invite flow above.
- `org_admin` — bare role label, not produced by the invite flow above.

Neither value is reachable through the REQ-2 mapping; both are legacy
guards that pre-date the explicit role mapping. SPEC-SEC-TENANT-001
REQ-4 audits whether they remain necessary. The mapped roles
(`org:owner`, `org:group-admin`, `org:member`) are NOT in the
admin-equivalent set — the cross-org check fires for member-equivalent
JWTs as expected.

`org:owner` is the natural "admin-ish" candidate but is intentionally
NOT in the admin-equivalent set. Adding it would silently grant
admin-bypass to every invited user under the REQ-2 mapping (since
`portal_role="admin"` maps to `org:owner`). The mapping's design
keeps the admin label unique to the portal `admin` role; downstream
admin-equivalence is a separate, smaller-blast-radius decision.

### How to verify the claim shape end-to-end

1. Invite a test user via the portal admin UI with each of the three
   roles in turn (or via `POST /api/admin/users/invite` against a dev
   environment).
2. Have the user complete the invite flow and obtain an access token
   (e.g. by signing in to the portal and capturing the access_token
   cookie or by using the OIDC code-exchange directly).
3. Decode the access token's payload (no signature verification needed
   for this read-only check):
   ```bash
   echo '<jwt>' | cut -d. -f2 | base64 -d 2>/dev/null | jq '."urn:zitadel:iam:org:project:roles"'
   ```
4. Expected output for each portal role:
   - `admin`       -> `{"org:owner": {...}}`
   - `group-admin` -> `{"org:group-admin": {...}}`
   - `member`      -> `{"org:member": {...}}`

If the JWT carries a different role key than the mapping predicts, the
Zitadel project configuration has drifted from the mapping (or vice
versa). Update the mapping AND this section in the same commit — the
two MUST stay in lock-step.
