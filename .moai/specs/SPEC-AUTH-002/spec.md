---
id: SPEC-AUTH-002
version: "1.0.0"
status: draft
created: "2026-03-24"
updated: "2026-03-24"
author: MoAI
priority: high
issue_number: 20
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-03-24 | MoAI | Initial draft |

# SPEC-AUTH-002: Product Entitlements

## Overview

This specification defines the product entitlement system for the Klai portal. Each org has a plan (free, core, professional, complete) that determines which products (chat, scribe, knowledge) are available. Individual users receive product assignments that are gated at both the API level (FastAPI dependency) and the token level (Zitadel JWT `klai:products` claim). Seat limits enforce maximum active users per org.

## Environment

- **Runtime:** FastAPI backend (Python 3.13+, async)
- **Database:** PostgreSQL via SQLAlchemy 2.0 async, Alembic migrations
- **Auth provider:** Zitadel (JWT enrichment via Pre-access-token-creation Action)
- **Frontend:** React/Next.js portal admin interface
- **Existing tables:** `portal_orgs` (with `plan`, `seats` columns), `portal_users`
- **Existing endpoints:** `invite_user()` in `app/api/admin.py`

## Assumptions

- A1: The plan-to-products mapping is an application-level constant, not stored in the database. Changes require a code deployment.
- A2: Zitadel Actions can make HTTP calls to the portal API within an acceptable latency budget (< 500ms).
- A3: JWT tokens have a short expiry (e.g., 15 minutes). Stale product claims are acceptable for the duration of one token lifetime after a product assignment change.
- A4: Plan upgrades make new products assignable but do not auto-enable them. Plan downgrades actively revoke over-ceiling assignments.
- A5: Seat counting uses `portal_users` with `status = 'active'` (status column from AUTH-001).
- A6: The `portal_orgs` table already has `plan` (varchar) and `seats` (integer) columns.

## Requirements

### R1 -- Ubiquitous: Plan-to-Products Mapping

The system shall maintain a plan-to-products mapping as application code: `free = []`, `core = [chat]`, `professional = [chat, scribe]`, `complete = [chat, scribe, knowledge]`.

### R2 -- Event-driven: Auto-Assignment on Invite

WHEN an admin invites a new user THEN the system shall automatically create `portal_user_products` records for all products included in the org's current plan.

### R3 -- State-driven: Product Gate

IF a user does not have an enabled `portal_user_products` record for a given product THEN the system shall return `403 Forbidden` on any route gated by `require_product(product)`.

### R4 -- Unwanted Behavior: Plan Ceiling Enforcement

The system shall not allow an admin to assign a product to a user that exceeds the org's plan ceiling (e.g., assigning "knowledge" to a user in a "professional" org).

### R5 -- Event-driven: Plan Upgrade

WHEN an org's plan is upgraded THEN the system shall make newly available products assignable to users but shall NOT auto-enable them for existing users.

### R6 -- Event-driven: Plan Downgrade

WHEN an org's plan is downgraded THEN the system shall revoke product assignments that exceed the new plan ceiling and log the revocation.

### R7 -- State-driven: JWT Product Claims

IF a user has an enabled product assignment THEN the Zitadel Action (JWT enrichment) shall include that product in the `klai:products` claim of the user's access token.

### R8 -- Ubiquitous: Seat Limit Enforcement

The system shall enforce seat limits at invite time: if `count(active users in org) >= org.seats`, the invite endpoint shall return `409 Conflict`.

### R9 -- Optional: Product Summary Endpoint

Where possible, the system shall provide a `/api/admin/products/summary` endpoint showing per-product user counts for the org.

## Specifications

### Plan-to-Products Mapping

Defined in `app/core/plans.py`:

```python
PLAN_PRODUCTS: dict[str, list[str]] = {
    "free": [],
    "core": ["chat"],
    "professional": ["chat", "scribe"],
    "complete": ["chat", "scribe", "knowledge"],
}
```

### Database Schema

**Table: `portal_user_products`**

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `integer` (PK) | Auto-increment |
| `zitadel_user_id` | `varchar(64)` | NOT NULL |
| `org_id` | `integer` (FK `portal_orgs.id`) | NOT NULL |
| `product` | `varchar(32)` | NOT NULL, CHECK IN (`chat`, `scribe`, `knowledge`) |
| `enabled_at` | `timestamptz` | DEFAULT `now()` |
| `enabled_by` | `varchar(64)` | NOT NULL (admin's Zitadel user ID) |

- UNIQUE constraint on `(zitadel_user_id, product)`
- Index on `(org_id, product)`

### FastAPI Dependencies

**`require_product(product)` in `app/api/dependencies.py`:**

```python
def require_product(product: str):
    async def dependency(
        user_id: str = Depends(get_current_user_id),
        db: AsyncSession = Depends(get_db),
    ):
        result = await db.execute(
            select(PortalUserProduct).where(
                PortalUserProduct.zitadel_user_id == user_id,
                PortalUserProduct.product == product,
            )
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=403,
                detail=f"Product '{product}' not available",
            )
    return Depends(dependency)
```

**Seat check in `invite_user()`:**

```python
active_count = await db.scalar(
    select(func.count()).where(
        PortalUser.org_id == org.id,
        PortalUser.status == "active",
    )
)
if active_count >= org.seats:
    raise HTTPException(status_code=409, detail="Seat limit reached")
```

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/products` | Admin | List available products for org's plan |
| `POST` | `/api/admin/users/{id}/products` | Admin | Assign product to user |
| `DELETE` | `/api/admin/users/{id}/products/{product}` | Admin | Revoke product from user |
| `GET` | `/api/admin/products/summary` | Admin | Per-product user counts (optional) |

### Zitadel Action: JWT Enrichment

- **Trigger:** Pre-access-token-creation
- **Logic:** Call portal API `GET /api/internal/users/{user_id}/products` to retrieve enabled products. Add `klai:products` custom claim to JWT with the product list.
- **Fallback:** On API timeout, emit empty product list (fail-closed). The `require_product()` dependency acts as a second gate.

### Seat Enforcement Flow

1. Admin calls `POST /api/admin/users/invite`
2. Backend counts `portal_users WHERE org_id = X AND status = 'active'`
3. If count >= `org.seats`, return `409 Conflict`
4. Otherwise, create user + auto-assign products from `PLAN_PRODUCTS[org.plan]`

### Plan Change Flow

**Upgrade:**
1. Admin or system updates `portal_orgs.plan`
2. Newly available products become assignable via `POST /users/{id}/products`
3. No auto-enable for existing users

**Downgrade:**
1. Admin or system updates `portal_orgs.plan`
2. System queries all `portal_user_products` where `product NOT IN PLAN_PRODUCTS[new_plan]`
3. Revoke those assignments (DELETE rows)
4. Log each revocation to `portal_audit_log` (AUTH-003)

### Alembic Migrations

- `003_add_portal_user_products.py` -- `portal_user_products` table + backfill from plan

**Backfill logic:** For every active user in each org, insert product records matching `PLAN_PRODUCTS[org.plan]`.

### MX Tag Strategy

| Function | Current Callers | Proposed Tag |
|----------|----------------|--------------|
| `invite_user()` in admin.py | 1 (router) | `@MX:ANCHOR` |
| `remove_user()` in admin.py | 1 (router) | `@MX:NOTE` |
| `PLAN_PRODUCTS` constant | New, 3+ functions | `@MX:ANCHOR` |
| `require_product()` dependency | New, 5+ routes | `@MX:ANCHOR` |

## Traceability

| Requirement | Database | API | Zitadel | Frontend | Migration |
|-------------|----------|-----|---------|----------|-----------|
| R1 | -- | plans.py | -- | Plan display | -- |
| R2 | portal_user_products | invite_user() | -- | Invite flow toggles | 003 |
| R3 | portal_user_products | require_product() | -- | 403 error handling | -- |
| R4 | -- | Validation in POST products | -- | Disabled toggles | -- |
| R5 | -- | Plan upgrade handler | -- | New product badges | -- |
| R6 | DELETE rows | Plan downgrade handler | -- | Notification | -- |
| R7 | portal_user_products | /internal/users/{id}/products | Action script | -- | -- |
| R8 | COUNT query | invite_user() | -- | Seat counter UI | -- |
| R9 | Aggregate query | GET /products/summary | -- | Dashboard widget | -- |

## Cross-SPEC Dependencies

- **AUTH-001:** `portal_users.status` (from AUTH-001) is used for seat counting (`status = 'active'`).
- **AUTH-001:** Offboarding (AUTH-001 R7) must revoke all product assignments for the offboarded user.
- **AUTH-003:** Plan downgrade revocations should be logged to `portal_audit_log` (from AUTH-003).
- **Shared:** `PLAN_PRODUCTS` mapping in `app/core/plans.py` is referenced by AUTH-003 for knowledge base access scoping.
