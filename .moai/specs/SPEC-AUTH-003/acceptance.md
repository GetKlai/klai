# SPEC-AUTH-003: Data Rights & Resource Scoping -- Acceptance Criteria

**SPEC ID:** SPEC-AUTH-003
**Status:** Draft

---

## Test Scenarios

### TS-001: Org-Level Resource Scoping (R1)

**Given** user "alice" in org "Acme" owns meeting M1
**And** user "bob" in org "BetaCorp" owns meeting M2
**When** "alice" queries her accessible meetings
**Then** the result contains M1
**And** the result does NOT contain M2

### TS-002: Meeting Creation with Group Scope (R2)

**Given** user "alice" in org "Acme" is a member of group "Engineering"
**When** "alice" creates a meeting with `group_id` set to "Engineering"
**Then** a `vexa_meetings` record is created with:
- `zitadel_user_id` = alice's ID (owner)
- `group_id` = Engineering's ID
- `org_id` = Acme's ID
**And** an audit log entry is written: `meeting.created`
**And** the response status is `201 Created`

### TS-003: Meeting Creation Without Group Scope (R2)

**Given** user "alice" in org "Acme"
**When** "alice" creates a meeting without specifying `group_id`
**Then** a `vexa_meetings` record is created with:
- `zitadel_user_id` = alice's ID
- `group_id` = NULL
**And** the response status is `201 Created`

### TS-004: Group-Scoped Meeting -- Read Access for Group Members (R3)

**Given** user "alice" owns meeting M1 with `group_id` = "Engineering"
**And** user "bob" is a member of group "Engineering"
**And** user "carol" is NOT a member of group "Engineering"
**When** "bob" queries his accessible meetings
**Then** the result contains M1

**When** "carol" queries her accessible meetings
**Then** the result does NOT contain M1

### TS-005: Group-Scoped Meeting -- Write Access (R3)

**Given** user "alice" owns meeting M1 with `group_id` = "Engineering"
**And** user "bob" is a regular member of "Engineering" (not group admin)
**And** user "carol" is a group admin of "Engineering"
**When** "bob" attempts to delete M1
**Then** the system returns `403 Forbidden`

**When** "carol" attempts to delete M1
**Then** the deletion succeeds (group admin has write access)

**When** "alice" attempts to delete M1
**Then** the deletion succeeds (owner has write access)

### TS-006: Personal Meeting -- Owner-Only Access (R4)

**Given** user "alice" owns meeting M1 with `group_id` = NULL
**And** user "bob" is in the same org as "alice"
**When** "bob" queries his accessible meetings
**Then** the result does NOT contain M1

**When** "alice" queries her accessible meetings
**Then** the result contains M1

### TS-007: Invalid Group Scope Prevention (R5)

**Given** user "alice" is a member of group "Engineering" but NOT "Marketing"
**When** "alice" creates a meeting with `group_id` set to "Marketing"
**Then** the system returns `403 Forbidden`
**And** no meeting record is created

### TS-008: Access Revocation on Group Removal (R6)

**Given** user "bob" is a member of group "Engineering"
**And** meeting M1 is scoped to group "Engineering"
**And** "bob" can currently access M1
**When** an admin removes "bob" from group "Engineering"
**Then** "bob" can no longer access M1 (immediate effect)
**And** meeting M1 still exists and is accessible to other group members

### TS-009: Audit Log -- Resource Creation (R7)

**Given** user "alice" creates a meeting with group scope
**When** the meeting is created
**Then** `portal_audit_log` contains an entry with:
- `actor_user_id` = alice's ID
- `action` = "meeting.created"
- `resource_type` = "meeting"
- `resource_id` = meeting ID
- `org_id` = alice's org ID
- `created_at` is set

### TS-010: Audit Log -- Group Membership Change (R7)

**Given** an admin adds user "bob" to group "Engineering"
**Then** `portal_audit_log` contains an entry with:
- `action` = "group.member_added"
- `resource_type` = "group"
- `resource_id` = Engineering's ID
- `details` contains `{"user_id": "bob's ID"}`

**Given** an admin removes user "bob" from group "Engineering"
**Then** `portal_audit_log` contains an entry with:
- `action` = "group.member_removed"

### TS-011: Audit Log -- Immutability (R7)

**Given** audit log entries exist
**When** any system component attempts to UPDATE or DELETE an audit log entry
**Then** the operation is rejected at the application level
**And** the original entry remains unchanged

### TS-012: Scoped Query Helper (R8)

**Given** user "alice" owns meetings: M1 (personal), M2 (group "Engineering")
**And** user "bob" is a member of "Engineering" and owns meeting M3 (group "Engineering")
**And** user "carol" is NOT a member of "Engineering" and owns meeting M4 (personal)
**When** `get_accessible_meetings("alice", acme_org_id, db)` is called
**Then** the result contains M1 (owned, personal) and M2 (owned, group)
**And** the result contains M3 (group member access)
**And** the result does NOT contain M4 (carol's personal meeting)

### TS-013: Group-Scoped Knowledge Base Access (R9)

**Given** a knowledge base with `kb_slug` = "group:42" (group ID 42 = "Engineering")
**And** user "alice" is a member of group "Engineering"
**And** user "bob" is NOT a member of group "Engineering"
**When** "alice" queries knowledge bases
**Then** the result includes the "group:42" knowledge base

**When** "bob" queries knowledge bases
**Then** the result does NOT include the "group:42" knowledge base

### TS-014: Knowledge Base Access After Group Membership Change (R9)

**Given** user "alice" is a member of group "Engineering" with KB "group:42"
**When** an admin removes "alice" from "Engineering"
**And** "alice" immediately queries knowledge bases
**Then** the result does NOT include "group:42" (immediate effect, no caching)

### TS-015: Audit Log Viewer -- Pagination and Filtering

**Given** org "Acme" has 100 audit log entries
**When** an admin calls `GET /api/admin/audit-log?page=1&size=20`
**Then** the response contains 20 entries, ordered by `created_at` descending
**And** the response includes pagination metadata (total count, page, size)

**When** an admin calls `GET /api/admin/audit-log?action=meeting.created`
**Then** the response contains only entries with action "meeting.created"

**When** an admin calls `GET /api/admin/audit-log?resource_type=group`
**Then** the response contains only entries with resource_type "group"

### TS-016: Visibility Selector in Frontend (R10, Optional)

**Given** user "alice" is a member of groups "Engineering" and "Marketing"
**When** "alice" opens the meeting creation form
**Then** a visibility selector is displayed with options:
- "Personal"
- "Engineering"
- "Marketing"

**When** "alice" selects "Engineering" and creates the meeting
**Then** the meeting is created with `group_id` = Engineering's ID

### TS-017: Group Deletion -- Meeting Scope Fallback

**Given** meeting M1 has `group_id` = "Engineering"
**When** an admin deletes group "Engineering"
**Then** M1's `group_id` becomes NULL (ON DELETE SET NULL)
**And** M1 is now a personal meeting, accessible only to the owner

### TS-018: Existing Meetings Preserve Current Behavior

**Given** existing meetings in the database before migration
**When** migration `004_add_meeting_group_id.py` runs
**Then** all existing meetings have `group_id` = NULL
**And** existing access patterns are unchanged (owner-only access)

---

## Quality Gate Criteria

### Functional Completeness

- [ ] All 10 requirements (R1-R10) have corresponding test scenarios
- [ ] Org-level scoping verified with cross-org negative tests
- [ ] Group-scoped access verified for both read and write operations
- [ ] Personal meeting access verified as owner-only
- [ ] Audit log immutability verified
- [ ] Knowledge base scoping verified via `kb_slug` pattern
- [ ] Scoped query helpers return correct results for all access patterns

### Test Coverage

- [ ] Unit tests for `get_accessible_meetings()` with various ownership/membership combinations
- [ ] Unit tests for `can_write_meeting()` authorization logic
- [ ] Unit tests for `get_accessible_kb_slugs()` with group membership variations
- [ ] Unit tests for `log_event()` audit service
- [ ] Integration tests for meeting creation with group scope
- [ ] Integration tests for access revocation on group removal
- [ ] Integration tests for audit log viewer with filters and pagination
- [ ] Integration tests for knowledge base scoping with Qdrant
- [ ] Negative tests for all unwanted behaviors (R5)
- [ ] Target: >= 85% line coverage for new code

### Security Verification

- [ ] No endpoint allows cross-org resource access
- [ ] Group membership is validated before setting `group_id` on resources
- [ ] Scoped queries cannot be bypassed by direct DB access (code review)
- [ ] Audit log cannot be modified or deleted
- [ ] Admin-only audit log endpoint rejects non-admin users

### Database Integrity

- [ ] `group_id` FK with ON DELETE SET NULL works correctly
- [ ] Audit log indexes support efficient paginated queries
- [ ] Migration preserves existing meeting data (group_id = NULL)
- [ ] Migrations are reversible (up and down)

### Performance

- [ ] Scoped meeting query executes in < 50ms for user with 10 groups
- [ ] Audit log pagination query executes in < 100ms for 100K entries
- [ ] Knowledge base scoping adds < 20ms to knowledge queries
- [ ] Audit log writes do not block parent transactions

---

## Definition of Done

1. All migrations pass (up and down) on clean database
2. Existing meetings verified to retain `group_id = NULL` after migration
3. All test scenarios pass with >= 85% coverage
4. `get_accessible_meetings()` replaces direct meeting queries in all endpoints
5. Audit log instrumented for AUTH-001 group operations
6. Audit log instrumented for AUTH-002 product operations
7. Audit log instrumented for AUTH-001 lifecycle operations (suspend, offboard)
8. Knowledge base scoping verified with Qdrant integration tests
9. Frontend visibility selector functional
10. Admin audit log viewer functional with filters and pagination
11. Code reviewed and approved
