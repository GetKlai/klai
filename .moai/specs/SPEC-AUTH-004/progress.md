## SPEC-AUTH-004 Progress

- Started: 2026-03-24
- Completed: 2026-03-24

## Delivered

### Backend
- `portal_group_products` table + Alembic migration
- `PortalGroupProduct` model (`portal/backend/app/models/groups.py`)
- Group product CRUD endpoints: `GET/POST/DELETE /api/admin/groups/{group_id}/products`
- `get_effective_products()` service (`portal/backend/app/services/entitlements.py`)
- `GET /api/admin/users/{user_id}/effective-products` with source attribution
- `require_product()` and `/internal/user-products` updated to use effective products (dual-mode)
- `change_plan` extended to clean up `PortalGroupProduct` rows exceeding plan ceiling
- Provisioning step 10: auto-create product groups for new orgs

### Frontend
- Group detail page: product toggle switches per plan product
- User edit page: per-user product toggles replaced with read-only effective products card (source badges)
- i18n keys added for all new UI text (en + nl)
- Paraglide message files generated

## Commits
- `0ecc4e1` feat(auth): effective entitlement resolution via group inheritance
- `e9bcd9c` fix(portal): apply ruff format to SPEC-AUTH-004 files
- `58168b9` feat(auth): group-based product entitlements (SPEC-AUTH-004)
- `fec2027` fix(auth): remove unused interface and fix ruff format in SPEC-AUTH-004 files
