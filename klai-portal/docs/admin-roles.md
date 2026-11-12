# Roles, Products and the User Lifecycle

What a portal user may do and how that is decided: the role ladder, the
capabilities derived from it, the account type, how products and platform
features are granted, groups, invitations, and the suspend, offboard and
delete paths. Authentication itself is in [auth-flows.md](auth-flows.md) and
[api-auth.md](api-auth.md).

The source of truth is `klai-portal/backend/app/core/permissions.py`,
`klai-portal/backend/app/core/profiles.py`,
`klai-portal/backend/app/core/seats.py` and
`klai-portal/backend/app/core/features.py`.

## Where membership and role live

A user's identity (name, e-mail, password, second factors) lives in Zitadel.
Membership of a workspace is a row in `portal_users` with the Zitadel user id,
the integer `org_id`, `role`, `seat_type` and `status`
(`klai-portal/backend/app/models/portal.py:227-261`). One person can have rows
in several workspaces; the session is bound to one of them
([auth-flows.md](auth-flows.md), step 9).

Every authenticated request resolves a `UserPermissions` snapshot in one
query (`resolve_user_permissions`, `permissions.py:171-232`): user id, org id
and slug, role, account type, plan, the org's platform unlocks, effective
capabilities, effective products, KB limits and whether the caller is a
platform admin. Handlers read that snapshot instead of querying again.

## Roles

Five roles in a strict order, each including everything below it
(`profiles.py:41-68`):

| Rank | Role | Default account type |
|---|---|---|
| 1 | `personal` | `chat` |
| 2 | `company` | `chat` |
| 3 | `kb_manager` | `knowledge` |
| 4 | `group_manager` | `knowledge` |
| 5 | `admin` | `knowledge` |

The column default for a new `portal_users` row is `company`
(`models/portal.py:232-245`). The founder of a workspace becomes `admin` at
signup (`klai-portal/backend/app/api/signup.py:455`). The account-type mapping
is `DEFAULT_SEAT_FOR_ROLE` (`seats.py:146-152`).

Gates are FastAPI dependencies on `get_caller`:

- `get_caller_at_least(role)` answers 403 below the given role
  (`permissions.py:372-391`). Members of the platform organisation pass every
  role gate (`has_role_at_least`, `permissions.py:360-369`).
- `require_capability(capability)` checks the effective capabilities
  (`permissions.py:409-424`).
- `require_product(product)` checks the effective products
  (`permissions.py:394-406`); `knowledge.py` and `meetings.py` use a second
  implementation with the same derivation
  (`klai-portal/backend/app/api/dependencies.py:78-101`,
  `klai-portal/backend/app/services/entitlements.py:25-41`).
- `require_platform_unlocked(feature)` checks the org's platform unlocks
  (`permissions.py:456-476`).
- `require_platform_admin()` requires the `admin` role inside the platform
  organisation (`permissions.py:427-453`).

### What each role may do

Derived from the gates in the code:

| Action | Minimum | Gate |
|---|---|---|
| Use chat and the personal knowledge base | `personal` | products, see below |
| Add sources to an organisation knowledge base | `company` | `perms.effective_role == PERSONAL` is refused (`klai-portal/backend/app/api/app_knowledge_sources.py:157-165`, `klai-portal/backend/app/api/connectors.py:991`) |
| Scribe and Docs, once unlocked for the workspace | `company` | `FEATURE_MIN_PROFILE` (`features.py:63-68`) |
| URL and upload connectors | `personal` | `kb.connectors` (`klai-portal/backend/app/api/connectors.py:74`) |
| External connectors, KB members, taxonomy, gaps, activity, organisation templates | `kb_manager` | `kb.connectors.external`, `kb.members`, `kb.taxonomy`, `kb.gaps`, `kb.activity`, `templates.manage_org` (`profiles.py:92-115`) |
| Create organisation knowledge bases, no personal KB limits | `kb_manager` | `PROFILE_LIMITS` (`profiles.py:138-169`) |
| Enter `/admin` in the interface | `kb_manager` | `klai-portal/frontend/src/routes/admin/route.tsx:18-21` |
| Create, edit and delete groups and their members | `group_manager` | `klai-portal/backend/app/api/groups.py:143-425` |
| Users, invitations, join requests, settings, API keys, widgets, Slack, MCP servers, billing, audit log, deleting the workspace | `admin` | `get_caller_at_least(ProfileRole.ADMIN)` in `klai-portal/backend/app/api/admin/` and the admin routers |
| Platform area `/api/admin/platform/*`, platform unlocks, Shield | `admin` in the platform organisation | `require_platform_admin()` |

### Capabilities and account type

A capability is granted when the role has it (`PROFILE_CAPABILITIES`,
`profiles.py:108-114`) and the user's account type unlocks the feature it maps
to (`CAPABILITY_TO_SEAT_FEATURE`, `seats.py:97-111`). The `chat` account type
unlocks `kb.connectors` only; `knowledge` unlocks the full set
(`SEAT_FEATURES`, `seats.py:63-89`). An `admin` always receives the
`knowledge` capability set whatever their account type
(`_derive_effective_capabilities`, `permissions.py:113-153`).

The account type (`portal_users.seat_type`, `chat` or `knowledge`) is also the
billing axis. Invitations set it from the role (`suggest_seat`,
`seats.py:155-166`); `PATCH /api/admin/users/{id}/seat` changes it on its own
(`klai-portal/backend/app/api/admin/users.py:857`), and the users list flags a
user whose account type differs from the role's default (`users.py:115-131`).

### KB limits

`effective_kb_limits(role, plan)` takes the stricter of the role limit and
the plan limit (`profiles.py:188-215`). `personal` and `company` may have five
personal knowledge bases of twenty items each and may not create organisation
knowledge bases; the three higher roles have no personal limits and may
create them, unless the plan lowers that (`profiles.py:138-169`;
`klai-portal/backend/app/core/plan_limits.py:98`).

## Products and platform unlocks

A user's products are `derive_user_products(role, account_type, platform_unlocked_features)`
(`features.py:73-113`):

- both account types grant `chat` and `knowledge` (`ACCOUNT_TYPE_PRODUCTS`,
  `features.py:46-49`);
- `scribe` and `docs` are added when the org has them unlocked and the role is
  at least `company`.

`portal_orgs.platform_unlocked_features` holds the org's unlocks
(`models/portal.py:120-137`). The allowed values are `partner_api`, `widgets`,
`custom_mcps`, `scribe`, `docs`, `librechat_quality_judge`,
`knowledge_activity`, `knowledge_gaps` and `slack`
(`klai-portal/backend/app/core/extensions_registry.py:30-54`). A new org starts
with `partner_api`, `scribe` and `widgets` (`models/portal.py:28`). Only
`scribe` and `docs` become products; the others gate features through
`require_platform_unlocked` or, for partner keys, `assert_platform_unlocked`
(`permissions.py:479-498`). Only platform admins change unlocks, through
`PATCH /api/admin/orgs/{slug}/platform-unlocks`
(`klai-portal/backend/app/api/admin/platform_unlocks.py:115`); a tenant admin reads
the state of their own organisation at `GET /api/admin/extensions`
(`klai-portal/backend/app/api/admin/extensions.py:77`).

Products are not assigned per user or per group; those endpoints answer 410
Gone (see [api-auth.md](api-auth.md)).

## Platform admin

`is_platform_admin` is true for every member of the organisation whose slug is
`PLATFORM_ORG_SLUG` (`permissions.py:209`). Such members pass the tenant role
gates; actions in the platform area additionally require the `admin` role
there. The platform area manages organisations, users across tenants,
messages, statistics and unlocks.

## Zitadel role grant

Authorisation comes from `portal_users.role`. Only `admin` is mirrored to
Zitadel, as the `org:owner` grant; the other four roles get no grant
(`_ZITADEL_ROLE_BY_PORTAL_ROLE`, `users.py:81-87`). A role change through
`PATCH /api/admin/users/{id}/role` syncs that grant after commit
(`users.py:828-833`).

## Groups

Groups give members access to content: knowledge bases shared with a group
and meetings scoped to it (`klai-portal/backend/app/services/access.py:91-160`).
They do not grant products (`groups.py:1-9`).

| Method | Path | Minimum role |
|---|---|---|
| GET | `/api/admin/groups` | `admin` |
| POST | `/api/admin/groups` | `group_manager` |
| PATCH, DELETE | `/api/admin/groups/{group_id}` | `group_manager` |
| GET, POST | `/api/admin/groups/{group_id}/members` | `group_manager` |
| DELETE | `/api/admin/groups/{group_id}/members/{user_id}` | `group_manager` |
| PATCH | `/api/admin/groups/{group_id}/members/{user_id}` | `group_manager`; sets `is_group_admin` |
| GET | `/api/admin/users/{user_id}/groups`, `/api/admin/group-memberships` | `admin` |

Group names are unique per organisation, case-insensitive
(`klai-portal/backend/alembic/versions/q7r8s9t0u1v2_add_portal_groups.py:32`).
A group admin may edit and delete meetings scoped to the group
(`can_write_meeting`, `access.py:91-112`).

## Invitations and joining

`POST /api/admin/users/invite` (`users.py:508-725`, admin):

1. creates the person as a Zitadel user in the portal's Zitadel organisation
   (`ZITADEL_PORTAL_ORG_ID`) with a pre-verified e-mail and no password
   (`invite_user`, `klai-portal/backend/app/services/zitadel.py:258-300`); when
   the e-mail already exists in Zitadel the existing identity is reused;
2. grants `org:owner` in Zitadel only for the `admin` role;
3. writes the `portal_users` row with the role and the account type derived
   from it; an earlier `offboarded` row for the same person is reactivated
   instead;
4. sends the invitation code with a link to `/password/set` on the login host,
   where the invitee chooses a password (`users.py:655-671`).

`POST /api/admin/users/{id}/resend-invite` (`users.py:922`) sends a new code
and, for an offboarded user, reactivates the row and unlocks the Zitadel user.
There is no seat cap on invitations (`users.py:527-533`).

A person whose verified e-mail domain matches a workspace's `primary_domain`
can ask to join it at login ([auth-flows.md](auth-flows.md), step 7). With
`auto_accept_same_domain` they are added at once, otherwise a join request is
filed. Admins handle requests under `/api/admin/join-requests`, with approve
and deny endpoints; approval also works from the one-time link in the
notification mail (`klai-portal/backend/app/api/admin/join_requests.py:63`,
`:94-120`, `:217`).

## Lifecycle

`portal_users.status` is `active`, `suspended` or `offboarded`, enforced by a
check constraint (`models/portal.py:214`). All endpoints below require
`admin` and are scoped to the caller's organisation.

| Method | Path | Effect |
|---|---|---|
| POST | `/api/admin/users/{id}/suspend` | `active` → `suspended`, audited (`users.py:1046-1077`) |
| POST | `/api/admin/users/{id}/reactivate` | `suspended` → `active` (`users.py:1080-1104`) |
| GET | `/api/admin/users/{id}/offboard-preview` | Knowledge bases that need a decision (`users.py:1107`) |
| POST | `/api/admin/users/{id}/offboard` | Offboarding, below (`users.py:1168-1310`) |
| GET | `/api/admin/users/{id}/delete-preview` | As the offboard preview (`users.py:1312`) |
| POST | `/api/admin/users/{id}/delete` | Delete with knowledge-base decisions (`users.py:1331`) |
| DELETE | `/api/admin/users/{id}` | Delete without decisions (`users.py:992-1043`) |
| PATCH | `/api/admin/users/{id}/role` | Change role; refuses to change the last admin (`users.py:763-856`) |
| POST | `/api/admin/users/{id}/promote-admin`, `/demote-admin` | Demotion sets `company` and refuses to leave zero admins (`users.py:1423`, `:1481`) |

**Suspend** sets the portal status to `suspended`. `get_caller` answers 403
`user_suspended` for a suspended user on every request
(`permissions.py:276-285`), so the session stops working while memberships,
products and data stay as they were. Reactivation restores access.

**Offboard** runs in one database transaction: it requires a decision
(transfer or delete) for every knowledge base the preview lists, applies
those decisions, revokes the user's partner API keys and MCP tokens, removes
the user's group memberships in this organisation, removes the internal chat
cases kept for the user, sets `offboarded` and writes the audit row. After the
commit it deactivates the Zitadel user and removes the linked GitHub
organisation membership (`users.py:1175-1310`). An offboarded user returns
through a new invitation, which reactivates the row.

**Delete** removes the `portal_users` row. The Zitadel identity is removed only
when the person has no membership in another workspace; platform-admin
identities cannot be deleted from a tenant (`users.py:1012-1043`). An admin
cannot delete themselves this way.

Deleting a whole workspace is a separate flow: `DELETE /api/admin/org/me` for
its admin and the platform endpoints under `/api/admin/orgs/{slug}/`
(`klai-portal/backend/app/api/admin/deprovision_org.py:137-360`); the runbook is
[tenant-delete.md](../../docs/runbooks/tenant-delete.md).
