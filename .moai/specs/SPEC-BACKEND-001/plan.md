# SPEC-BACKEND-001: Implementation Plan

## Task Decomposition

### Phase 1: Critical (can be done independently)

| Task | Files | Effort |
|------|-------|--------|
| R1: DB pool config | `database.py` | 5 min |
| R3: Fix products=[] bug | `groups.py` | 15 min |
| R5: Pin LibreChat image | `config.py` | 5 min |
| R10: Password min length | `signup.py` | 5 min |

### Phase 2: Medium complexity

| Task | Files | Effort |
|------|-------|--------|
| R7: Async catalog loading | `mcp_servers.py` | 15 min |
| R8: Logging context reorder | `logging_context.py` | 20 min |
| R9: Sanitize billing errors | `billing.py` | 10 min |

### Phase 3: Larger refactors

| Task | Files | Effort |
|------|-------|--------|
| R4: Auth helper consolidation | 5 files | 45 min |
| R6: DB-level pagination | 3 endpoints + tests | 45 min |
| R2: Zitadel userinfo caching | `dependencies.py`, `auth.py` | 30 min |

## Dependencies

- R4 (auth consolidation) should be done before R2 (caching), since caching will be added to the consolidated helper
- R6 (pagination) requires adding tests for the new parameters

## Risk Analysis

- **R4 (auth consolidation):** High blast radius — touches auth flow for all endpoints. Must verify all endpoints still work after consolidation.
- **R2 (Zitadel caching):** Cache invalidation risk — stale userinfo could cause permission issues. 60s TTL is conservative.
- **R8 (logging context):** Middleware ordering matters — must test that the context is available in route handlers.
