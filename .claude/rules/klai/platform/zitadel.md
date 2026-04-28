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

## Project roles and JWT claims (authority model)

**Klai uses Zitadel for identity, portal_users.role for authorization.**
This is the canonical pattern that Zitadel itself recommends:
*"ZITADEL only provides RBAC and no permission handling — you get the
role from Zitadel and map to permissions in your own application."*
For Klai that mapping happens in `portal_users.role` (DB column, single
source of truth) plus `_require_admin` and similar checks at the API
layer. JWT `urn:zitadel:iam:org:project:roles` claims do not drive
portal-side authorization decisions and SHOULD NOT be added as a
parallel authority for downstream services either — see "Tech debt" below.

### Mapping table (SPEC-SEC-TENANT-001 v0.5.0 / β)

| Portal role | Zitadel grant role | JWT claim shape |
|---|---|---|
| `admin` | `org:owner` | `{"org:owner": {...}}` |
| `group-admin` | *(no grant)* | claim absent or `{}` |
| `member` | *(no grant)* | claim absent or `{}` |

Implementation: `_ZITADEL_ROLE_BY_PORTAL_ROLE: Final[Mapping[str, str | None]]`
in `klai-portal/backend/app/api/admin/users.py` (REQ-2.2). The
`invite_user` handler skips `grant_user_role` when the mapping value is
`None`, and emits `event="invite_no_zitadel_grant"` so the absence is
queryable in VictoriaLogs (REQ-2.1).

Adding a new portal role requires updating BOTH the
`InviteRequest.role` Literal AND the mapping AND this table in the same
change. REQ-2.3 raises HTTP 500 at runtime if the schema and mapping
diverge.

### Why no Zitadel grant for group-admin / member

1. **Industry alignment.** Multi-tenant B2B SaaS consensus is
   IDP-for-identity + application-(or centralized-authz-service)-for-
   authorization. Replicating portal_users.role into Zitadel project
   roles would create a sync surface with no functional payoff (no
   downstream service today branches on group-admin or member
   role-strings).
2. **Zitadel project-state minimisation.** The Klai Platform Zitadel
   project today has only the `org:owner` role configured. Adding
   `org:group-admin` and `org:member` would require a setup script,
   IAM-admin operational dependency, and ongoing drift-prevention
   between portal Literal and Zitadel project state. None of that
   buys the platform anything today.
3. **Finding #10 surface reduction.** Pre-v0.5.0, every invited user
   received `org:owner` regardless of `body.role`. Adding
   `"org:owner"` to retrieval-api's `_extract_role` admin-equivalent
   set would have silently granted admin to every invited user. By
   making the admin Zitadel grant mean *exactly* "portal admin", the
   role-string is no longer ambiguous; the time-bomb scenario
   disappears at its root.

### Admin equivalence in retrieval-api (current state + tech debt)

`klai-retrieval-api/retrieval_api/middleware/auth.py::_extract_role`
currently treats two claim values as admin-equivalent for the
`verify_body_identity` cross-org / cross-user bypass (SPEC-SEC-010
REQ-3.1):

- `admin` — bare role label. Used by the dev-fixture path; not
  produced by any production invite or signup flow.
- `org_admin` — bare role label. Not produced by any code path in
  the monorepo. Legacy guard, candidate for removal under
  SPEC-SEC-TENANT-001 REQ-4.

`org:owner` is **intentionally NOT** in the admin-equivalent set.
Under the v0.5.0 mapping `portal_role="admin" -> org:owner`, adding
`"org:owner"` here would re-introduce finding #10 in a more direct
form (every signup-created or admin-invited user becomes admin in
retrieval-api). Do not add it without first re-architecting downstream
admin-bypass to use a portal-signed assertion (see Tech debt below).

### Tech debt: replace JWT-claim admin-bypass with portal-signed assertion

The `_extract_role` text-match against `urn:zitadel:iam:org:project:roles`
for cross-tenant decisions is the anti-pattern that finding #10
exemplifies — a coarse role-string in an IDP claim drives a
fine-grained tenant-boundary check. The industry-standard fix is to
have the portal sign an explicit "this user is admin" assertion when
calling downstream services, removing the JWT-claim coupling
altogether. Tracked under SPEC-SEC-IDENTITY-ASSERT-001 (γ direction).
Until that lands, the v0.5.0 mapping is the smaller-blast-radius
holding pattern.

### How to verify the JWT claim shape end-to-end

1. Invite a test user via the portal admin UI for each portal role
   (or via `POST /api/admin/users/invite` against a dev environment).
2. Have the user complete the invite flow and obtain an access token
   (sign in to the portal and capture the access_token cookie, or
   exchange via OIDC code).
3. Decode the access token's payload (no signature verification
   needed for a read-only inspection):
   ```bash
   echo '<jwt>' | cut -d. -f2 | base64 -d 2>/dev/null \
     | jq '."urn:zitadel:iam:org:project:roles"'
   ```
4. Expected output:
   - `admin`       -> `{"org:owner": {...}}`
   - `group-admin` -> `null` or `{}` (claim absent)
   - `member`      -> `null` or `{}` (claim absent)

If the admin JWT carries a key other than `org:owner`, the Zitadel
project state has drifted from the mapping. Update the mapping AND
this section in the same commit. If a non-admin JWT carries any
project-roles content, an unintended `grant_user_role` call has
occurred — grep the portal codebase for `grant_user_role(` and
audit the call sites.
