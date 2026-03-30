# SPEC-KB-012: Taxonomy Management for Knowledge Bases

| Field       | Value                                         |
|-------------|-----------------------------------------------|
| SPEC ID     | SPEC-KB-012                                   |
| Title       | Taxonomy Management for Knowledge Bases       |
| Created     | 2026-03-27                                    |
| Status      | Completed                                     |
| Priority    | High                                          |
| Lifecycle   | spec-anchored                                 |
| Domain      | Knowledge Base                                |
| Depends On  | SPEC-KB-001 (unification), SPEC-KB-004 (schema) |

---

## 1. Environment

### 1.1 System Context

Klai is a privacy-first, EU-only AI platform. The portal (`klai-portal/`) provides a knowledge base management interface where organizations create, populate, and query knowledge bases. Documents are ingested via connectors (GitHub, web crawlers, etc.), embedded into Qdrant, and served through a retrieval API.

### 1.2 Current Architecture

- **Frontend:** React 19 + Vite + TanStack Router. KB detail page at `klai-portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` with tab system (`overview | connectors | members | items`).
- **Backend:** FastAPI + SQLAlchemy (async) + Alembic. API routers in `klai-portal/backend/app/api/`, models in `klai-portal/backend/app/models/`.
- **Database:** PostgreSQL with org-scoped isolation. KB model `portal_knowledge_bases` with access control via `portal_user_kb_access` and `portal_group_kb_access`.
- **Auth:** Zitadel OIDC. Token via `auth.user?.access_token`, org isolation via subdomain.
- **AI Pipeline:** BERTopic runs externally (knowledge-ingest service) for document classification and taxonomy proposal generation.

### 1.3 Integration Points

- **Qdrant:** Document vectors carry metadata payloads including `org_id`, `kb_slug`. Taxonomy node assignment will be written to document payloads as `taxonomy_node_id` (metadata-only update, no re-embedding required).
- **knowledge-ingest service:** Runs BERTopic topic modeling. Produces taxonomy proposals that are posted to the portal API for human review.
- **Portal access control:** Taxonomy management is restricted to KB owners and contributors. Viewers can see the taxonomy tree but cannot modify it.

---

## 2. Assumptions

| # | Assumption | Confidence | Risk if Wrong |
|---|-----------|------------|---------------|
| A1 | BERTopic runs in the separate `knowledge-ingest` service and posts proposals to the portal API via internal HTTP | High | Need to refactor proposal creation to be synchronous within portal |
| A2 | Taxonomy nodes form a strict tree (single parent per node), not a DAG | High | DAG support requires a different data model (adjacency list vs. materialized path) |
| A3 | Document-to-node assignment is 1:1 (a document belongs to exactly one taxonomy node) | Medium | Many-to-many requires a junction table instead of a FK on document metadata |
| A4 | The existing `$kbSlug.tsx` tab component can accommodate a 5th tab without layout breakage | High | May need horizontal scrolling or tab overflow menu |
| A5 | Taxonomy proposals are generated asynchronously and the review queue is eventually consistent | High | Synchronous generation would block the UI |
| A6 | Qdrant payload updates (setting `taxonomy_node_id`) do not require re-embedding | High | Re-embedding on taxonomy change would be prohibitively expensive |

---

## 3. Requirements

### 3.1 Ubiquitous Requirements (shall -- always active)

**REQ-U1:** The system shall enforce org-scoped isolation on all taxonomy data. Every `taxonomy_node` and `taxonomy_proposal` row shall belong to a specific `kb_id`, and the `kb_id` shall belong to the caller's org.

**REQ-U2:** The system shall maintain referential integrity between taxonomy nodes and their parent nodes. Deleting a parent node shall cascade to reassign or orphan child nodes (configurable behavior).

**REQ-U3:** The system shall validate that taxonomy node names are unique within the same parent scope (siblings cannot share a name).

**REQ-U4:** The system shall record an audit trail for all taxonomy mutations (node create, rename, delete, merge, split, proposal approve/reject) including the actor's `zitadel_user_id` and timestamp.

### 3.2 Event-Driven Requirements (when...shall)

**REQ-E1:** When the knowledge-ingest service submits a taxonomy proposal via `POST /api/app/knowledge-bases/{kb_slug}/taxonomy/proposals`, the system shall create a proposal record with status `pending` and notify the review queue.

**REQ-E2:** When a reviewer approves a `new_node` proposal, the system shall create the corresponding taxonomy node in the tree and update the proposal status to `approved`.

**REQ-E3:** When a reviewer approves a `merge` proposal, the system shall merge the source node into the target node by reassigning all documents from the source node to the target node, then delete the source node.

**REQ-E4:** When a reviewer approves a `split` proposal, the system shall create the new child nodes under the specified parent and leave document reassignment to a subsequent manual or automated step.

**REQ-E5:** When a reviewer approves a `rename` proposal, the system shall update the node's `name` field and record the old name in the audit trail.

**REQ-E6:** When a reviewer rejects a proposal, the system shall set the proposal status to `rejected` with an optional `rejection_reason` and the proposal shall not modify the taxonomy tree.

**REQ-E7:** When a taxonomy node is deleted manually, the system shall reassign all documents belonging to that node to the parent node (or to "uncategorized" if the node is a root-level node).

**REQ-E8:** When a user navigates to the "Taxonomy" tab on the KB detail page, the system shall fetch and display the approved taxonomy tree with document counts per node.

### 3.3 State-Driven Requirements (while...shall)

**REQ-S1:** While a user has the `viewer` role on a knowledge base, the system shall display the taxonomy tree and proposal queue in read-only mode with all mutation actions hidden.

**REQ-S2:** While a user has the `owner` or `contributor` role, the system shall display full taxonomy management controls including add, rename, delete nodes and approve/reject proposals.

**REQ-S3:** While the review queue contains pending proposals, the system shall display a badge count on the "Taxonomy" tab header showing the number of pending proposals.

### 3.4 Optional Requirements (where...shall)

**REQ-O1:** Where a knowledge base has zero taxonomy nodes and zero proposals, the system shall display an empty state with guidance text explaining that taxonomy nodes will be generated automatically by the AI pipeline once documents are ingested.

**REQ-O2:** Where a proposal includes a `confidence_score` from BERTopic, the system shall display the confidence as a visual indicator (e.g., progress bar or percentage) in the review queue.

### 3.5 Unwanted Behavior Requirements (if...then...shall)

**REQ-N1:** If a taxonomy proposal references a node ID that does not exist (e.g., a deleted node), then the system shall reject the proposal with a `409 Conflict` status and a descriptive error message.

**REQ-N2:** If a user attempts to create a taxonomy node with a name that already exists among its siblings, then the system shall return a `409 Conflict` error and shall not create the duplicate.

**REQ-N3:** If a non-owner/non-contributor attempts to mutate taxonomy data (create, update, delete nodes or approve/reject proposals), then the system shall return `403 Forbidden`.

**REQ-N4:** If deleting a taxonomy node would orphan documents without a fallback parent, then the system shall reassign documents to the nearest ancestor or a special "Uncategorized" root node, and shall never leave documents without a taxonomy assignment.

---

## 4. Specifications

### 4.1 Data Model

#### Table: `portal_taxonomy_nodes`

| Column        | Type                       | Constraints                                          |
|---------------|----------------------------|------------------------------------------------------|
| `id`          | `Integer`                  | Primary key                                          |
| `kb_id`       | `Integer`                  | FK -> `portal_knowledge_bases.id`, ON DELETE CASCADE  |
| `parent_id`   | `Integer` (nullable)       | FK -> `portal_taxonomy_nodes.id`, ON DELETE SET NULL  |
| `name`        | `String(128)`              | NOT NULL                                             |
| `slug`        | `String(128)`              | NOT NULL, generated from name                        |
| `description` | `Text` (nullable)          |                                                      |
| `doc_count`   | `Integer`                  | NOT NULL, DEFAULT 0, denormalized counter            |
| `sort_order`  | `Integer`                  | NOT NULL, DEFAULT 0                                  |
| `created_at`  | `DateTime(timezone=True)`  | DEFAULT now()                                        |
| `created_by`  | `String(64)`               | NOT NULL, Zitadel user ID                            |
| `updated_at`  | `DateTime(timezone=True)`  | DEFAULT now(), ON UPDATE now()                       |

**Indexes:**
- `ix_taxonomy_nodes_kb_id` on `kb_id`
- `uq_taxonomy_node_parent_name` UNIQUE on `(kb_id, parent_id, name)` -- siblings cannot share a name

#### Table: `portal_taxonomy_proposals`

| Column             | Type                       | Constraints                                          |
|--------------------|----------------------------|------------------------------------------------------|
| `id`               | `Integer`                  | Primary key                                          |
| `kb_id`            | `Integer`                  | FK -> `portal_knowledge_bases.id`, ON DELETE CASCADE  |
| `proposal_type`    | `String(32)`               | NOT NULL, CHECK IN ('new_node', 'merge', 'split', 'rename') |
| `status`           | `String(32)`               | NOT NULL, DEFAULT 'pending', CHECK IN ('pending', 'approved', 'rejected') |
| `title`            | `String(256)`              | NOT NULL, human-readable summary                     |
| `payload`          | `JSONB`                    | NOT NULL, type-specific data (see below)             |
| `confidence_score` | `Float` (nullable)         | 0.0-1.0 from BERTopic                               |
| `created_at`       | `DateTime(timezone=True)`  | DEFAULT now()                                        |
| `reviewed_at`      | `DateTime(timezone=True)` (nullable) |                                             |
| `reviewed_by`      | `String(64)` (nullable)    | Zitadel user ID of reviewer                          |
| `rejection_reason` | `Text` (nullable)          |                                                      |

**Indexes:**
- `ix_taxonomy_proposals_kb_id` on `kb_id`
- `ix_taxonomy_proposals_status` on `status`

**Payload schemas by `proposal_type`:**

```
new_node:   { "parent_id": int|null, "name": str, "description": str|null }
merge:      { "source_node_id": int, "target_node_id": int }
split:      { "source_node_id": int, "new_children": [{"name": str}, ...] }
rename:     { "node_id": int, "old_name": str, "new_name": str }
```

### 4.2 API Endpoints

All endpoints are prefixed with `/api/app/knowledge-bases/{kb_slug}/taxonomy`.

#### Taxonomy Nodes

| Method   | Path                   | Description                    | Auth Required | Min Role      |
|----------|------------------------|--------------------------------|---------------|---------------|
| `GET`    | `/nodes`               | List full taxonomy tree        | Yes           | viewer        |
| `POST`   | `/nodes`               | Create a new node              | Yes           | contributor   |
| `PATCH`  | `/nodes/{node_id}`     | Rename or move a node          | Yes           | contributor   |
| `DELETE` | `/nodes/{node_id}`     | Delete a node (reassign docs)  | Yes           | owner         |

#### Taxonomy Proposals

| Method   | Path                            | Description                    | Auth Required | Min Role       |
|----------|---------------------------------|--------------------------------|---------------|----------------|
| `GET`    | `/proposals`                    | List proposals (filterable)    | Yes           | viewer         |
| `POST`   | `/proposals`                    | Submit a new proposal          | Yes           | internal/system |
| `POST`   | `/proposals/{id}/approve`       | Approve a proposal             | Yes           | contributor    |
| `POST`   | `/proposals/{id}/reject`        | Reject a proposal              | Yes           | contributor    |

#### Request/Response Schemas

**Create Node (`POST /nodes`):**
```json
{
  "parent_id": 5,
  "name": "Machine Learning",
  "description": "Documents about ML techniques"
}
```

**Node Response:**
```json
{
  "id": 12,
  "parent_id": 5,
  "name": "Machine Learning",
  "slug": "machine-learning",
  "description": "Documents about ML techniques",
  "doc_count": 0,
  "sort_order": 0,
  "children": []
}
```

**Tree Response (`GET /nodes`):**
```json
{
  "nodes": [
    {
      "id": 1,
      "parent_id": null,
      "name": "Engineering",
      "slug": "engineering",
      "doc_count": 42,
      "children": [
        { "id": 5, "parent_id": 1, "name": "Backend", "doc_count": 18, "children": [] },
        { "id": 6, "parent_id": 1, "name": "Frontend", "doc_count": 24, "children": [] }
      ]
    }
  ],
  "total_doc_count": 42
}
```

**Proposal Response:**
```json
{
  "id": 3,
  "proposal_type": "new_node",
  "status": "pending",
  "title": "Add 'Machine Learning' under Engineering",
  "payload": { "parent_id": 1, "name": "Machine Learning" },
  "confidence_score": 0.87,
  "created_at": "2026-03-27T10:00:00Z",
  "reviewed_at": null,
  "reviewed_by": null
}
```

### 4.3 Frontend Components

**Tab Addition:**
- Add `'taxonomy'` to `type KBTab = 'overview' | 'connectors' | 'members' | 'items' | 'taxonomy'`
- New tab uses `FolderTree` icon from Lucide
- Badge count for pending proposals shown on tab header

**TaxonomySection Component:**
- Split into two sub-sections: **Tree View** (left/top) and **Review Queue** (right/bottom)
- Tree view: indented list with expand/collapse, doc count per node, inline rename, add child, delete
- Review queue: table of pending proposals with approve/reject action buttons
- Empty state when no nodes exist

**i18n keys prefix:** `knowledge_taxonomy_*`

### 4.4 Constraints

- **No time estimates** -- milestones are priority-ordered only
- **Klai model policy** -- No OpenAI/Anthropic model names; BERTopic runs on self-hosted infrastructure
- **EU data residency** -- All taxonomy data stored in EU PostgreSQL; no external API calls for taxonomy operations
- **Separation of concerns** -- Taxonomy UI is a Section component within the existing KB detail page; data fetching is inline in `useQuery`/`useMutation`
- **portal-ui-components** -- All form elements use `<Input>`, `<Label>`, `<Select>`, `<Button>`, `<Card>` from `components/ui/`
- **Color tokens** -- All semantic colors use CSS variables (`--color-destructive`, `--color-success`, etc.)
- **Logging** -- Add `taxonomyLogger` to `lib/logger.ts`; no `console.log`

---

## 5. Traceability

| Requirement | Module | Test |
|-------------|--------|------|
| REQ-U1      | M1-database, M2-api | AC-1.1 |
| REQ-U2      | M1-database | AC-1.2 |
| REQ-U3      | M1-database, M2-api | AC-1.3 |
| REQ-U4      | M2-api | AC-1.4 |
| REQ-E1      | M2-api | AC-2.1 |
| REQ-E2      | M2-api | AC-2.2 |
| REQ-E3      | M2-api | AC-2.3 |
| REQ-E4      | M2-api | AC-2.4 |
| REQ-E5      | M2-api | AC-2.5 |
| REQ-E6      | M2-api | AC-2.6 |
| REQ-E7      | M2-api | AC-2.7 |
| REQ-E8      | M3-frontend | AC-3.1 |
| REQ-S1      | M3-frontend | AC-3.2 |
| REQ-S2      | M3-frontend | AC-3.3 |
| REQ-S3      | M3-frontend | AC-3.4 |
| REQ-O1      | M3-frontend | AC-4.1 |
| REQ-O2      | M3-frontend | AC-4.2 |
| REQ-N1      | M2-api | AC-5.1 |
| REQ-N2      | M2-api | AC-5.2 |
| REQ-N3      | M2-api | AC-5.3 |
| REQ-N4      | M2-api | AC-5.4 |
