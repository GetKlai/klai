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
1. A Zitadel user is created with an invitation code
2. A portal user record is created with the specified role
3. Zitadel sends an invitation email (via klai-mailer, `InviteUser` notification type)
4. The invited user sets their password via the link in the email
