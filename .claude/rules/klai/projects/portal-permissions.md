---
paths:
  - "klai-portal/backend/**/*.py"
---
# Portal permissions

Source of truth: `app/core/permissions.py`, `app/core/profiles.py`,
`app/core/seats.py`, and `app/core/features.py`.

## Current model

Keep these axes separate:

- **Profile role** is the permission hierarchy: `personal` < `company` <
  `kb_manager` < `group_manager` < `admin`.
- **Seat type** is the per-user billing axis. Effective capabilities are the
  profile's capabilities filtered through the seat's features. Admin has the
  explicit KNOWLEDGE-seat bypass implemented in `permissions.py`.
- **Plan and platform unlocks** determine workspace products.
  `derive_user_products(role, plan, platform_unlocked_features)` combines plan
  products with unlocked user-facing products, then applies the profile floor.

There are no per-user or per-group product overrides. Do not read legacy
product-assignment tables as entitlement gates. Pure platform gates such as
`widgets`, `custom_mcps`, and `partner_api` do not appear in
`effective_products`; they are checked with `require_platform_unlocked()`.

`UserPermissions` is the request snapshot. It carries identity, role, plan,
seat, platform unlocks, effective capabilities/products, KB limits, and
platform-admin status. Reuse it instead of issuing a second permissions query.

KB quotas remain a separate role-plus-plan calculation in
`effective_kb_limits()`: the more restrictive limit wins.

## Endpoint gates

Use declarative FastAPI dependencies:

```python
perms: UserPermissions = Depends(get_caller_at_least(ProfileRole.COMPANY))
perms: UserPermissions = Depends(require_capability(Capability.KB_TAXONOMY))
perms: UserPermissions = Depends(require_product("knowledge"))
perms: UserPermissions = Depends(require_platform_unlocked("widgets"))
perms: UserPermissions = Depends(require_platform_admin())
```

Use `assert_platform_unlocked()` only in non-OIDC dependency paths that already
resolve a `PortalOrg`, such as partner-key authentication. Do not substitute a
product check for a platform-only gate, or a role check for a capability gate.

## Changing permissions

- New role or capability: update `profiles.py`, the seat mapping in `seats.py`,
  and the relevant declarative gate.
- New user-facing product: add its profile floor to `FEATURE_MIN_PROFILE` and
  include it in a plan and/or the platform-unlock registry.
- New platform-only feature: register it in
  `app/api/admin/platform_unlocks.py`, omit it from `FEATURE_MIN_PROFILE`, and
  gate it with `require_platform_unlocked()`.
- New quota behavior: change the profile and plan limit sources together;
  neither may silently widen the other.

Run the focused tests in `tests/test_permissions.py`,
`tests/test_features_derive.py`, `tests/test_features_derive_regression.py`,
and `tests/test_platform_unlocks_phase5.py` after changing this model.
