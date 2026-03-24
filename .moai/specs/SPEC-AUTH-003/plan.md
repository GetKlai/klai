# SPEC-AUTH-003: Data Rights & Resource Scoping -- Implementation Plan

**SPEC ID:** SPEC-AUTH-003
**Status:** Completed
**Priority:** High
**Dependencies:** SPEC-AUTH-001 (groups/memberships), SPEC-AUTH-002 (product entitlements)
**Dependents:** None (terminal SPEC in the auth chain)

---

## Implementation Strategy

### Approach

This SPEC builds on the group infrastructure from AUTH-001 and product entitlements from AUTH-002 to implement resource-level access control. The core pattern is a scoped query helper (`access.py`) that encapsulates all access logic, preventing ad-hoc query construction throughout the codebase. The audit log is a cross-cutting concern that retroactively instruments AUTH-001 and AUTH-002 operations.

### Architecture Design Direction

- **Access control at the query level:** Instead of middleware-based access checks, access is enforced via scoped query helpers that produce filtered querysets. This eliminates the possibility of forgetting an access check on a new endpoint.
- **Audit log as infrastructure:** The audit log is not a feature of AUTH-003 alone -- it serves all three SPECs. AUTH-003 defines the table and service; AUTH-001 and AUTH-002 operations are instrumented to log events.
- **Qdrant scoping by convention:** Group-scoped knowledge bases use `kb_slug = "group:{group_id}"`. No Qdrant schema change needed.

---

## Milestones

### Primary Goal: Database Schema & Migrations

**Deliverables:**
- Alembic migration `004_add_meeting_group_id.py` for `vexa_meetings.group_id`
- Alembic migration `005_add_audit_log.py` for `portal_audit_log` table
- SQLAlchemy model: `PortalAuditLog`
- Updated `VexaMeeting` model with `group_id` field

**Tasks:**
1. Add `group_id` nullable FK to `VexaMeeting` model
2. Create `PortalAuditLog` model in `app/models/audit.py`
3. Generate Alembic migration for `group_id` column on `vexa_meetings`
4. Generate Alembic migration for `portal_audit_log` table with indexes
5. Test migrations up and down
6. Verify existing meetings retain `group_id = NULL` (preserves current behavior)

### Secondary Goal: Scoped Query Helpers & Audit Service

**Deliverables:**
- `app/services/access.py` with `get_accessible_meetings()` and related helpers
- `app/services/audit.py` with `log_event()` function
- Integration of audit logging into AUTH-001 and AUTH-002 operations

**Tasks:**
1. Create `app/services/access.py` with `get_accessible_meetings()` function
2. Add `can_write_meeting(user_id, meeting, db)` helper for write/delete authorization
3. Add `get_accessible_kb_slugs(user_id, db)` helper for knowledge base scoping
4. Create `app/services/audit.py` with `log_event()` function
5. Instrument AUTH-001 group operations with audit logging:
   - `group.member_added` in `POST /groups/{id}/members`
   - `group.member_removed` in `DELETE /groups/{id}/members/{user_id}`
6. Instrument AUTH-002 product operations with audit logging:
   - `product.assigned` in `POST /users/{id}/products`
   - `product.revoked` in `DELETE /users/{id}/products/{product}`
7. Instrument AUTH-001 lifecycle operations with audit logging:
   - `user.suspended` in `POST /users/{id}/suspend`
   - `user.offboarded` in `POST /users/{id}/offboard`

### Secondary Goal: Backend API -- Resource Scoping

**Deliverables:**
- Modified meetings list endpoint using scoped queries
- Modified meeting creation endpoint with optional `group_id`
- Audit log viewer endpoint

**Tasks:**
1. Modify meetings list endpoint to use `get_accessible_meetings()` instead of direct query
2. Add `group_id` optional field to meeting creation request schema
3. Validate group membership before accepting `group_id` on meeting creation
4. Log `meeting.created` audit event on meeting creation
5. Implement `GET /api/admin/audit-log` with pagination, date range, and filters
6. Add `visibility` field to meeting list response (personal / group name)

### Secondary Goal: Knowledge Base Scoping

**Deliverables:**
- Modified knowledge queries to include group-scoped collections
- `get_accessible_kb_slugs()` integration in knowledge service

**Tasks:**
1. Implement `get_accessible_kb_slugs(user_id, db)` that returns:
   - Personal KB slugs owned by user
   - Group KB slugs matching `group:{group_id}` for user's groups
2. Modify knowledge query logic in `app/services/knowledge.py` to use accessible slugs
3. Verify that users without group membership cannot query group-scoped KBs
4. Test that membership changes immediately affect KB access

### Final Goal: Frontend Integration

**Deliverables:**
- Visibility selector on meeting creation
- Meeting list visibility indicators
- Admin audit log viewer

**Tasks:**
1. Create visibility selector component (dropdown: "Personal" + user's groups)
2. Add visibility indicator to meeting list items (lock icon / group icon)
3. Create audit log viewer page with:
   - Date range picker
   - Action type filter dropdown
   - Resource type filter dropdown
   - Paginated table with actor, action, resource, timestamp
4. Display group name in meeting detail view when group-scoped

---

## Technical Approach

### File Changes

| File | Change Type | Description |
|------|-------------|-------------|
| `app/models/meetings.py` | Modify | Add `group_id` FK to VexaMeeting |
| `app/models/audit.py` | New | PortalAuditLog model |
| `app/services/access.py` | New | Scoped query helpers |
| `app/services/audit.py` | New | Audit log service |
| `app/services/knowledge.py` | Modify | Group-scoped KB queries |
| `app/api/meetings.py` | Modify | Use scoped queries, add group_id field |
| `app/api/admin.py` | Modify | Add audit log endpoint, instrument lifecycle |
| `app/api/groups.py` | Modify | Instrument with audit logging |
| `alembic/versions/004_*.py` | New | meeting group_id migration |
| `alembic/versions/005_*.py` | New | audit log migration |

### Key Design Decisions

1. **Query-level access control:** All access checks live in `access.py` as reusable query builders. This prevents direct DB queries that bypass access checks. Code review should flag any `select(VexaMeeting)` that does not go through `access.py`.
2. **Audit log immutability:** The application layer never issues UPDATE or DELETE on `portal_audit_log`. This is enforced by code convention and PR review; a database-level trigger could be added as hardening.
3. **ON DELETE SET NULL for group_id:** If a group is deleted, meetings previously scoped to that group become personal (owner-only). This is safer than CASCADE (which would delete meetings) or RESTRICT (which would block group deletion).
4. **No caching for access checks:** Membership is checked at query time. This ensures group changes take effect immediately. If performance becomes an issue, a short TTL cache (< 60s) can be added later.
5. **Audit log partitioning:** Not implemented in v1. If the table grows beyond 10M rows, partition by `(org_id, month)` using PostgreSQL declarative partitioning.

---

## Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Scoped query performance with many groups per user | Medium | Medium | Subquery for user's group_ids is indexed; monitor query plan |
| Audit log table growth | High | Low | Partition by month; add retention policy later |
| Bypassing scoped queries via raw SQL | Medium | High | All resource queries must use access.py helpers; code review enforcement |
| Group-scoped Qdrant queries stale after membership change | Low | Medium | Membership check at query time, not cached |
| Breaking existing meeting list behavior | Medium | High | Default `group_id=NULL` preserves current behavior; integration tests |
| Audit log write failures blocking main operations | Low | High | `log_event()` uses `flush()` not `commit()`; failure logged but does not roll back parent transaction |

---

## Implementation Sequencing (Cross-SPEC)

This SPEC starts in Phase 2, after AUTH-001 and AUTH-002 tables exist:

- **Phase 1:** AUTH-001 groups tables + AUTH-002 products table (parallel)
- **Phase 2:** AUTH-003 scoped queries + meeting group_id + audit log + AUTH-002 Zitadel Action
- **Phase 3:** Offboarding cascade + audit log instrumentation across AUTH-001/002 + full frontend integration
