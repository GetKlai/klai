---
id: SPEC-AUTH-001
version: "1.0.0"
status: completed
created: "2026-03-24"
updated: "2026-03-24"
author: MoAI
priority: high
issue_number: 19
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-03-24 | MoAI | Initial draft |

# SPEC-AUTH-001: User Groups & Lifecycle

## Overview

This specification defines the group management and user lifecycle system for the Klai portal. Groups provide organizational structure within an org, enabling team-based access control for resources (AUTH-003) and scoped product management (AUTH-002). User lifecycle management covers suspension, reactivation, and offboarding with full cascade cleanup.

## Environment

- **Runtime:** FastAPI backend (Python 3.13+, async)
- **Database:** PostgreSQL via SQLAlchemy 2.0 async, Alembic migrations
- **Auth provider:** Zitadel (external IdP, user deactivation API)
- **Frontend:** React/Next.js portal admin interface
- **Existing tables:** `portal_orgs`, `portal_users` (with `zitadel_user_id`)
- **Existing dependencies:** `_get_caller_org()`, `_require_admin()`, `get_current_user_id()` in `app/api/admin.py` and `app/api/auth.py`

## Assumptions

- A1: Every portal user belongs to exactly one org (enforced by existing `portal_users.org_id`).
- A2: Zitadel provides a `deactivate_user(user_id)` API that is idempotent and returns success even if the user is already deactivated.
- A3: Group names are human-readable labels, not machine identifiers. Display is case-preserving; uniqueness check is case-insensitive within an org.
- A4: Offboarding is a destructive, non-reversible operation. Suspension is reversible.
- A5: The existing `_get_caller_org()` dependency reliably returns the caller's org from the JWT. This function will be extracted to `app/api/dependencies.py` as shared infrastructure.

## Requirements

### R1 -- Ubiquitous: Group-Org Integrity

The system shall enforce that every group belongs to exactly one org, and group names shall be unique within that org.

### R2 -- Event-driven: Group Creation

WHEN an admin creates a group THEN the system shall persist a `portal_groups` record with the `org_id` of the admin's organization and record the admin's `user_id` as `created_by`.

### R3 -- Event-driven: Group Membership Addition

WHEN an admin adds a user to a group THEN the system shall verify that both the user and the group belong to the same org before creating the membership record.

### R4 -- State-driven: Group Admin Privileges

IF a user has `is_group_admin = true` for a group THEN that user shall be able to add and remove members from that group without requiring org-level admin role.

### R5 -- Unwanted Behavior: Cross-Org Membership Prevention

The system shall not allow a user to be added to a group in a different org than their own, even if the `group_id` is known.

### R6 -- Event-driven: User Suspension

WHEN an admin suspends a user THEN the system shall set the user's status to `suspended`, preserving all group memberships but preventing the user from authenticating via Zitadel.

### R7 -- Event-driven: User Offboarding

WHEN an admin offboards a user THEN the system shall remove all group memberships, revoke product assignments (AUTH-002), deactivate the user in Zitadel, and mark the `portal_user` as `offboarded`.

### R8 -- Optional: Bulk Import

Where possible, the system shall provide a bulk import endpoint for group memberships to support initial onboarding of teams.

## Specifications

### Database Schema

**Table: `portal_groups`**

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `integer` (PK) | Auto-increment |
| `org_id` | `integer` (FK `portal_orgs.id`) | NOT NULL |
| `name` | `varchar(128)` | NOT NULL |
| `description` | `text` | NULLABLE |
| `created_at` | `timestamptz` | DEFAULT `now()` |
| `created_by` | `varchar(64)` | NOT NULL (Zitadel user ID) |

- UNIQUE constraint on `(org_id, LOWER(name))`
- Index on `(org_id)`

**Table: `portal_group_memberships`**

| Column | Type | Constraints |
|--------|------|-------------|
| `id` | `integer` (PK) | Auto-increment |
| `group_id` | `integer` (FK `portal_groups.id`) | NOT NULL, ON DELETE CASCADE |
| `zitadel_user_id` | `varchar(64)` | NOT NULL |
| `is_group_admin` | `boolean` | DEFAULT `false` |
| `joined_at` | `timestamptz` | DEFAULT `now()` |

- UNIQUE constraint on `(group_id, zitadel_user_id)`

**Column addition: `portal_users.status`**

| Column | Type | Constraints |
|--------|------|-------------|
| `status` | `varchar(16)` | DEFAULT `'active'`, CHECK IN (`active`, `suspended`, `offboarded`) |

### API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/api/admin/groups` | Admin | List all groups in caller's org |
| `POST` | `/api/admin/groups` | Admin | Create a group |
| `PATCH` | `/api/admin/groups/{id}` | Admin | Update group name/description |
| `DELETE` | `/api/admin/groups/{id}` | Admin | Delete a group (cascades memberships) |
| `GET` | `/api/admin/groups/{id}/members` | Admin or Group Admin | List group members |
| `POST` | `/api/admin/groups/{id}/members` | Admin or Group Admin | Add member to group |
| `DELETE` | `/api/admin/groups/{id}/members/{user_id}` | Admin or Group Admin | Remove member from group |
| `PATCH` | `/api/admin/groups/{id}/members/{user_id}` | Admin | Toggle `is_group_admin` |
| `POST` | `/api/admin/users/{id}/suspend` | Admin | Suspend a user |
| `POST` | `/api/admin/users/{id}/reactivate` | Admin | Reactivate a suspended user |
| `POST` | `/api/admin/users/{id}/offboard` | Admin | Offboard a user (destructive) |

### Shared Infrastructure

- Extract `_get_caller_org()` to `app/api/dependencies.py`
- New dependency: `_require_admin_or_group_admin(group_id, caller_user)` -- checks org-level admin OR `is_group_admin` for the specific group.

### Alembic Migrations

- `001_add_portal_groups.py` -- `portal_groups` + `portal_group_memberships` tables
- `002_add_user_status.py` -- `status` column on `portal_users` (default `'active'`, backfill existing rows)

### MX Tag Strategy

| Function | Current Callers | Proposed Tag |
|----------|----------------|--------------|
| `_get_caller_org()` in admin.py | 8 endpoints | `@MX:ANCHOR` |
| `_require_admin()` in admin.py | 8 endpoints | `@MX:ANCHOR` |
| `zitadel.deactivate_user()` | 0 (new) | `@MX:WARN` |
| `get_current_user_id()` in auth.py | 6 endpoints | `@MX:ANCHOR` |

## Traceability

| Requirement | Database | API | Frontend | Migration |
|-------------|----------|-----|----------|-----------|
| R1 | UNIQUE(org_id, name) | Validation in POST/PATCH | -- | 001 |
| R2 | portal_groups | POST /groups | Create group form | 001 |
| R3 | portal_group_memberships | POST /groups/{id}/members | Add member UI | 001 |
| R4 | is_group_admin column | _require_admin_or_group_admin | Group admin badge | 001 |
| R5 | FK + validation | org_id check in membership | -- | 001 |
| R6 | portal_users.status | POST /users/{id}/suspend | Suspend button | 002 |
| R7 | CASCADE + cleanup | POST /users/{id}/offboard | Offboard confirmation | 001, 002 |
| R8 | Batch insert | POST /groups/{id}/members/bulk | Bulk import UI | 001 |

## Cross-SPEC Dependencies

- **AUTH-002:** Offboarding (R7) must revoke product assignments defined in SPEC-AUTH-002.
- **AUTH-003:** Groups defined here are referenced by AUTH-003 for resource scoping (`group_id` on meetings).
- **Shared:** `portal_users.status` is used by AUTH-002 for seat counting (only `status='active'` users count).
- **Shared:** `_get_caller_org()` extraction to `dependencies.py` benefits all three SPECs.
