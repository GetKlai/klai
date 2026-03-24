# SPEC-AUTH-001: User Groups & Lifecycle -- Implementation Plan

**SPEC ID:** SPEC-AUTH-001
**Status:** Draft
**Priority:** High
**Dependencies:** None (foundational SPEC)
**Dependents:** SPEC-AUTH-002, SPEC-AUTH-003

---

## Implementation Strategy

### Approach

Bottom-up implementation: database schema first, then backend API layer, then frontend integration. The shared dependency extraction (`_get_caller_org()` to `dependencies.py`) happens early since it benefits all three SPECs.

### Architecture Design Direction

- **Pattern:** Repository pattern for group data access, service layer for lifecycle orchestration
- **Org isolation:** Every query includes `org_id` filter; enforced at the dependency level
- **Lifecycle orchestration:** Offboarding is a transactional operation that spans groups (AUTH-001), products (AUTH-002), and Zitadel

---

## Milestones

### Primary Goal: Database Schema & Migrations

**Deliverables:**
- Alembic migration `001_add_portal_groups.py` with `portal_groups` and `portal_group_memberships` tables
- Alembic migration `002_add_user_status.py` with `status` column on `portal_users`
- SQLAlchemy models: `PortalGroup`, `PortalGroupMembership`
- Update `PortalUser` model with `status` field

**Tasks:**
1. Create `PortalGroup` model in `app/models/groups.py`
2. Create `PortalGroupMembership` model in `app/models/groups.py`
3. Add `status` field to `PortalUser` model with Literal type
4. Generate Alembic migration for groups tables with UNIQUE constraints and indexes
5. Generate Alembic migration for user status column with backfill
6. Test migrations up and down

### Secondary Goal: Backend API -- Groups CRUD

**Deliverables:**
- `app/api/groups.py` router with all group endpoints
- `app/api/dependencies.py` with extracted shared dependencies
- `_require_admin_or_group_admin()` dependency

**Tasks:**
1. Extract `_get_caller_org()` from `admin.py` to `app/api/dependencies.py`
2. Extract `_require_admin()` to `app/api/dependencies.py`
3. Update `admin.py` imports to use new dependency location
4. Create `app/api/groups.py` with router prefix `/api/admin/groups`
5. Implement `GET /groups` -- list groups filtered by caller's org
6. Implement `POST /groups` -- create group with org_id from caller, created_by from caller
7. Implement `PATCH /groups/{id}` -- update name/description, verify group belongs to caller's org
8. Implement `DELETE /groups/{id}` -- delete group (CASCADE removes memberships)
9. Implement `_require_admin_or_group_admin()` dependency
10. Implement `GET /groups/{id}/members` -- list members, accessible by admin or group admin
11. Implement `POST /groups/{id}/members` -- add member with cross-org validation (R3, R5)
12. Implement `DELETE /groups/{id}/members/{user_id}` -- remove member
13. Implement `PATCH /groups/{id}/members/{user_id}` -- toggle is_group_admin (admin only)
14. Register groups router in main app

### Secondary Goal: Backend API -- User Lifecycle

**Deliverables:**
- Suspend, reactivate, and offboard endpoints in `admin.py`
- Zitadel integration for user deactivation

**Tasks:**
1. Implement `POST /api/admin/users/{id}/suspend` -- set status to suspended, no Zitadel call
2. Implement `POST /api/admin/users/{id}/reactivate` -- set status to active, verify currently suspended
3. Implement Zitadel `deactivate_user()` client call in `app/services/zitadel.py`
4. Implement `POST /api/admin/users/{id}/offboard` -- orchestrate full cleanup:
   - Remove all group memberships
   - Revoke all product assignments (AUTH-002, if available; otherwise stub)
   - Call `zitadel.deactivate_user()`
   - Set status to `offboarded`
5. Add guard: offboarding should not be allowed for already-offboarded users
6. Add guard: suspension should not be allowed for offboarded users

### Final Goal: Frontend Integration

**Deliverables:**
- "Teams" admin section with group management UI
- User lifecycle controls (suspend/reactivate buttons)
- Group membership display on user detail page

**Tasks:**
1. Create group list view component
2. Create group detail view with member list
3. Create add/remove member UI with search
4. Create group admin toggle UI
5. Add suspend/reactivate buttons to user management rows
6. Add offboard button with confirmation dialog
7. Display group memberships on user detail page
8. Display user status badge (active/suspended/offboarded)

### Optional Goal: Bulk Import

**Deliverables:**
- `POST /api/admin/groups/{id}/members/bulk` endpoint
- CSV or JSON bulk import UI

**Tasks:**
1. Implement bulk membership endpoint accepting list of user IDs
2. Validate all users belong to same org in single query
3. Use bulk insert with conflict handling
4. Return summary of added/skipped/failed

---

## Technical Approach

### File Changes

| File | Change Type | Description |
|------|-------------|-------------|
| `app/models/groups.py` | New | PortalGroup, PortalGroupMembership models |
| `app/models/users.py` | Modify | Add `status` field to PortalUser |
| `app/api/dependencies.py` | New | Extracted shared dependencies |
| `app/api/admin.py` | Modify | Update imports, add lifecycle endpoints |
| `app/api/groups.py` | New | Groups router with all CRUD + membership endpoints |
| `app/services/zitadel.py` | Modify | Add `deactivate_user()` method |
| `alembic/versions/001_*.py` | New | Groups tables migration |
| `alembic/versions/002_*.py` | New | User status migration |

### Key Design Decisions

1. **CASCADE on group deletion:** Deleting a group cascades to `portal_group_memberships`. This is acceptable because group deletion is an explicit admin action.
2. **Soft status vs hard delete:** Users are never deleted from `portal_users`. The `status` column tracks lifecycle state. This preserves audit trail and referential integrity.
3. **Group admin scope:** A group admin can only manage members of their specific group, not other groups in the same org. This is enforced by `_require_admin_or_group_admin(group_id)`.
4. **Offboard stub for AUTH-002:** If AUTH-002 is not yet implemented when AUTH-001 ships, the offboard endpoint will include a stub for product revocation that logs a warning and is replaced once AUTH-002 is available.

---

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Group admin escalation outside their scope | Medium | High | Every group operation validates `group.org_id == caller_org.id` |
| Orphaned memberships after user deletion | Low | Medium | CASCADE on FK or explicit cleanup in offboard flow |
| Cross-org group membership via direct API call | Medium | High | Validate `user.org_id == group.org_id` in membership creation |
| Performance with many groups per org | Low | Low | Index on `(org_id)` in `portal_groups` |
| Offboarding partial failure (Zitadel down) | Medium | High | Wrap in transaction; retry Zitadel call; log failure for manual follow-up |

---

## Implementation Sequencing (Cross-SPEC)

This SPEC is part of Phase 1 (parallel with AUTH-002):

- **Phase 1:** AUTH-001 groups tables + AUTH-002 products table (parallel)
- **Phase 2:** AUTH-003 scoped queries + AUTH-002 Zitadel Action + product gate on existing routes
- **Phase 3:** Offboarding cascade + audit log backfill + full frontend integration
