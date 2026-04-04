# Admin Role System

## Roles

| Role | Value | Access |
|------|-------|--------|
| Admin | `admin` | Full access to `/admin/*` — users, billing, settings |
| Member | `member` | Access to `/app/*` only |

## How roles are stored

Roles are stored in the portal database (`users` table, `role` column) as a string enum (`admin` / `member`). The default role for new users is `member`.

The Zitadel user ID (`zitadel_user_id`) is the primary key linking the portal user record to the Zitadel identity.

## How roles are enforced

**Backend:** The `/api/admin/*` endpoints check the caller's role using the Bearer token:

1. Validate Bearer token via Zitadel userinfo endpoint
2. Look up the user in the portal database by `zitadel_user_id`
3. Verify `role == "admin"` — return `403` otherwise

**Frontend:** The route guard at `/admin/*` reads the `is_admin` field from the portal user API:

- If `is_admin: false`: redirect to `/app`
- The guard runs on every navigation to an `/admin/*` route

Additionally, `sessionStorage.getItem('klai:isAdmin')` is used post-2FA-setup to determine the redirect target (`/admin` or `/app`). This is a hint only — the real enforcement is always on the API.

## Role management

Admins can change any other user's role via the admin users page (`/admin/users`). An admin cannot change their own role (the role selector is disabled for the current user).

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/admin/users` | List all users in the organisation |
| `DELETE` | `/api/admin/users/{user_id}` | Remove a user |
| `PATCH` | `/api/admin/users/{user_id}/role` | Change a user's role |
| `POST` | `/api/admin/users/invite` | Invite a new user |

All four endpoints require `role == "admin"` on the calling user.

## First admin

The first admin user is created during the signup flow when a new organisation is provisioned. The provisioning logic sets `role = "admin"` for the organisation creator.

## Invitation flow

When an admin invites a user:
1. A Zitadel user is created with an invitation code (`sendCodes: true` via Management API)
2. A portal user record is created with the specified role
3. Zitadel sends an invitation email via klai-mailer (`InviteUser` notification type)
4. The invited user sets their password via the link in the email

## User Groups

Groups provide organizational structure within an org. Each group belongs to exactly one org.

### Data Model

| Table | Columns |
|-------|---------|
| `portal_groups` | id, org_id, name, description, created_at, created_by |
| `portal_group_memberships` | id, group_id, zitadel_user_id, is_group_admin, joined_at |

- Group names are unique per org (case-insensitive)
- ON DELETE CASCADE on memberships when group is deleted

### API Endpoints

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/admin/groups` | List all groups | Admin |
| POST | `/api/admin/groups` | Create group | Admin |
| PATCH | `/api/admin/groups/{id}` | Update group | Admin |
| DELETE | `/api/admin/groups/{id}` | Delete group | Admin |
| GET | `/api/admin/groups/{id}/members` | List members | Admin |
| POST | `/api/admin/groups/{id}/members` | Add member | Admin |
| DELETE | `/api/admin/groups/{id}/members/{user_id}` | Remove member | Admin |
| PATCH | `/api/admin/groups/{id}/members/{user_id}` | Toggle group admin | Admin |

### Group Admins

Group admins can manage members of their group without needing the org-level admin role.

## Group-Based Product Entitlements

Products are assigned at group level. Users inherit product access through group membership.

### How it works

1. Org admin assigns products to a group via `portal_group_products`
2. All members of that group inherit those products
3. Effective products = union of direct (per-user) + inherited (via groups)
4. JWT tokens are enriched with effective products (~15 min expiry)

### API Endpoints

| Method | Endpoint | Description | Role |
|--------|----------|-------------|------|
| GET | `/api/admin/groups/{group_id}/products` | List group products | Admin |
| POST | `/api/admin/groups/{group_id}/products` | Assign product | Admin |
| DELETE | `/api/admin/groups/{group_id}/products/{product}` | Revoke product | Admin |

### Per-user product toggles

Per-user product assignment still works for backwards compatibility. The admin UI shows "Effective Products" (read-only) combining direct + inherited assignments.

## User Lifecycle

Users transition through three states:

| State | Description | Reversible |
|-------|-------------|------------|
| `active` | Normal access | — |
| `suspended` | Access revoked, data preserved | Yes → reactivate |
| `offboarded` | Cascading cleanup, non-reversible | No |

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/users/{id}/suspend` | Suspend user |
| POST | `/api/admin/users/{id}/reactivate` | Reactivate suspended user |
| POST | `/api/admin/users/{id}/offboard` | Permanently offboard user |

### Suspension
- Revokes access via Zitadel user deactivation
- Preserves all data and memberships for potential reactivation

### Offboarding
- Destructive: cascading cleanup of memberships and product assignments
- Non-reversible: user cannot be reactivated after offboarding
- Requires confirmation dialog in the admin UI
