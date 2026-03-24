# SPEC-AUTH-001: User Groups & Lifecycle -- Acceptance Criteria

**SPEC ID:** SPEC-AUTH-001
**Status:** Draft

---

## Test Scenarios

### TS-001: Group Creation (R1, R2)

**Given** an authenticated admin user in org "Acme"
**When** the admin creates a group with name "Engineering" and description "Dev team"
**Then** a `portal_groups` record is created with:
- `org_id` matching the admin's org
- `name` = "Engineering"
- `created_by` = admin's Zitadel user ID
- `created_at` is set to current timestamp
**And** the response status is `201 Created`
**And** the response body contains the group ID, name, and description

### TS-002: Group Name Uniqueness Within Org (R1)

**Given** an existing group "Engineering" in org "Acme"
**When** an admin in org "Acme" creates another group with name "engineering" (case-insensitive match)
**Then** the system returns `409 Conflict`
**And** no new group record is created

### TS-003: Group Name Uniqueness Across Orgs (R1)

**Given** an existing group "Engineering" in org "Acme"
**When** an admin in org "BetaCorp" creates a group with name "Engineering"
**Then** the group is created successfully with status `201 Created`
**And** the group belongs to org "BetaCorp"

### TS-004: Add Member to Group -- Same Org (R3)

**Given** a group "Engineering" in org "Acme"
**And** a user "alice" in org "Acme" who is not a member of "Engineering"
**When** an admin adds "alice" to "Engineering"
**Then** a `portal_group_memberships` record is created with:
- `group_id` matching "Engineering"
- `zitadel_user_id` matching "alice"
- `is_group_admin` = false
**And** the response status is `201 Created`

### TS-005: Prevent Cross-Org Membership (R3, R5)

**Given** a group "Engineering" in org "Acme"
**And** a user "bob" in org "BetaCorp"
**When** an admin in org "Acme" attempts to add "bob" to "Engineering"
**Then** the system returns `403 Forbidden`
**And** no membership record is created

### TS-006: Group Admin Can Manage Members (R4)

**Given** a group "Engineering" in org "Acme"
**And** user "carol" is a member of "Engineering" with `is_group_admin = true`
**And** user "carol" does NOT have org-level admin role
**When** "carol" adds user "dave" (same org) to "Engineering"
**Then** the membership is created successfully with status `201 Created`

**Given** user "carol" is a group admin of "Engineering"
**When** "carol" removes user "dave" from "Engineering"
**Then** the membership is removed successfully with status `204 No Content`

### TS-007: Group Admin Cannot Manage Other Groups (R4)

**Given** user "carol" is a group admin of "Engineering" but not of "Marketing"
**When** "carol" attempts to add a user to "Marketing"
**Then** the system returns `403 Forbidden`

### TS-008: User Suspension (R6)

**Given** an active user "alice" in org "Acme" who is a member of groups "Engineering" and "Marketing"
**When** an admin suspends "alice"
**Then** `portal_users.status` for "alice" is set to `suspended`
**And** all group memberships for "alice" are preserved (not removed)
**And** the response status is `200 OK`

### TS-009: Suspended User Cannot Authenticate (R6)

**Given** a suspended user "alice"
**When** "alice" attempts to access any authenticated endpoint
**Then** the system returns `401 Unauthorized` (enforced by Zitadel, not portal)

### TS-010: User Reactivation

**Given** a suspended user "alice"
**When** an admin reactivates "alice"
**Then** `portal_users.status` for "alice" is set to `active`
**And** all previously preserved group memberships remain intact
**And** the response status is `200 OK`

### TS-011: Reactivation of Non-Suspended User

**Given** an active user "alice"
**When** an admin attempts to reactivate "alice"
**Then** the system returns `409 Conflict` with message indicating user is not suspended

### TS-012: User Offboarding (R7)

**Given** an active user "alice" in org "Acme" who:
- Is a member of groups "Engineering" and "Marketing"
- Has product assignments: chat, scribe
**When** an admin offboards "alice"
**Then** all group memberships for "alice" are removed
**And** all product assignments for "alice" are revoked (AUTH-002)
**And** `zitadel.deactivate_user()` is called for "alice"
**And** `portal_users.status` for "alice" is set to `offboarded`
**And** the response status is `200 OK`

### TS-013: Offboarding Already Offboarded User (R7)

**Given** an offboarded user "alice"
**When** an admin attempts to offboard "alice" again
**Then** the system returns `409 Conflict`

### TS-014: Suspension of Offboarded User

**Given** an offboarded user "alice"
**When** an admin attempts to suspend "alice"
**Then** the system returns `409 Conflict` with message indicating user is already offboarded

### TS-015: Group Deletion Cascades Memberships

**Given** a group "Engineering" with 5 members
**When** an admin deletes the group "Engineering"
**Then** the group record is removed
**And** all 5 membership records are removed (CASCADE)
**And** the response status is `204 No Content`

### TS-016: List Groups Filtered by Org

**Given** org "Acme" has groups: Engineering, Marketing
**And** org "BetaCorp" has groups: Sales, Support
**When** an admin in org "Acme" calls `GET /api/admin/groups`
**Then** the response contains only Engineering and Marketing
**And** Sales and Support are not included

### TS-017: Duplicate Membership Prevention

**Given** user "alice" is already a member of group "Engineering"
**When** an admin attempts to add "alice" to "Engineering" again
**Then** the system returns `409 Conflict`

### TS-018: Bulk Import (R8, Optional)

**Given** a group "Engineering" in org "Acme"
**And** users "alice", "bob", "carol" in org "Acme"
**And** "alice" is already a member of "Engineering"
**When** an admin bulk-imports ["alice", "bob", "carol"] to "Engineering"
**Then** "bob" and "carol" are added as members
**And** "alice" is skipped (already a member)
**And** the response includes a summary: 2 added, 1 skipped

---

## Quality Gate Criteria

### Functional Completeness

- [ ] All 8 requirements (R1-R8) have corresponding test scenarios
- [ ] All API endpoints return correct status codes and response bodies
- [ ] Cross-org isolation is verified with explicit negative test cases
- [ ] User lifecycle transitions follow valid state machine: active -> suspended -> active, active -> offboarded (terminal)

### Test Coverage

- [ ] Unit tests for group CRUD operations
- [ ] Unit tests for membership management with org validation
- [ ] Unit tests for user lifecycle state transitions
- [ ] Integration tests for offboarding cascade (groups + products + Zitadel)
- [ ] Integration tests for `_require_admin_or_group_admin()` dependency
- [ ] Negative tests for all unwanted behaviors (R5)
- [ ] Target: >= 85% line coverage for new code

### Security Verification

- [ ] No endpoint allows cross-org data access
- [ ] Group admin scope is limited to their specific group
- [ ] Offboarding revokes all access paths (groups, products, Zitadel)
- [ ] All endpoints require authentication
- [ ] Admin-only endpoints reject non-admin users with `403`

### Database Integrity

- [ ] UNIQUE constraints prevent duplicate group names per org
- [ ] UNIQUE constraints prevent duplicate memberships
- [ ] CASCADE on group deletion removes memberships
- [ ] Status column has CHECK constraint for valid values
- [ ] Migrations are reversible (up and down)

### Performance

- [ ] Group listing query uses index on `(org_id)`
- [ ] Membership queries use index on `(group_id, zitadel_user_id)`
- [ ] Offboarding completes within 5 seconds (including Zitadel call)

---

## Definition of Done

1. All migrations pass (up and down) on clean database
2. All test scenarios pass with >= 85% coverage
3. API documentation is updated with new endpoints
4. Frontend group management UI is functional
5. User lifecycle controls (suspend/reactivate/offboard) work end-to-end
6. Cross-org isolation verified via integration tests
7. `_get_caller_org()` successfully extracted to `dependencies.py` without breaking existing endpoints
8. Code reviewed and approved
