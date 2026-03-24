# SPEC-AUTH-002: Product Entitlements -- Implementation Plan

**SPEC ID:** SPEC-AUTH-002
**Status:** Draft
**Priority:** High
**Dependencies:** SPEC-AUTH-001 (for `portal_users.status` used in seat counting)
**Dependents:** SPEC-AUTH-003 (for product-gated knowledge access)

---

## Implementation Strategy

### Approach

The product entitlement system is built in layers: (1) plan-to-products mapping as code, (2) database table for user-product assignments, (3) FastAPI dependency for route gating, (4) Zitadel Action for JWT enrichment. The phased rollout applies `require_product()` to new routes first, then existing routes after validation.

### Architecture Design Direction

- **Plan mapping as code:** `PLAN_PRODUCTS` is a Python dict constant, not database-driven. This keeps it simple and version-controlled.
- **Double gate:** Product access is checked both at the API level (`require_product()`) and at the token level (`klai:products` claim). The API check is authoritative; the JWT claim is for client-side routing.
- **Seat enforcement:** Atomic check using `SELECT ... FOR UPDATE` on the org row to prevent race conditions.

---

## Milestones

### Primary Goal: Plan Mapping & Database Schema

**Deliverables:**
- `app/core/plans.py` with `PLAN_PRODUCTS` constant
- Alembic migration `003_add_portal_user_products.py`
- SQLAlchemy model: `PortalUserProduct`
- Backfill migration for existing users

**Tasks:**
1. Create `app/core/plans.py` with `PLAN_PRODUCTS` dict
2. Create `PortalUserProduct` model in `app/models/products.py`
3. Generate Alembic migration for `portal_user_products` table
4. Add backfill step to migration: for each active user, insert products matching `PLAN_PRODUCTS[org.plan]`
5. Test migration up and down, verify backfill correctness
6. Add helper function `get_plan_products(plan: str) -> list[str]` with validation

### Secondary Goal: Backend API -- Product Management

**Deliverables:**
- `require_product()` dependency in `app/api/dependencies.py`
- Product assignment/revocation endpoints
- Seat enforcement in `invite_user()`

**Tasks:**
1. Implement `require_product(product)` dependency in `app/api/dependencies.py`
2. Implement `GET /api/admin/products` -- list available products for org's plan
3. Implement `POST /api/admin/users/{id}/products` -- assign product to user with plan ceiling check
4. Implement `DELETE /api/admin/users/{id}/products/{product}` -- revoke product
5. Modify `invite_user()` to enforce seat limit (`409 Conflict` if at capacity)
6. Modify `invite_user()` to auto-create product assignments from `PLAN_PRODUCTS[org.plan]`
7. Add `require_product("chat")` to chat-related routes
8. Add `require_product("scribe")` to scribe-related routes
9. Add `require_product("knowledge")` to knowledge-related routes

### Secondary Goal: Plan Change Handling

**Deliverables:**
- Plan upgrade/downgrade logic
- Revocation logging (integrates with AUTH-003 audit log when available)

**Tasks:**
1. Implement plan upgrade handler: no auto-enable, just unlock assignability
2. Implement plan downgrade handler:
   - Query over-ceiling product assignments
   - Bulk DELETE over-ceiling rows
   - Log each revocation (stub to `logger.info` until AUTH-003 audit log exists)
3. Add admin endpoint or hook for plan changes (or integrate with existing plan management)
4. Test upgrade path: verify new products are assignable but not auto-enabled
5. Test downgrade path: verify over-ceiling products are revoked

### Secondary Goal: Zitadel Action -- JWT Enrichment

**Deliverables:**
- Internal API endpoint: `GET /api/internal/users/{user_id}/products`
- Zitadel Action script for pre-access-token-creation trigger

**Tasks:**
1. Implement `GET /api/internal/users/{user_id}/products` (internal, no admin auth, service-to-service)
2. Secure internal endpoint (API key or IP allowlist from Zitadel server)
3. Write Zitadel Action script (JavaScript):
   - Call portal internal API
   - Parse response
   - Add `klai:products` claim to access token
   - Handle timeout gracefully (empty claim on failure)
4. Deploy Action to Zitadel instance
5. Test JWT contains `klai:products` claim with correct products
6. Verify fail-closed behavior: when portal API is unreachable, JWT has empty products claim

### Final Goal: Frontend Integration

**Deliverables:**
- Product toggle switches in invite flow
- Product management in user edit view
- Seat counter display
- Plan ceiling visual indicators

**Tasks:**
1. Add product toggle switches to invite flow (pre-checked based on plan)
2. Create product management section in user edit view
3. Disable toggles for products above plan ceiling with tooltip explanation
4. Display seat counter: "X / Y seats used" in admin header
5. Show warning when approaching seat limit (>= 80% capacity)
6. Add notification/toast when plan downgrade revokes products

### Optional Goal: Product Summary Dashboard

**Deliverables:**
- `GET /api/admin/products/summary` endpoint
- Dashboard widget showing per-product user counts

**Tasks:**
1. Implement aggregate query: COUNT users per product per org
2. Create summary endpoint with response including product name and user count
3. Create frontend dashboard widget

---

## Technical Approach

### File Changes

| File | Change Type | Description |
|------|-------------|-------------|
| `app/core/plans.py` | New | PLAN_PRODUCTS constant and helpers |
| `app/models/products.py` | New | PortalUserProduct model |
| `app/api/dependencies.py` | Modify | Add require_product() dependency |
| `app/api/admin.py` | Modify | Seat enforcement in invite, product endpoints |
| `app/api/internal.py` | New | Internal products endpoint for Zitadel |
| `alembic/versions/003_*.py` | New | portal_user_products migration + backfill |

### Key Design Decisions

1. **Plan mapping as code:** Version-controlled, no DB migration for plan changes, easy to test. Trade-off: plan changes require deployment.
2. **Double gate (API + JWT):** Defense in depth. API check is authoritative and always queries DB. JWT claim enables fast client-side routing without API calls.
3. **No auto-enable on upgrade:** Prevents surprise cost increases. Admin explicitly assigns new products to users who need them.
4. **Backfill strategy:** Migration backfills based on current plan. This gives every existing user their full entitlement. Admins can reduce assignments after migration.
5. **Seat check atomicity:** `SELECT ... FOR UPDATE` on the org row prevents TOCTOU race conditions on concurrent invite requests.

---

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Backfill creates incorrect records | Medium | High | Backfill defaults to plan ceiling; admin adjusts after migration |
| Zitadel Action latency on JWT creation | Medium | Medium | Short timeout; cache in Action if needed |
| Plan downgrade leaves stale JWT claims | Low | Medium | JWT expiry + `require_product()` as second gate |
| Race condition: invite + seat check | Low | Low | `SELECT ... FOR UPDATE` on org row during seat check |
| Product gate breaks existing functionality | Medium | High | Phase rollout: new routes first, existing routes after validation |
| Internal API exposed without proper auth | Medium | High | API key or IP allowlist, not exposed on public routes |

---

## Implementation Sequencing (Cross-SPEC)

This SPEC is part of Phase 1 (parallel with AUTH-001):

- **Phase 1:** AUTH-001 groups tables + AUTH-002 products table (parallel)
- **Phase 2:** AUTH-002 Zitadel Action + product gate on existing routes + AUTH-003 scoped queries
- **Phase 3:** Offboarding cascade (AUTH-001 calls AUTH-002 revocation) + full frontend integration
