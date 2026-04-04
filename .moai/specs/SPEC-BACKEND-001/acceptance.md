# SPEC-BACKEND-001: Acceptance Criteria

## R1: Database Pool Configuration

**Given** portal-api starts with default configuration
**When** the SQLAlchemy engine is created
**Then** `pool_pre_ping=True`, `pool_size=10`, `max_overflow=20`, `pool_recycle=3600` are set
**And** the values are overridable via environment variables

## R3: Fix update_group Bug

**Given** a group with 2 assigned products
**When** the group name is updated via `PUT /api/groups/{id}`
**Then** the response contains the 2 products (not an empty list)

## R4: Auth Helper Consolidation

**Given** all endpoints using org context
**When** any endpoint calls `_get_caller_org()`
**Then** it uses the single implementation from `dependencies.py`
**And** no other file defines its own version
**And** all existing tests pass without modification

## R6: Database-Level Pagination

**Given** 50 groups exist for an org
**When** `GET /api/groups?limit=10&offset=20` is called
**Then** exactly 10 groups are returned starting from the 21st
**And** the SQL query contains LIMIT/OFFSET (not Python slicing)

## R2: Zitadel Userinfo Caching

**Given** two requests with the same token within 60 seconds
**When** both requests call `_get_caller_org()`
**Then** only one HTTP call is made to Zitadel userinfo
**And** the cached result is used for the second request

## Quality Gates

- All 260+ existing tests pass
- ruff check: 0 errors
- pyright: 0 errors
- No new `any` types introduced
