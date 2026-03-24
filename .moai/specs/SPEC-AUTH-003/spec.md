---
id: SPEC-AUTH-003
version: "1.0.0"
status: draft
created: "2026-03-24"
updated: "2026-03-24"
author: MoAI
priority: high
issue_number: 21
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-03-24 | MoAI | Initial draft |

# SPEC-AUTH-003: Data Rights & Resource Scoping

## Overview

This specification defines the data rights and resource scoping system for the Klai portal. All resources (meetings, knowledge bases) are scoped by org. Within an org, resources can be further scoped to a group (from AUTH-001) for team-level sharing, or kept personal (owner-only). An immutable audit log captures all access control events for compliance and debugging.

## Environment

- **Runtime:** FastAPI backend (Python 3.13+, async)
- **Database:** PostgreSQL via SQLAlchemy 2.0 async, Alembic migrations
- **Vector store:** Qdrant (knowledge base collections, `kb_slug` based)
- **Auth provider:** Zitadel (JWT with `klai:products` claim from AUTH-002)
- **Frontend:** React/Next.js portal interface
- **Existing tables:** `portal_groups`, `portal_group_memberships` (from AUTH-001), `portal_user_products` (from AUTH-002), `vexa_meetings`
- **Existing services:** Qdrant knowledge queries in `app/services/knowledge.py`

## Assumptions

- A1: All existing meetings have `group_id = NULL`, which preserves current owner-only access behavior. No data migration is needed for existing meeting access patterns.
- A2: Qdrant collections use `kb_slug` as their identifier. Group-scoped knowledge bases use the convention `group:{group_id}`.
- A3: The audit log is append-only and immutable. No UPDATE or DELETE operations are permitted on the audit log table.
- A4: Access checks happen at query time (not cached). Group membership changes take effect immediately for subsequent queries.
- A5: The `vexa_meetings` table has a `zitadel_user_id` column identifying the meeting owner, and an `org_id` column for org scoping.
- A6: Resource deletion is out of scope for this SPEC. Only access revocation (via group membership removal) is covered.

## Requirements

### R1 -- Ubiquitous: Org-Level Resource Scoping

The system shall scope all resource queries by `org_id`, ensuring no user can access resources belonging to another org.

### R2 -- Event-driven: Meeting Creation with Optional Group Scope

WHEN a user creates a meeting THEN the system shall record `zitadel_user_id` as owner and optionally set `group_id` if the user specifies a team scope.

### R3 -- State-driven: Group-Scoped Meeting Access (Read)

IF a meeting has `group_id` set THEN all members of that group shall have read access to the meeting, and only the owner and group admins shall have write/delete access.

### R4 -- State-driven: Personal Meeting Access

IF a meeting has `group_id = NULL` THEN only the owner (`zitadel_user_id`) shall have access to the meeting.

### R5 -- Unwanted Behavior: Invalid Group Scope Prevention

The system shall not allow a user to set `group_id` on a resource to a group they are not a member of.

### R6 -- Event-driven: Access Revocation on Group Removal

WHEN a user is removed from a group THEN the system shall revoke their access to all resources scoped to that group (no resource deletion, only access revocation via membership removal).

### R7 -- Event-driven: Audit Logging

WHEN any access control event occurs (resource creation, sharing, access revocation, group membership change) THEN the system shall write an immutable audit log entry with actor, action, resource_type, resource_id, and timestamp.

### R8 -- Ubiquitous: Scoped Query Helpers

The system shall provide scoped query helpers in `app/services/access.py` that automatically filter resources based on ownership and group membership.

### R9 -- State-driven: Group-Scoped Knowledge Base Access

IF a knowledge base has `kb_slug` matching pattern `group:{group_id}` THEN all members of that group shall have access to the knowledge base.

### R10 -- Optional: Visibility Selector in Frontend

Where possible, the system shall provide a visibility selector in the frontend when creating resources, allowing users to choose between "personal" and a specific group.

## Specifications

### Database Schema

**Column addition: `vexa_meetings.group_id`**

| Column | Type | Constraints |
|--------|------|-------------|
| `group_id` | `integer` (FK `portal_groups.id`) | NULLABLE, ON DELETE SET NULL |

- Index on `(group_id)` on `vexa_meetings`

**Table: `portal_audit_log`**

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `bigint` (PK) | Auto-increment |
| `org_id` | `integer` | NOT NULL |
| `actor_user_id` | `varchar(64)` | NOT NULL |
| `action` | `varchar(64)` | NOT NULL |
| `resource_type` | `varchar(32)` | NOT NULL |
| `resource_id` | `varchar(128)` | NOT NULL |
| `details` | `jsonb` | NULLABLE |
| `created_at` | `timestamptz` | DEFAULT `now()` |

- Index on `(org_id, created_at)` for paginated queries
- No UPDATE or DELETE permissions on this table (enforced at application level)

### Scoped Query Helpers

**`app/services/access.py`:**

```python
async def get_accessible_meetings(
    user_id: str,
    org_id: int,
    db: AsyncSession,
) -> list[VexaMeeting]:
    """Return meetings the user can access: owned + group-scoped."""
    # Get user's group IDs
    group_ids_subquery = (
        select(PortalGroupMembership.group_id)
        .where(PortalGroupMembership.zitadel_user_id == user_id)
        .scalar_subquery()
    )

    result = await db.execute(
        select(VexaMeeting).where(
            VexaMeeting.org_id == org_id,
            or_(
                VexaMeeting.zitadel_user_id == user_id,  # owned
                VexaMeeting.group_id.in_(group_ids_subquery),  # group-scoped
            ),
        )
    )
    return list(result.scalars().all())
```

### Audit Log Service

**`app/services/audit.py`:**

```python
async def log_event(
    db: AsyncSession,
    org_id: int,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: dict | None = None,
) -> None:
    """Write an immutable audit log entry."""
    entry = PortalAuditLog(
        org_id=org_id,
        actor_user_id=actor,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        details=details,
    )
    db.add(entry)
    await db.flush()
```

### Audit Log Actions

| Action | Resource Type | Trigger |
|--------|--------------|---------|
| `meeting.created` | `meeting` | New meeting with group scope |
| `meeting.shared` | `meeting` | group_id added to existing meeting |
| `meeting.unshared` | `meeting` | group_id removed from meeting |
| `group.member_added` | `group` | User added to group (AUTH-001) |
| `group.member_removed` | `group` | User removed from group (AUTH-001) |
| `product.assigned` | `product` | Product assigned to user (AUTH-002) |
| `product.revoked` | `product` | Product revoked from user (AUTH-002) |
| `user.suspended` | `user` | User suspended (AUTH-001) |
| `user.offboarded` | `user` | User offboarded (AUTH-001) |

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/audit-log` | Admin | Paginated audit log, filterable by action and resource_type |

### Qdrant Knowledge Scoping

- Group-scoped knowledge bases use `kb_slug = "group:{group_id}"`
- Modify knowledge queries to include group-scoped collections when user is a member
- Access check: query user's group memberships, build list of accessible `kb_slug` values
- No Qdrant schema migration needed -- scoping is at the query level

### Existing Endpoint Modifications

**Meetings list endpoint:**
- Replace direct query with `get_accessible_meetings()` from `access.py`
- Add `visibility` field to meeting response (personal / group name)

**Meeting creation endpoint:**
- Add optional `group_id` field to request body
- Validate group membership before setting `group_id`
- Log `meeting.created` audit event

### Frontend Changes

- Visibility selector on meeting creation: "Personal" or group name dropdown
- Meeting list: visibility indicator icon (lock for personal, group icon for shared)
- Admin audit log viewer with date range, action filter, and resource type filter

### Alembic Migrations

- `004_add_meeting_group_id.py` -- nullable `group_id` column on `vexa_meetings`
- `005_add_audit_log.py` -- `portal_audit_log` table

### MX Tag Strategy

| Function | Current Callers | Proposed Tag |
|----------|----------------|--------------|
| Meetings list endpoint | Frontend, API consumers | `@MX:ANCHOR` |
| `_qdrant_count()` in knowledge.py | 2 calls | `@MX:NOTE` |
| `get_accessible_meetings()` (new) | 3+ endpoints | `@MX:ANCHOR` |
| `log_event()` (new) | 10+ locations | `@MX:ANCHOR` |
| `_get_caller_org()` in admin.py | 8+ endpoints | `@MX:ANCHOR` |

## Traceability

| Requirement | Database | API | Service | Frontend | Migration |
|-------------|----------|-----|---------|----------|-----------|
| R1 | org_id WHERE clause | All endpoints | access.py | -- | -- |
| R2 | group_id column | Meeting create | -- | Visibility selector | 004 |
| R3 | group_id + memberships | Meeting list | get_accessible_meetings | Group badge | -- |
| R4 | group_id = NULL | Meeting list | get_accessible_meetings | Lock icon | -- |
| R5 | Membership validation | Meeting create/update | -- | Dropdown filtered | -- |
| R6 | Membership removal | Group member delete | -- | -- | -- |
| R7 | portal_audit_log | All mutating endpoints | log_event() | Audit viewer | 005 |
| R8 | -- | -- | access.py | -- | -- |
| R9 | kb_slug pattern | Knowledge queries | knowledge.py | -- | -- |
| R10 | -- | -- | -- | Visibility UI | -- |

## Cross-SPEC Dependencies

- **AUTH-001:** `portal_groups` and `portal_group_memberships` tables are the foundation for group-scoped access.
- **AUTH-001:** Group membership changes (AUTH-001 R3, R7) trigger audit log entries defined here.
- **AUTH-002:** Product assignment/revocation (AUTH-002 R2, R6) should log audit events defined here.
- **AUTH-002:** `PLAN_PRODUCTS` mapping determines which products gate knowledge base access.
- **Shared:** `_get_caller_org()` in `app/api/dependencies.py` is used across all three SPECs.
- **Shared:** `portal_audit_log` is the centralized audit table for AUTH-001, AUTH-002, and AUTH-003 operations.
