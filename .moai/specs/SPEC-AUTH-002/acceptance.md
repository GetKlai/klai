# SPEC-AUTH-002: Product Entitlements -- Acceptance Criteria

**SPEC ID:** SPEC-AUTH-002
**Status:** Draft

---

## Test Scenarios

### TS-001: Plan-to-Products Mapping (R1)

**Given** the `PLAN_PRODUCTS` constant is defined
**When** the system queries products for plan "core"
**Then** the result is `["chat"]`

**Given** the `PLAN_PRODUCTS` constant is defined
**When** the system queries products for plan "professional"
**Then** the result is `["chat", "scribe"]`

**Given** the `PLAN_PRODUCTS` constant is defined
**When** the system queries products for plan "complete"
**Then** the result is `["chat", "scribe", "knowledge"]`

**Given** the `PLAN_PRODUCTS` constant is defined
**When** the system queries products for plan "free"
**Then** the result is `[]`

### TS-002: Auto-Assignment on Invite (R2)

**Given** org "Acme" has plan "professional" (products: chat, scribe)
**And** org "Acme" has 5 seats with 3 active users
**When** an admin invites a new user "alice"
**Then** `portal_user_products` records are created for "alice" with products: chat, scribe
**And** each record has `enabled_by` = admin's user ID
**And** each record has `enabled_at` set to current timestamp
**And** the invite response status is `201 Created`

### TS-003: Seat Limit Enforcement (R8)

**Given** org "Acme" has 5 seats with 5 active users
**When** an admin attempts to invite a new user
**Then** the system returns `409 Conflict` with detail "Seat limit reached"
**And** no user is created
**And** no product assignments are created

### TS-004: Seat Counting Excludes Non-Active Users (R8)

**Given** org "Acme" has 5 seats with 4 active users and 1 suspended user
**When** an admin invites a new user
**Then** the invite succeeds with status `201 Created`
**And** active user count becomes 5 (suspended user is not counted)

### TS-005: Product Gate -- Access Granted (R3)

**Given** user "alice" has an enabled `portal_user_products` record for product "chat"
**When** "alice" accesses a route gated by `require_product("chat")`
**Then** the request proceeds normally

### TS-006: Product Gate -- Access Denied (R3)

**Given** user "alice" does NOT have a `portal_user_products` record for product "knowledge"
**When** "alice" accesses a route gated by `require_product("knowledge")`
**Then** the system returns `403 Forbidden` with detail "Product 'knowledge' not available"

### TS-007: Plan Ceiling Enforcement (R4)

**Given** org "Acme" has plan "professional" (ceiling: chat, scribe)
**When** an admin attempts to assign product "knowledge" to user "alice" in org "Acme"
**Then** the system returns `403 Forbidden` with detail indicating product exceeds plan
**And** no product assignment is created

### TS-008: Valid Product Assignment Within Ceiling (R4)

**Given** org "Acme" has plan "professional" (ceiling: chat, scribe)
**And** user "alice" has product "chat" but not "scribe"
**When** an admin assigns product "scribe" to "alice"
**Then** a `portal_user_products` record is created for "alice" with product "scribe"
**And** the response status is `201 Created`

### TS-009: Duplicate Product Assignment Prevention

**Given** user "alice" already has product "chat" assigned
**When** an admin attempts to assign product "chat" to "alice" again
**Then** the system returns `409 Conflict`

### TS-010: Product Revocation

**Given** user "alice" has products: chat, scribe
**When** an admin revokes product "scribe" from "alice"
**Then** the `portal_user_products` record for "alice" + "scribe" is deleted
**And** "alice" still has product "chat"
**And** the response status is `204 No Content`

### TS-011: Plan Upgrade -- Products Become Assignable (R5)

**Given** org "Acme" has plan "core" (products: chat)
**And** user "alice" has product: chat
**When** org "Acme" is upgraded to plan "professional"
**Then** product "scribe" becomes assignable to users in org "Acme"
**And** user "alice" still has only product "chat" (no auto-enable)
**And** an admin can now assign "scribe" to "alice"

### TS-012: Plan Downgrade -- Over-Ceiling Revocation (R6)

**Given** org "Acme" has plan "complete" (products: chat, scribe, knowledge)
**And** user "alice" has products: chat, scribe, knowledge
**And** user "bob" has products: chat, scribe
**When** org "Acme" is downgraded to plan "core" (products: chat)
**Then** "alice"'s products "scribe" and "knowledge" are revoked
**And** "bob"'s product "scribe" is revoked
**And** "alice" and "bob" retain product "chat"
**And** each revocation is logged

### TS-013: JWT Product Claims (R7)

**Given** user "alice" has enabled products: chat, scribe
**When** "alice" authenticates and receives a JWT from Zitadel
**Then** the JWT contains claim `klai:products` with value `["chat", "scribe"]`

### TS-014: JWT Claims -- No Products

**Given** user "bob" has no enabled product assignments
**When** "bob" authenticates and receives a JWT from Zitadel
**Then** the JWT contains claim `klai:products` with value `[]`

### TS-015: JWT Claims -- Portal API Timeout (R7)

**Given** the portal internal API is unreachable
**When** a user authenticates and the Zitadel Action attempts to fetch products
**Then** the JWT contains claim `klai:products` with value `[]` (fail-closed)
**And** the `require_product()` dependency acts as the authoritative gate

### TS-016: Product Summary Endpoint (R9, Optional)

**Given** org "Acme" has:
- 10 users with product "chat"
- 7 users with product "scribe"
- 3 users with product "knowledge"
**When** an admin calls `GET /api/admin/products/summary`
**Then** the response contains:
- chat: 10
- scribe: 7
- knowledge: 3

### TS-017: Offboarding Revokes All Products (Cross-SPEC with AUTH-001)

**Given** user "alice" has products: chat, scribe, knowledge
**When** an admin offboards "alice" (AUTH-001 R7)
**Then** all product assignments for "alice" are deleted
**And** "alice"'s products count is 0

### TS-018: List Available Products for Org

**Given** org "Acme" has plan "professional"
**When** an admin calls `GET /api/admin/products`
**Then** the response lists: chat, scribe
**And** "knowledge" is not listed as available

---

## Quality Gate Criteria

### Functional Completeness

- [ ] All 9 requirements (R1-R9) have corresponding test scenarios
- [ ] Plan-to-products mapping is correct for all plan levels
- [ ] Auto-assignment on invite works for all plan types
- [ ] Seat enforcement is atomic (no race conditions)
- [ ] Product gate returns correct status codes (403 for denied, pass-through for granted)
- [ ] Plan upgrade does not auto-enable products
- [ ] Plan downgrade revokes over-ceiling products

### Test Coverage

- [ ] Unit tests for `PLAN_PRODUCTS` mapping and helper functions
- [ ] Unit tests for `require_product()` dependency (granted and denied cases)
- [ ] Unit tests for seat limit enforcement
- [ ] Unit tests for plan ceiling validation
- [ ] Integration tests for invite flow with auto-assignment
- [ ] Integration tests for plan upgrade/downgrade flows
- [ ] Integration tests for Zitadel Action JWT enrichment
- [ ] Negative tests for all unwanted behaviors (R4)
- [ ] Target: >= 85% line coverage for new code

### Security Verification

- [ ] Product assignments cannot exceed plan ceiling
- [ ] Internal products API is not accessible from public routes
- [ ] `require_product()` cannot be bypassed by direct DB access
- [ ] Seat limit cannot be circumvented by concurrent requests
- [ ] JWT claims cannot be manipulated client-side (verified by `require_product()`)

### Database Integrity

- [ ] UNIQUE constraint prevents duplicate product assignments per user
- [ ] Backfill migration correctly assigns products for all existing users
- [ ] Migration is reversible (up and down)
- [ ] Index on `(org_id, product)` exists for summary queries

### Performance

- [ ] Seat count query uses appropriate index
- [ ] `require_product()` executes in < 10ms
- [ ] Backfill migration handles 10K+ users without timeout
- [ ] Zitadel Action completes in < 500ms

---

## Definition of Done

1. All migrations pass (up and down) on clean database
2. Backfill migration verified on staging data
3. All test scenarios pass with >= 85% coverage
4. `require_product()` dependency applied to at least one route per product
5. Zitadel Action deployed and JWT claims verified
6. Seat enforcement tested with concurrent requests
7. Plan upgrade/downgrade flows tested end-to-end
8. Frontend product toggles functional in invite flow
9. Internal API secured with API key or IP allowlist
10. Code reviewed and approved
