---
paths:
  - "klai-portal/backend/**/*.py"
---
# Portal Permissions — RBAC-001 v0.2.0

Reference for the five-layer permission model introduced by SPEC-PORTAL-RBAC-REFACTOR-001.
Source of truth: `klai-portal/backend/app/core/permissions.py` and `klai-portal/backend/app/core/features.py`.

## Five-Layer Model

Effective access = intersection of all five layers (all must pass):

| Layer | Controls | Config location |
|---|---|---|
| **Plan** | Which features an org's subscription unlocks | `FEATURE_MIN_PLAN` in `core/features.py` |
| **Add-ons** | Optional extra products a tenant admin enables | `portal_orgs.enabled_addons` (DB) + `ADDON_FEATURES` in `core/features.py` |
| **Platform-features** | Globally on/off toggles independent of plan | `PLATFORM_LOCKED_FEATURES` in `core/features.py` |
| **Profile** | User's role-based floor for an action | `ProfileRole` enum: `personal` < `company` < `admin` |
| **Groups** | Fine-grained capability overrides per user | `portal_user_capabilities` / `portal_group_capabilities` (DB) |

## Central Resolver: `UserPermissions`

`UserPermissions` is built once per request by `_resolve_caller_with_options()` in `core/permissions.py`. It contains:

- `user_id`, `org_id`, `effective_role` — identity
- `effective_products` — derived from `(profile, plan, enabled_addons)` triple via `derive_user_products()`
- `effective_capabilities` — union of direct + group capabilities (alias layer, REQ-10)

**Rule:** `(profile, plan, enabled_addons)` is the source of truth for entitlement checks. `portal_user_products` and `portal_group_products` are legacy seat-billing tables — do not use them as entitlement gates in new code.

## Endpoint Patterns

### Profile gate (most common)

```python
from app.core.permissions import ProfileRole, UserPermissions, get_caller_at_least

@router.get("/admin/something")
async def my_admin_endpoint(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> MyResponse:
    # perms.org_id, perms.user_id, perms.effective_role always available
    ...
```

### Capability gate

```python
from app.core.permissions import require_capability

@router.post("/something/sensitive")
async def my_endpoint(
    perms: UserPermissions = Depends(require_capability("manage_team")),
    db: AsyncSession = Depends(get_db),
) -> MyResponse:
    ...
```

### Product gate (check within handler)

```python
@router.get("/feature/data")
async def my_endpoint(
    perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.PERSONAL)),
    db: AsyncSession = Depends(get_db),
) -> MyResponse:
    if "my_product" not in perms.effective_products:
        raise HTTPException(status_code=403, detail="Product not available on your plan")
    ...
```

## How to Add a New Product

1. Add the feature name to `FEATURE_MIN_PROFILE` in `core/features.py`:
   ```python
   FEATURE_MIN_PROFILE: dict[str, ProfileRole] = {
       ...
       "my_new_feature": ProfileRole.PERSONAL,  # minimum profile to use this feature
   }
   ```
2. If it is an optional tenant add-on (toggleable by admins), also add it to `ADDON_FEATURES`:
   ```python
   ADDON_FEATURES: set[str] = {
       ...
       "my_new_feature",
   }
   ```
3. `derive_user_products()` picks it up automatically — no further code changes in the resolver.
4. Gate the endpoint using a product check on `perms.effective_products` (see pattern above).

Do NOT add rows to `portal_user_products` or `portal_group_products` for new features — those tables are legacy seat-billing only.

## How to Add a New Profile Gate

Use `get_caller_at_least(ProfileRole.X)` as a FastAPI `Depends`. Available roles:

```python
class ProfileRole(str, Enum):
    PERSONAL = "personal"   # any authenticated member
    COMPANY  = "company"    # company-plan member or admin
    ADMIN    = "admin"      # org admin only
```

If you need a role between `personal` and `company`, add a new enum value to `ProfileRole` and extend `ROLE_ORDER` in `core/permissions.py`. All existing gates continue to work unchanged.

## How to Add a New Capability Gate

1. Define the capability string (snake_case) — e.g. `"export_data"`.
2. Set it on users/groups via `portal_user_capabilities` / `portal_group_capabilities` DB columns.
3. Gate the endpoint with `Depends(require_capability("export_data"))`.

`UserPermissions.effective_capabilities` is the union of direct + all group capabilities for the caller.

## How to Add a New Platform-Locked Feature

Platform-locked features are globally enabled/disabled (not per-org or per-user).

1. Add a `DB` column or settings field that controls the toggle.
2. Add a helper that reads the toggle.
3. Add the feature name to `PLATFORM_LOCKED_FEATURES` in `core/features.py` and map it to the helper.
4. `derive_user_products()` automatically excludes platform-locked features that are off.

## New Users Join as `"personal"`

When creating a `PortalUser` row anywhere in the codebase (join request approval, admin invite, provisioning), the `role` field MUST be `"personal"`.

```python
# SPEC-PORTAL-RBAC-REFACTOR-001 REQ-11
new_user = PortalUser(
    zitadel_user_id=...,
    org_id=...,
    role="personal",   # NOT "member" — that value is legacy/migration-only
    status="active",
    ...
)
```

`"member"` is handled by `ROLE_MIGRATION_MAP` in `core/profiles.py` for existing rows in the DB — never write it into new rows.

## Key Invariants

- `perms.effective_products` is always derived fresh per request — no caching of product sets across requests.
- `perms.effective_role` is the resolved role after migration map application — always use this, never raw `portal_users.role`.
- Capabilities are additive: a capability granted by ANY group the user belongs to is included.
- Disabling an add-on in `portal_orgs.enabled_addons` takes effect on the next request — no cache invalidation needed.
