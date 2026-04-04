# Research: SPEC-REFACTOR-001

## Frontend: $kbSlug.tsx

### File location
`/Users/mark/Server/projects/klai/klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` (1,863 lines)

### Current route structure
- Registered via `createFileRoute('/app/knowledge/$kbSlug')` with route validation and ProductGuard wrapper
- Uses `KnowledgeDetailPage` as the main component
- Search parameters: `tab?: KBTab` (validates against VALID_TABS set) and `edit?: string`
- No child routes — all tab content rendered conditionally via `activeTab` state within single component
- Route file also imports related modal components (`DeleteKbModal`)

### Tab inventory
Six conditional tabs, some gated by permissions. Line ranges and state patterns:

| Tab | Name | Lines | Data dependencies | State | Permission gate |
|-----|------|-------|-------------------|-------|-----------------|
| overview | Dashboard/Stats | ~1700-1850 | KB data, stats, members (role calc), pending proposals count | `deleteModalOpen` (local) | None (owner-only delete modal) |
| items | Personal items | ~1810-1820 | Personal items list (personal KB only) | `deletingId` (local) | `isPersonal === true` |
| connectors | Connectors CRUD | ~170-593 | Connector list, supports web_crawler + github configs | `editingId, confirmingDeleteId, syncingIds, editName, editSchedule, editWebcrawlerConfig, editGithubConfig, editAllowedAssertionModes, editPreviewResult` (local) | `isOwner` |
| members | User/Group invite | ~697-1008 | Members list (users + groups), invite flows | Multiple invite/role-management states | `isOwner` |
| taxonomy | Graph editor | ~1205-1531 | Taxonomy nodes + proposals tree | `showAddRoot, addParentId, newNodeName, rejectingProposalId, rejectReason` (local) | `canEdit` (for mutations), `canDelete` (for deletes), proposal count badge |
| settings | Dangerous zone | ~1831-1857 | (none - just deletion) | `deleteModalOpen` (local) | `isOwner` |

**State shared across tabs:**
- `activeTab` (KBTab from search params)
- `kb` (KnowledgeBase query result — used for header, visibility, owner_type check)
- `stats` (KBStats — volume, doc count, connector summaries)
- `members` (MembersResponse — used to calculate isOwner, isContributor, isPersonal)
- `pendingCount` (taxonomy proposal count for badge)

**Tab-local state:**
Each tab maintains its own local React state for editing, confirmation, loading states. No cross-tab state synchronization beyond the shared queries.

### Shared state analysis

**Shared (used by >1 tab or page chrome):**
1. `kb` — fetched once, used for:
   - Page header (name, description, visibility icon)
   - Owner type check (personal vs org) — controls items tab visibility
   - Used by all tabs to build URLs (kbSlug)

2. `stats` — fetched once, used for:
   - Overview tab (docs count, connector list, volume breakdown)
   - Connector list summary (embedded in connectors tab)

3. `members` — fetched once, used for:
   - Role calculation (isOwner, isContributor)
   - Members tab (full member list)
   - Tab visibility gates (ownership checks for settings/connectors)

4. `pendingCount` — proposal count for taxonomy tab badge

**Tab-local state should NOT be shared:**
- ConnectorsSection: `editingId, confirmingDeleteId, syncingIds, editName, editSchedule, editWebcrawlerConfig, editGithubConfig, editAllowedAssertionModes, editPreviewResult` — all specific to editing a single connector
- ItemsSection: `deletingId` — only used within items table
- MembersSection: `showInviteUser, showInviteGroup, inviteEmail, inviteGroupId, inviteRole, inviteLanguage, confirmingRemoveUser, confirmingRemoveGroup, editingUserId, editFirstName, editLastName, editLanguage, roleChangeUserId, roleChangeRole, suspendingUserId, reactivatingUserId, offboardingUserId` — all user/group invite/edit flows
- TaxonomySection: `showAddRoot, addParentId, newNodeName, rejectingProposalId, rejectReason` — node creation/rejection
- Overview: `deleteModalOpen` — delete confirmation

### TanStack Router integration
- Route uses `Route.useParams()` to extract `kbSlug` parameter
- Route uses `Route.useSearch()` to get tab from query string and validate via `validateSearch`
- Route uses `navigate({ from: '/app/knowledge/$kbSlug', search: { tab: id } })` to switch tabs programmatically (button click handlers)
- No layout route parent, no children — this is a leaf route with internal tab switching
- The `$kbSlug` parameter determines which knowledge base is loaded; all data fetches include this in the API URL

**How outlet/children would work if refactored:**
If tabs were split into separate child routes (`/app/knowledge/$kbSlug/overview`, `/app/knowledge/$kbSlug/connectors`, etc.), the layout could be at `$kbSlug.tsx` with an `<Outlet />` rendering child pages. The layout would keep `kb`, `stats`, `members` queries and the tab bar chrome; each child would have a single focused component.

### Import map (key shared imports)
```tsx
// Router & data fetching
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'

// UI & state
import { useState, useRef, useEffect } from 'react'

// Markdown rendering
import ReactMarkdown from 'react-markdown'

// Icons (lucide-react)
import { Brain, FileText, Globe, Lock, RefreshCw, Trash2, Loader2, Plus, Pencil,
  BookOpen, Users, BarChart2, Zap, List, FolderTree, ChevronRight, ChevronDown,
  Check, X, Settings, AlertTriangle, ArrowLeft, Database, Search, GitBranch } from 'lucide-react'

// Components (from @/components/ui/)
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect, type MultiSelectOption } from '@/components/ui/multi-select'
import { Select } from '@/components/ui/select'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog'
import { Tooltip } from '@/components/ui/tooltip'
import { DeleteConfirmButton } from '@/components/ui/delete-confirm-button'
import { DeleteKbModal } from '@/components/ui/delete-kb-modal'

// i18n & utilities
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { queryLogger, taxonomyLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { STORAGE_KEYS } from '@/lib/storage'

// Auth
import { useAuth } from 'react-oidc-context'
```

### Risk analysis
1. **Size & complexity** — 1,863 lines in a single component makes refactoring hazardous. Any state reorganization risks breaking the tab-switching logic or data synchronization. Initial risk: HIGH. Mitigation: Extract to sub-components/tabs progressively, test each tab independently.

2. **Tab synchronization** — Each tab component receives `kbSlug` and `token` as props but implements its own queries. If a tab's query is stale, manually invalidating via `queryClient` across tabs may miss one. Risk: MEDIUM. Mitigation: Centralize query invalidation logic in the parent; have tabs call a callback to trigger re-fetch.

3. **State lifting concerns** — Tab-local states like `editingId` and `confirmingDeleteId` are not shared, so extracting them into sub-component files should be safe. The challenge is ensuring the tab-switch navigation doesn't leave UI in an inconsistent state (e.g. editing a connector, switch to members, switch back to connectors — is editing still open?). Risk: LOW (state is reset per tab load). Mitigation: Document that switching tabs resets all local UI state.

4. **Data fetching dependencies** — Stats and members queries depend on `kb` being loaded first (`enabled: !!token && !!kb`). If `kb` fails, the cascade fails silently. Risk: LOW (currently handled gracefully with error state). Mitigation: No change needed; error state already covers this.

5. **Connector config parsing** — WebCrawler and GitHub connector configs are typed inline (not as strict types). Editing them involves casting from `Record<string, unknown>`. Risk: MEDIUM (typo in field name breaks silently). Mitigation: Export strict TypeScript interfaces for both config types and use them in the edit form.

---

## Backend: provisioning.py

### File location
`/Users/mark/Server/projects/klai/klai-portal/backend/app/services/provisioning.py` (664 lines)

### Function inventory
Organized by responsibility layer: utilities, generation (pure), orchestration (side effects), rollback, and entry point.

#### Pure generation functions (no I/O, no side effects)
| Function | Lines | Responsibility | Side effects | Testability |
|----------|-------|-----------------|--------------|-------------|
| `_slugify_unique(name, existing_slugs)` | 134-149 | Generate unique slug from org name via normalization + collision avoidance | None | ✓ Fully testable (pure) |
| `_generate_librechat_yaml(base_path, mcp_servers)` | 185-240 | Merge MCP server catalog with base LibreChat config, return YAML string | Reads files from disk | ⚠ Testable but requires fixtures |
| `_generate_librechat_env(slug, client_id, client_secret, litellm_api_key, mongo_password, zitadel_org_id, mcp_servers)` | 242-356 | Generate per-tenant .env file content with all secrets (JWT, session, DB creds, etc.) | None | ✓ Fully testable |

#### Synchronous utility functions (run via executor, Docker interactions)
| Function | Lines | Responsibility | Side effects |
|----------|-------|-----------------|--------------|
| `_sync_remove_container(name)` | 48-56 | Remove Docker container by name (sync wrapper for run_in_executor) | Force-removes container |
| `_sync_drop_mongodb_tenant_user(slug)` | 58-82 | Drop MongoDB user for a tenant via mongosh in MongoDB container | Deletes MongoDB user |
| `_create_mongodb_tenant_user(slug, tenant_password)` | 151-183 | Create per-tenant MongoDB user with readWrite role on tenant's DB | Creates MongoDB user |
| `_write_tenant_caddyfile(slug)` | 410-443 | Write per-tenant Caddyfile to `/etc/caddy/tenants/{slug}.caddyfile` | Writes file to disk |
| `_reload_caddy()` | 445-460 | Reload Caddy config via `docker exec caddy reload` | Reloads reverse proxy |
| `_flush_redis_and_restart_librechat(slug)` | 358-408 | Flush Redis cache and restart LibreChat container | Flushes cache, restarts container |
| `_start_librechat_container(slug, env_file_host_path, mcp_servers)` | 462-508 | Start LibreChat Docker container with volumes and network config | Starts container |

#### Orchestration (async, calls multiple utilities)
| Function | Lines | Responsibility | Side effects | Entry point |
|----------|-------|-----------------|--------------|-------------|
| `_rollback(state)` | 84-132 | Best-effort cleanup of partial provisioning state (called on exception) | Removes/destroys resources | No (internal) |
| `_provision(org_id, db)` | 519-665 | 10-step provisioning sequence: Zitadel app, LiteLLM team, MongoDB user, .env, LibreChat container, Caddy, DB update, system groups | Creates 7+ external resources | No (internal) |
| `provision_tenant(org_id)` | 509-517 | Entry point: opens DB session and calls `_provision` | Indirect (via `_provision`) | ✓ Yes — called from `signup` endpoint |

### Procedure: 10-step _provision sequence
1. Fetch org from DB (get slug candidates)
2. Generate unique slug (lines 528)
3. **Zitadel**: Create OIDC app for LibreChat (line 536)
4. **LiteLLM**: Create team + generate API key (lines 542-569)
5. **Zitadel**: Add portal redirect URI (line 573)
6. **MongoDB**: Create tenant-isolated user (line 580)
7. **File system**: Write .env file (line 586)
8. **Docker**: Start LibreChat container (line 628)
9. **Caddy**: Write tenant Caddyfile + reload (line 634)
10. **Database**: Update PortalOrg row with provisioning results (line 646)
11. **System**: Create system groups (line 651)

Each step sets a flag in `_ProvisionState` for rollback tracking. On exception, `_rollback()` reverses steps in reverse order (LIFO).

### External service dependencies
- **Zitadel** (via `zitadel` service, authenticated with PAT)
  - `create_librechat_oidc_app(slug, redirect_uri)` → returns `{clientId, clientSecret, appId}`
  - `add_portal_redirect_uri(slug)` → adds redirect to portal login
  - `delete_librechat_oidc_app(app_id)` → rollback

- **LiteLLM** (HTTP client, `http://litellm:4000`)
  - `POST /team/new` → create team
  - `POST /key/generate` → create virtual API key with metadata
  - `POST /team/delete` → rollback

- **MongoDB** (via Docker `exec` on MongoDB container)
  - `mongosh` script to create user with DB-specific readWrite role

- **Docker** (via Docker SDK, `docker.from_env()`)
  - Container lifecycle: get, remove (stale), run (new)
  - Network operations: connect to multiple Docker networks
  - Exec operations: mongosh, redis-cli, caddy reload

- **Redis** (via Docker `exec` on Redis container)
  - `redis-cli FLUSHALL` — clears config cache before LibreChat restart

- **Caddy** (via Docker `exec` on Caddy container)
  - Write per-tenant site block file
  - `caddy reload` command

- **klai-docs API** (HTTP client, `http://docs-app:3000`)
  - `POST /api/orgs/{slug}/kbs` → create personal KB (optional, logs warning if fails)

- **PostgreSQL** (via SQLAlchemy, existing DB session)
  - Read org, write slug + container name + credentials to PortalOrg
  - Commit `provisioning_status = 'ready'`

### Entry points (who calls provision_tenant)
1. **`signup.py`** — After user creates new org in signup flow:
   ```python
   background_tasks.add_task(provision_tenant, org.id)  # fire-and-forget
   ```
   Entry point: `/api/signup` endpoint receives new user form, creates PortalOrg in DB, adds background task.

2. Manual triggering (not in current code, but possible via:
   - Direct call from API (not exposed)
   - Re-provisioning scenario (not yet implemented)

### Shared state or module-level variables
- `_caddy_lock = asyncio.Lock()` — global lock to prevent concurrent Caddy writes (lines 31-32, lines 633, 92, 635)
  - Used in `_rollback()` to prevent race between cleanup and new provisioning writing
  - Safe: correct usage with `async with _caddy_lock:`

- `logger = logging.getLogger(__name__)` — module-level logger for structured logs

No unsafe shared state (no global counters, no in-memory caches).

### Test coverage
**Test file:** `tests/test_provisioning.py` (confirmed to exist)

Tests should cover:
- ✓ Pure functions: `_slugify_unique()` (collision detection)
- ✓ `_generate_librechat_yaml()` (MCP server merging with catalog validation)
- ✓ `_generate_librechat_env()` (output format, secret generation)
- ⚠ Integration: Mock Zitadel, LiteLLM, MongoDB, Docker APIs; verify state transitions
- ⚠ Rollback: Trigger failures at each of the 11 steps, verify cleanup called in correct order
- ⚠ Idempotency: Re-running `_provision` with same org_id should fail gracefully (slug collision)

**Risk:** Rollback path is complex (8 cleanup paths with different exception handling). Without comprehensive rollback tests, a deployment bug could leave a tenant partially provisioned and unable to re-provision (locked).

### Risk analysis
1. **State tracking for rollback** — `_ProvisionState` dataclass tracks which steps succeeded. If a new step is added, the developer must remember to set the flag and add rollback logic. Risk: MEDIUM. Mitigation: Make rollback exhaustive (check all resources that could exist, attempt cleanup regardless of flag).

2. **External service failures** — 7 different services must succeed. If LiteLLM is temporarily unavailable, the entire signup flow fails (blocks user from creating org). No fallback or degraded mode. Risk: MEDIUM. Mitigation: Implement async retry with exponential backoff for HTTP calls (LiteLLM, klai-docs).

3. **File system paths** — Config file paths are constructed from `settings.librechat_container_data_path`, `settings.caddy_tenants_path`, etc. A typo in settings breaks provisioning silently. Risk: LOW (paths are validated at startup in dev). Mitigation: Test paths on container startup; log all file operations.

4. **MongoDB password generation** — Uses `secrets.token_hex(24)` which is cryptographically secure. However, if the password is ever logged, it breaks the security boundary. Risk: LOW. Mitigation: Ensure password is never included in log statements (use structured logging with separate field for "password_generated: true").

5. **Caddy reload timing** — `_reload_caddy()` restarts Caddy container if `admin off` is set (per platform pitfall). Timing window between "write Caddyfile" and "container restart" is where an old request could hit old config. Risk: LOW (acceptable 1s downtime per platform-caddy-admin-off-reload).

---

## Backend: admin.py

### File location
`/Users/mark/Server/projects/klai/klai-portal/backend/app/api/admin.py` (889 lines)

### Endpoint inventory

**Router setup:**
```python
router = APIRouter(prefix="/api/admin", tags=["admin"])
```

All endpoints are under `/api/admin/...` and require authentication via Bearer token + org membership.

**Shared infrastructure:**
- `_get_caller_org(credentials, db)` — Helper validates token, looks up PortalOrg + PortalUser
- `_require_admin(caller_user)` — Helper checks `role == "admin"` before allowing operation
- All endpoints use `Depends(bearer)` for Bearer token extraction

#### User management domain
| Endpoint | Method | Lines | Responsibility | Permission | Audit |
|----------|--------|-------|-----------------|-----------|-------|
| `/users` | GET | 181-223 | List org members (portal + live Zitadel identity) | Any org member | No |
| `/users/invite` | POST | 224-300 | Invite user to org, create Zitadel user, grant role | Admin only | ✓ Yes |
| `/users/{zitadel_user_id}` | PATCH | 301-340 | Update user profile (name, language) | Admin only | No |
| `/users/{zitadel_user_id}/role` | PATCH | 341-367 | Change user role (admin, group-admin, member) | Admin only | ✓ Yes |
| `/users/{zitadel_user_id}/resend-invite` | POST | 395-425 | Resend invitation email to pending user | Admin only | No |
| `/users/{zitadel_user_id}` | DELETE | 427-465 | Remove user from org, delete GitHub org membership | Admin only | ✓ Yes |
| `/users/{zitadel_user_id}/suspend` | POST | 730-767 | Suspend user account (soft delete) | Admin only | ✓ Yes |
| `/users/{zitadel_user_id}/reactivate` | POST | 768-797 | Reactivate suspended user | Admin only | ✓ Yes |
| `/users/{zitadel_user_id}/offboard` | POST | 798-850 | Full offboarding: revoke access, archive data, suspend | Admin only | ✓ Yes |

#### Product assignment domain
| Endpoint | Method | Lines | Responsibility | Permission | Notes |
|----------|--------|-------|-----------------|-----------|-------|
| `/products` | GET | 467-476 | List available products for this plan | Any member | No audit |
| `/users/{zitadel_user_id}/products` | POST | 478-537 | Assign product to user (direct grant) | Admin only | ✓ Audited |
| `/users/{zitadel_user_id}/products/{product}` | DELETE | 538-572 | Revoke product from user | Admin only | ✓ Audited |
| `/users/{zitadel_user_id}/products` | GET | 590-621 | Get user's directly-assigned products | Any member | No audit |
| `/users/{zitadel_user_id}/effective-products` | GET | 622-675 | Get user's effective products (direct + group-inherited) | Any member | No audit |
| `/products/summary` | GET | 573-589 | Product adoption summary (count per product) | Admin only | No audit |

#### Billing & organization settings domain
| Endpoint | Method | Lines | Responsibility | Permission | Notes |
|----------|--------|-------|-----------------|-----------|-------|
| `/settings` | GET | 368-377 | Get org settings (name, default language, MFA policy) | Any member | No audit |
| `/settings` | PATCH | 378-394 | Update org settings | Admin only | ✓ Audited |
| `/plan` | PATCH | 676-729 | Change org plan (billing tier change) | Admin only | ✓ Audited |

#### Audit & monitoring domain
| Endpoint | Method | Lines | Responsibility | Permission | Notes |
|----------|--------|-------|-----------------|-----------|-------|
| `/audit-log` | GET | 852-889 | Paginated audit log for this org | Admin only | Read-only |

### Domain grouping

**Clear separation of concerns:**

1. **User Lifecycle (8 endpoints)** — Lines 181-465 (invitation, profile, role, removal)
2. **Product Entitlements (6 endpoints)** — Lines 467-675 (assignment, revocation, listing, summary)
3. **Billing & Settings (3 endpoints)** — Lines 676-729 (org settings, plan changes)
4. **Audit & Compliance (1 endpoint)** — Line 852+ (audit log viewing)

**Potential refactoring boundaries:**
- Extract user lifecycle into `admin_users.py` (lines 181-465)
- Extract product assignment into `admin_products.py` (lines 467-675)
- Extract org settings & billing into `admin_org_settings.py` (lines 676-729)
- Keep audit log minimal in `admin_audit.py` or inline (lines 852+)

### Risk analysis

1. **Complex permission model** — Mix of admin-only, any-member, and implicit "is this org member" checks. No consistent guard pattern. Risk: MEDIUM. Mitigation: Every endpoint should start with:
   ```python
   _, org, caller_user = await _get_caller_org(credentials, db)
   if condition_requires_admin:
       _require_admin(caller_user)
   ```

2. **User lifecycle complexity** — 8 endpoints handling invite → active → suspend → reactivate → offboard state transitions. State machine is implicit, not explicit. Risk: HIGH. Mitigation: Document the state machine; add explicit state validation before transitions.

3. **Product assignment validation** — Endpoint accepts any product name from request body without validating against `PLAN_PRODUCTS`. Risk: LOW (Pydantic model uses literal type for product, but should be verified in code).

4. **Audit logging coverage** — Some mutations are audited (`/role`, `/products/*`, `/settings`, `/plan`), others are not (`/users/{id}` PATCH for profile). Inconsistent. Risk: LOW (non-critical updates missing audit). Mitigation: Audit all mutations, even profile updates.

5. **Auth token reuse** — Each endpoint calls `_get_caller_org()` which fetches token info from Zitadel. No caching. Risk: LOW (acceptable latency for typical UI patterns). Mitigation: If auth becomes a bottleneck, implement a short-TTL cache for userinfo.

---

## Existing patterns (reference implementations)

### Backend service extraction examples in codebase

**Pattern 1: Modular endpoint files**
- `app/api/` contains 20+ endpoint modules (`groups.py`, `knowledge_bases.py`, `connectors.py`, `taxonomy.py`, `mcp_servers.py`, etc.)
- Each file is a focused domain (groups management, KB operations, connectors)
- Each exports a `router: APIRouter` registered in `main.py`
- Size: typically 200-600 lines per file

**Pattern 2: Shared service layer**
- `app/services/` contains 30+ service modules (provisioning, zitadel, docs_client, access, audit, etc.)
- Services are imported by multiple endpoint files
- No circular dependencies
- Services are stateless (receive DB session, external clients as parameters)

**Example: docs_client.py**
```python
# Extracted HTTP client for klai-docs API
class DocsClient:
    async def create_kb(self, org_slug, kb_data) -> dict:
        # Implementation
```

Used by: `app_knowledge_bases.py`, `provisioning.py`, other endpoints.

**Pattern 3: Helper utilities**
- `app/core/` contains config, database setup, security helpers
- `app/core/plans.py` — Product tier definitions + helpers
- `app/core/database.py` — DB session management
- `app/core/config.py` — Pydantic settings

### Frontend sub-route examples in codebase

**Current patterns in `/routes/app/`:**
1. **Tab-based routes** — `/app/knowledge/$kbSlug.tsx` (this is ONE example, similar to KB detail)
2. **Nested page routes** — `/app/docs/**` (directory of nested pages)
3. **Modal routes** — `/app/knowledge/$kbSlug_.add-connector.tsx` (underscore = optional layout)

**Structure:**
```
/routes/app/
  /knowledge/
    index.tsx          — Knowledge base list
    new.tsx            — Create KB form
    $kbSlug.tsx        — Detail page (6 tabs, CURRENT GODCOMPONENT)
    $kbSlug_.add-connector.tsx — Modal for adding connector
  /docs/
    index.tsx          — Docs list
    [$slug]/page.tsx   — Doc viewer (nested)
  ...
```

**Reference: admin/users/** — Potential target for refactoring
```
/routes/admin/users/
  index.tsx          — Users list
  invite.tsx         — Invite form (separate route, not modal)
```

From `portal-patterns.md`: "add/edit forms are always separate route pages, never modals". The invite form at `/admin/users/invite` is the gold standard.

---

## Constraints & risks

### Breaking changes

1. **Frontend $kbSlug.tsx refactor**
   - If split into child routes, old URLs `?tab=connectors` must 307-redirect or be aliased
   - ProductGuard wrapper must move to layout route to avoid re-evaluating on child mount
   - Any external deep-links to `/app/knowledge/{slug}?tab=X` will 404 if route structure changes

2. **Backend admin.py split**
   - Clients calling `/api/admin/users` etc. will not break (same URLs)
   - Internal imports of admin.py (if any exist) will break (need to import from sub-modules)

3. **Backend provisioning.py extraction**
   - `provision_tenant(org_id)` entry point is in signup.py as `background_tasks.add_task(provision_tenant, org_id)`
   - If moved to a new module, import path changes but semantics remain
   - No API contracts (internal service only)

### Test coverage gaps

**Frontend:**
- `$kbSlug.tsx` has no unit tests (entire component untested)
- Tab switching logic (URL param → activeTab → correct component render) — not tested
- Query invalidation on mutations — not tested
- Recommended: Extract each tab to testable component, write snapshot tests

**Backend:**
- `provisioning.py`: test_provisioning.py exists but coverage is unclear (should test rollback extensively)
- `admin.py`: No admin endpoint tests found (should test permission gates, audit logging)
- `admin.py`: User lifecycle state transitions not tested (invite → active → suspend → offboard)

### Shared state complications

**Frontend:**
- Tab-local state should NOT leak across tabs (editing a connector in one tab should not affect members tab). Currently safe because each tab is self-contained.
- Query cache sharing via `useQueryClient()` is correct and should be preserved.
- If tabs are split into child routes, shared queries (kb, stats, members) must be fetched in the parent layout, not repeated in children.

**Backend:**
- `_provision` has a complex exception → rollback path. Rollback is best-effort and may leave partial state. This is acceptable but should be documented.
- `_ProvisionState` tracks rollback state; if a new step is added, developer must manually add rollback logic. Error-prone.

---

## Summary of refactoring opportunities

### Frontend: $kbSlug.tsx
- **Candidate for extraction:** Tab components (ConnectorsSection, ItemsSection, MembersSection, TaxonomySection, DashboardSection)
- **Keep in layout:** Shared queries (kb, stats, members), tab bar, header chrome, ProductGuard
- **Refactoring strategy:** Extract each tab to a sub-component file within a `_components/` folder, or split into child routes with shared layout
- **Complexity:** HIGH (test all tab interactions after split)
- **Timeline:** 4-6 sprints (discovery, extraction, testing per tab, integration testing)

### Backend: provisioning.py
- **Candidate for extraction:** Pure functions (_generate_librechat_yaml, _generate_librechat_env, _slugify_unique) → `provisioning/generators.py`
- **Candidate for extraction:** Docker/Caddy/MongoDB utilities → `provisioning/infrastructure.py`
- **Keep as orchestration:** `_provision`, `provision_tenant`, `_rollback`
- **Refactoring strategy:** Create submodules within `services/provisioning/`, keep public entry point stable
- **Complexity:** MEDIUM (pure functions are safe, orchestration is complex)
- **Timeline:** 2-3 sprints (extraction, test coverage, integration testing)

### Backend: admin.py
- **Candidate for extraction:** User management endpoints → `admin/users.py` (8 endpoints, ~285 lines)
- **Candidate for extraction:** Product assignment endpoints → `admin/products.py` (6 endpoints, ~210 lines)
- **Candidate for extraction:** Billing endpoints → `admin/billing.py` (3 endpoints, ~54 lines)
- **Keep as shared:** `_get_caller_org()`, `_require_admin()`, audit logging
- **Refactoring strategy:** Create sub-routers, mount them in a parent `admin_router`, maintain same URL structure
- **Complexity:** LOW (clear domain boundaries, no shared state beyond helpers)
- **Timeline:** 1-2 sprints (extraction, testing, integration)

---

