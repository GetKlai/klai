# SPEC-KB-012: Implementation Plan -- Taxonomy Management

| Field       | Value                                         |
|-------------|-----------------------------------------------|
| SPEC ID     | SPEC-KB-012                                   |
| Title       | Taxonomy Management for Knowledge Bases       |
| Created     | 2026-03-27                                    |

---

## 1. Module Decomposition

### M1 -- Database Layer

**Scope:** SQLAlchemy models and Alembic migration for taxonomy tables.

**Files to create:**
- `klai-portal/backend/app/models/taxonomy.py` -- `PortalTaxonomyNode` and `PortalTaxonomyProposal` models
- `klai-portal/backend/alembic/versions/{hash}_add_taxonomy_tables.py` -- Migration creating both tables with indexes and constraints

**Files to modify:**
- `klai-portal/backend/app/models/__init__.py` -- Register new models for Alembic auto-detection

**Dependencies:** None (first module to implement).

**Key decisions:**
- Adjacency list pattern for tree structure (`parent_id` FK to self). Materialized path is unnecessary given expected tree depth of 3-5 levels.
- `doc_count` is denormalized. Updated via trigger or application-level increment/decrement when documents are assigned/unassigned.
- `ON DELETE SET NULL` for `parent_id` allows orphaning children to root level; application code handles reassignment before deletion.
- UNIQUE constraint on `(kb_id, parent_id, name)` enforces sibling uniqueness. NULL `parent_id` requires special handling (PostgreSQL treats each NULL as distinct). Use a partial unique index or a sentinel value.

**Risk:** PostgreSQL UNIQUE constraint with NULL -- `(kb_id, NULL, 'name')` is not enforced the same way as `(kb_id, 5, 'name')`. Mitigation: Use `COALESCE(parent_id, 0)` in a unique expression index, or enforce in application code.

---

### M2 -- API Layer

**Scope:** FastAPI router for taxonomy CRUD and proposal review.

**Files to create:**
- `klai-portal/backend/app/api/taxonomy.py` -- Router with all taxonomy endpoints

**Files to modify:**
- `klai-portal/backend/app/main.py` -- Register `taxonomy.router` via `app.include_router()`

**Dependencies:** M1 (models must exist).

**Key patterns (matching existing codebase):**
- Router prefix: `APIRouter(prefix="/api/app/knowledge-bases", tags=["taxonomy"])`
- Auth: `Depends(bearer)` with `_get_caller_org` for org isolation
- Access control: Reuse `get_user_role_for_kb()` from `app/services/access.py` to check role (viewer/contributor/owner)
- Pydantic v2 schemas with `ConfigDict(from_attributes=True)`
- Async SQLAlchemy sessions via `get_db`
- Error responses: `HTTPException` with appropriate status codes

**Endpoint implementation notes:**

| Endpoint | Complexity | Notes |
|----------|-----------|-------|
| `GET /nodes` | Medium | Recursive tree building from flat query result. Load all nodes for a KB, build tree in Python. |
| `POST /nodes` | Low | Validate parent exists, check sibling uniqueness, insert. |
| `PATCH /nodes/{id}` | Medium | Support rename and/or reparent. Validate no circular references on reparent. |
| `DELETE /nodes/{id}` | High | Must reassign children and documents before deleting. Transaction-critical. |
| `GET /proposals` | Low | Filterable by status. Paginated. |
| `POST /proposals` | Low | Validate payload schema per proposal_type. Internal/system auth. |
| `POST /proposals/{id}/approve` | High | Type-specific execution: create node, merge, split, or rename. Transactional. |
| `POST /proposals/{id}/reject` | Low | Set status + reason. |

**Circular reference prevention:** When reparenting a node, walk up the ancestor chain of the proposed new parent. If the node being moved appears as an ancestor, reject with 409.

---

### M3 -- Frontend Layer

**Scope:** Taxonomy tab UI on the KB detail page.

**Files to create:**
- `klai-portal/frontend/src/routes/app/knowledge/_components/TaxonomySection.tsx` -- Main taxonomy section component
- `klai-portal/frontend/src/routes/app/knowledge/_components/TaxonomyTree.tsx` -- Recursive tree view component
- `klai-portal/frontend/src/routes/app/knowledge/_components/ProposalQueue.tsx` -- Proposal review table

**Files to modify:**
- `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx`:
  - Add `'taxonomy'` to `KBTab` type
  - Add taxonomy tab button with `FolderTree` icon
  - Add `<TaxonomySection>` render in tab content area
  - Add pending proposal count badge
- `klai-portal/frontend/src/lib/logger.ts` -- Add `taxonomyLogger` export

**Dependencies:** M2 (API must be available), M4 (i18n keys must exist).

**Component architecture:**
```
KnowledgeDetailPage
  +-- TaxonomySection (data fetching, layout)
       +-- TaxonomyTree (recursive node rendering)
       |     +-- TaxonomyNodeRow (single node with actions)
       +-- ProposalQueue (table of pending proposals)
             +-- ProposalRow (single proposal with approve/reject)
```

**UI patterns (matching existing codebase):**
- Data fetching: `useQuery` inline in TaxonomySection with `queryKey: ['kb-taxonomy', kbSlug]`
- Mutations: `useMutation` for create/rename/delete nodes, approve/reject proposals
- Tables: Card > CardContent with `pt-0 px-0 pb-0 overflow-hidden rounded-xl` > table
- Row alternation: `i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'`
- Action buttons: raw `<button>` for icon actions in tables
- Delete confirmation: Inline tier (Trash2 icon -> "Verwijderen" + "Annuleren" text buttons)
- Color tokens: `--color-purple-deep` for headings, `--color-destructive` for delete, `--color-success` for approve, `--color-muted-foreground` for secondary text

**Tree indentation:** Each level indented with `pl-{depth * 4}` (Tailwind padding-left). Expand/collapse via `ChevronRight`/`ChevronDown` icons.

**Add node form:** Inline card (same pattern as member invite in ConnectorsSection), not a route page. Single field (name) with optional parent selector.

---

### M4 -- Internationalization

**Scope:** Paraglide i18n keys for all taxonomy UI strings.

**Files to modify:**
- `klai-portal/frontend/messages/en.json` -- Add English keys
- `klai-portal/frontend/messages/nl.json` -- Add Dutch keys

**Dependencies:** None (can be done in parallel with M3).

**Key prefix:** `knowledge_taxonomy_`

**Required keys (estimated 25-30):**

```
knowledge_taxonomy_tab_label
knowledge_taxonomy_tab_badge_pending
knowledge_taxonomy_tree_heading
knowledge_taxonomy_tree_empty
knowledge_taxonomy_tree_empty_hint
knowledge_taxonomy_node_add
knowledge_taxonomy_node_add_name_label
knowledge_taxonomy_node_add_parent_label
knowledge_taxonomy_node_add_submit
knowledge_taxonomy_node_rename
knowledge_taxonomy_node_delete
knowledge_taxonomy_node_delete_confirm
knowledge_taxonomy_node_delete_cancel
knowledge_taxonomy_node_doc_count
knowledge_taxonomy_proposals_heading
knowledge_taxonomy_proposals_empty
knowledge_taxonomy_proposals_col_type
knowledge_taxonomy_proposals_col_title
knowledge_taxonomy_proposals_col_confidence
knowledge_taxonomy_proposals_col_created
knowledge_taxonomy_proposals_col_actions
knowledge_taxonomy_proposals_approve
knowledge_taxonomy_proposals_reject
knowledge_taxonomy_proposals_reject_reason_label
knowledge_taxonomy_proposals_status_pending
knowledge_taxonomy_proposals_status_approved
knowledge_taxonomy_proposals_status_rejected
knowledge_taxonomy_error_fetch
knowledge_taxonomy_error_create
knowledge_taxonomy_error_delete
```

---

## 2. Implementation Milestones (Priority-Ordered)

### Primary Goal: Database + API Foundation

**Modules:** M1, M2

**Deliverables:**
- Two new PostgreSQL tables with proper indexes and constraints
- Alembic migration (up + down)
- 8 API endpoints with full access control
- Pydantic request/response schemas
- Proposal approval logic for all 4 types (new_node, merge, split, rename)

**Verification:** All API endpoints testable via `curl` or httpx. Tree building returns correct nested structure. Proposal approve/reject executes correctly.

**Why first:** Frontend cannot function without API. Database is prerequisite for everything.

---

### Secondary Goal: Frontend Taxonomy Tab

**Modules:** M3, M4

**Deliverables:**
- Taxonomy tab visible on KB detail page
- Tree view with expand/collapse, doc counts, inline actions
- Proposal review queue with approve/reject
- All UI strings in EN + NL
- Read-only mode for viewers
- Full CRUD for owners/contributors

**Verification:** Tab loads and displays tree. Proposals can be approved/rejected. Badge shows pending count.

**Why second:** Requires API to be functional. UI is the primary user touchpoint.

---

### Final Goal: Integration + Polish

**Deliverables:**
- knowledge-ingest service integration (proposal submission endpoint tested end-to-end)
- Qdrant payload update on document-to-node assignment
- Empty state with guidance text
- Confidence score visualization in proposal queue
- `taxonomyLogger` integration with Sentry via consola

**Verification:** Full flow from BERTopic proposal -> review -> approval -> tree update -> Qdrant metadata update.

**Why last:** Integration depends on both API and UI being stable. Polish items are lower priority.

---

## 3. Technical Approach

### 3.1 Tree Data Structure

**Storage:** Adjacency list in PostgreSQL (`parent_id` FK to self).

**Query strategy:** Load all nodes for a KB in a single query (`SELECT * FROM portal_taxonomy_nodes WHERE kb_id = ?`), then build the tree in Python. Expected max node count per KB: ~100-500. No performance concern with in-memory tree building.

**Alternative considered:** Materialized path (`path = '/1/5/12/'`). Rejected because:
- Adjacency list is simpler for CRUD operations
- Tree depth is shallow (3-5 levels)
- No need for subtree queries beyond what adjacency list provides
- Reparenting with materialized path requires updating all descendants

### 3.2 Proposal Execution Strategy

Each proposal type has a distinct approval handler:

| Type | Handler Logic |
|------|--------------|
| `new_node` | Create `PortalTaxonomyNode` from payload. Validate parent exists. |
| `merge` | Move all documents from source to target node. Update `doc_count` on both. Delete source node. Single transaction. |
| `split` | Create new child nodes under source's parent. Source node remains. Document reassignment is manual follow-up. |
| `rename` | Update node `name` and regenerate `slug`. Record old name in audit. |

### 3.3 Access Control Matrix

| Action | viewer | contributor | owner |
|--------|--------|-------------|-------|
| View taxonomy tree | Yes | Yes | Yes |
| View proposals | Yes | Yes | Yes |
| Create node | No | Yes | Yes |
| Rename node | No | Yes | Yes |
| Delete node | No | No | Yes |
| Approve proposal | No | Yes | Yes |
| Reject proposal | No | Yes | Yes |

### 3.4 Frontend State Management

- Tree data: `useQuery` with key `['kb-taxonomy-nodes', kbSlug]`
- Proposals: `useQuery` with key `['kb-taxonomy-proposals', kbSlug, statusFilter]`
- Expand/collapse: Local `useState<Set<number>>` for expanded node IDs
- Inline editing: Local state per node row (rename mode toggle)
- Optimistic updates: Invalidate queries after successful mutations

---

## 4. Risks and Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Sibling uniqueness with NULL parent_id in PostgreSQL | High | Medium | Use expression index: `CREATE UNIQUE INDEX ... ON portal_taxonomy_nodes (kb_id, COALESCE(parent_id, 0), name)` |
| R2 | Circular reference on reparent | Medium | High | Walk ancestor chain in Python before committing reparent. O(depth) check, max ~5 levels. |
| R3 | Race condition on concurrent proposal approval | Low | Medium | Database-level row locking (`SELECT ... FOR UPDATE`) on proposal row during approval. |
| R4 | `doc_count` drift from denormalization | Medium | Low | Periodic reconciliation job. Or compute on-read if performance allows. |
| R5 | Tab overflow on small screens with 5 tabs | Low | Low | Existing tab layout uses flex-wrap. Test on 320px viewport. If needed, add horizontal scroll. |
| R6 | knowledge-ingest service unavailable | Medium | Low | Proposals are additive. Taxonomy works fully without AI proposals -- manual curation is always available. |

---

## 5. File Impact Summary

### New Files (7)

| File | Module |
|------|--------|
| `klai-portal/backend/app/models/taxonomy.py` | M1 |
| `klai-portal/backend/alembic/versions/{hash}_add_taxonomy_tables.py` | M1 |
| `klai-portal/backend/app/api/taxonomy.py` | M2 |
| `klai-portal/frontend/src/routes/app/knowledge/_components/TaxonomySection.tsx` | M3 |
| `klai-portal/frontend/src/routes/app/knowledge/_components/TaxonomyTree.tsx` | M3 |
| `klai-portal/frontend/src/routes/app/knowledge/_components/ProposalQueue.tsx` | M3 |

### Modified Files (5)

| File | Module | Change |
|------|--------|--------|
| `klai-portal/backend/app/models/__init__.py` | M1 | Import new models |
| `klai-portal/backend/app/main.py` | M2 | Register taxonomy router |
| `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` | M3 | Add taxonomy tab + section |
| `klai-portal/frontend/src/lib/logger.ts` | M3 | Add `taxonomyLogger` |
| `klai-portal/frontend/messages/en.json` | M4 | Add ~30 taxonomy keys |
| `klai-portal/frontend/messages/nl.json` | M4 | Add ~30 taxonomy keys (translated) |

---

## 6. Expert Consultation Recommendations

### Backend Expert (expert-backend)

Recommended for:
- Database schema review (adjacency list vs. materialized path trade-off)
- Proposal approval transaction design (merge operation atomicity)
- Access control integration with existing `get_user_role_for_kb()` service
- Circular reference prevention algorithm

### Frontend Expert (expert-frontend)

Recommended for:
- Recursive tree component design (performance with large trees)
- Expand/collapse state management
- Inline editing UX pattern (rename in-place)
- Badge count integration with existing tab header

---

## 7. Next Steps

After SPEC approval:
1. Run `/moai:2-run SPEC-KB-012` to begin implementation
2. Start with M1 (database) + M2 (API) as Primary Goal
3. Follow with M3 (frontend) + M4 (i18n) as Secondary Goal
4. Integration testing as Final Goal
