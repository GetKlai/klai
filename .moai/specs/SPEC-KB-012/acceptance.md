# SPEC-KB-012: Acceptance Criteria -- Taxonomy Management

| Field       | Value                                         |
|-------------|-----------------------------------------------|
| SPEC ID     | SPEC-KB-012                                   |
| Title       | Taxonomy Management for Knowledge Bases       |
| Created     | 2026-03-27                                    |

---

## AC-1: Data Model and Integrity (REQ-U1 through REQ-U4)

### AC-1.1: Org-Scoped Isolation

**Given** two organizations (Org-A and Org-B), each with a knowledge base
**When** Org-A creates taxonomy nodes in their KB
**Then** the nodes are only visible via Org-A's API requests
**And** Org-B's API requests to the same KB slug return 404 or empty results
**And** direct database queries confirm `kb_id` belongs to the correct org

### AC-1.2: Referential Integrity on Parent Deletion

**Given** a taxonomy tree with nodes: Root -> Category -> Subcategory
**When** the "Category" node is deleted
**Then** "Subcategory" is reassigned to "Root" (the grandparent)
**And** the `parent_id` of "Subcategory" is updated to Root's ID
**And** document counts are recalculated for affected nodes

**Given** a root-level node with children is deleted
**When** no parent exists to reassign to
**Then** children become new root-level nodes (`parent_id` = NULL)
**And** documents from the deleted node are reassigned to the first child or remain unassigned

### AC-1.3: Sibling Name Uniqueness

**Given** a parent node "Engineering" with child "Backend"
**When** a user attempts to create another child named "Backend" under "Engineering"
**Then** the API returns 409 Conflict
**And** no duplicate node is created
**And** the error message indicates the name already exists

**Given** two different parent nodes "Engineering" and "Design"
**When** both have a child named "Tools"
**Then** both nodes exist without conflict (uniqueness is scoped to siblings)

### AC-1.4: Audit Trail

**Given** any taxonomy mutation (create, rename, delete, merge, split, approve, reject)
**When** the mutation completes successfully
**Then** the response includes a timestamp
**And** the `created_by`, `reviewed_by`, or equivalent field contains the Zitadel user ID of the actor

---

## AC-2: Event-Driven Behaviors (REQ-E1 through REQ-E8)

### AC-2.1: Proposal Submission

**Given** the knowledge-ingest service has completed BERTopic analysis
**When** it POSTs a proposal to `/api/app/knowledge-bases/{kb_slug}/taxonomy/proposals`
**Then** the proposal is created with status `pending`
**And** the response includes the proposal ID, type, and payload
**And** the proposal appears in the review queue on the frontend

### AC-2.2: Approve New Node Proposal

**Given** a pending proposal of type `new_node` with payload `{"parent_id": 1, "name": "ML"}`
**When** a contributor approves the proposal via `POST /proposals/{id}/approve`
**Then** a new taxonomy node "ML" is created under node 1
**And** the proposal status changes to `approved`
**And** `reviewed_by` contains the approver's Zitadel user ID
**And** `reviewed_at` contains the current timestamp

### AC-2.3: Approve Merge Proposal

**Given** a pending merge proposal with `{"source_node_id": 5, "target_node_id": 3}`
**And** source node 5 has 12 documents assigned
**And** target node 3 has 8 documents assigned
**When** a contributor approves the proposal
**Then** all 12 documents from node 5 are reassigned to node 3
**And** node 3's `doc_count` becomes 20
**And** node 5 is deleted from the taxonomy tree
**And** children of node 5 are reassigned to node 3
**And** the operation completes atomically (all-or-nothing)

### AC-2.4: Approve Split Proposal

**Given** a pending split proposal with `{"source_node_id": 1, "new_children": [{"name": "Frontend"}, {"name": "Backend"}]}`
**When** a contributor approves the proposal
**Then** two new nodes "Frontend" and "Backend" are created under node 1's parent
**And** the source node 1 remains in the tree (not deleted)
**And** document reassignment is not performed automatically
**And** the proposal status changes to `approved`

### AC-2.5: Approve Rename Proposal

**Given** a pending rename proposal with `{"node_id": 5, "old_name": "Dev", "new_name": "Development"}`
**When** a contributor approves the proposal
**Then** node 5's name changes to "Development"
**And** node 5's slug is regenerated to "development"
**And** the proposal status changes to `approved`

### AC-2.6: Reject Proposal

**Given** a pending proposal of any type
**When** a contributor rejects the proposal with reason "Not relevant to our taxonomy"
**Then** the proposal status changes to `rejected`
**And** `rejection_reason` contains "Not relevant to our taxonomy"
**And** no changes are made to the taxonomy tree
**And** the proposal remains in the history (not deleted)

### AC-2.7: Manual Node Deletion with Document Reassignment

**Given** a taxonomy node "Legacy" with 5 documents and parent "Engineering"
**When** an owner deletes the "Legacy" node via `DELETE /nodes/{id}`
**Then** all 5 documents are reassigned to "Engineering" (the parent)
**And** "Engineering"'s `doc_count` increases by 5
**And** the "Legacy" node is removed from the tree
**And** any children of "Legacy" are reassigned to "Engineering"

### AC-3.1: Taxonomy Tab Display

**Given** a user navigates to the KB detail page
**When** the page loads
**Then** a "Taxonomy" tab is visible in the tab bar alongside existing tabs
**And** clicking the tab shows the taxonomy tree and proposal queue
**And** the tree displays nodes with doc counts in parentheses
**And** nodes with children have expand/collapse controls

---

## AC-3: State-Driven UI Behaviors (REQ-S1 through REQ-S3)

### AC-3.2: Viewer Read-Only Mode

**Given** a user with `viewer` role on a knowledge base
**When** they view the Taxonomy tab
**Then** the taxonomy tree is displayed with doc counts
**And** the proposal queue is visible with proposal details
**And** no "Add Node", "Rename", "Delete" buttons are shown on the tree
**And** no "Approve", "Reject" buttons are shown on proposals
**And** no form elements for creating nodes are rendered

### AC-3.3: Owner/Contributor Full Access

**Given** a user with `contributor` role on a knowledge base
**When** they view the Taxonomy tab
**Then** the tree shows "Add child" and "Rename" action buttons per node
**And** the proposal queue shows "Approve" and "Reject" buttons
**And** an "Add root node" button is visible above the tree

**Given** a user with `owner` role on a knowledge base
**When** they view the Taxonomy tab
**Then** all contributor actions are available
**And** additionally, "Delete" action buttons are shown per node

### AC-3.4: Pending Proposal Badge

**Given** a knowledge base with 3 pending taxonomy proposals
**When** the KB detail page loads
**Then** the Taxonomy tab header shows a badge with "3"
**And** the badge uses the accent color token

**Given** a knowledge base with 0 pending proposals
**When** the KB detail page loads
**Then** no badge is shown on the Taxonomy tab header

---

## AC-4: Optional Features (REQ-O1, REQ-O2)

### AC-4.1: Empty State

**Given** a knowledge base with no taxonomy nodes and no proposals
**When** a user views the Taxonomy tab
**Then** a centered empty state is displayed
**And** the empty state includes an icon and explanatory text
**And** the text explains that taxonomy nodes will be generated automatically by AI
**And** if the user is a contributor/owner, an "Add first node" button is shown

### AC-4.2: Confidence Score Display

**Given** a pending proposal with `confidence_score: 0.87`
**When** the proposal is displayed in the review queue
**Then** "87%" is shown in the confidence column
**And** the visual indicator uses the accent color

**Given** a pending proposal with `confidence_score: null`
**When** the proposal is displayed in the review queue
**Then** the confidence column shows "--" or a dash

---

## AC-5: Error Handling (REQ-N1 through REQ-N4)

### AC-5.1: Proposal References Deleted Node

**Given** a pending proposal of type `rename` referencing `node_id: 99`
**And** node 99 has been deleted
**When** a reviewer attempts to approve the proposal
**Then** the API returns 409 Conflict
**And** the error message indicates "Referenced node does not exist"
**And** the proposal status remains `pending`

### AC-5.2: Duplicate Sibling Name

**Given** a parent node "Engineering" with existing child "Backend"
**When** a user POSTs to create a node with `{"parent_id": 1, "name": "Backend"}`
**Then** the API returns 409 Conflict
**And** the error message indicates the name already exists
**And** no node is created

### AC-5.3: Unauthorized Mutation

**Given** a user with `viewer` role on a knowledge base
**When** they attempt to POST to `/taxonomy/nodes` to create a node
**Then** the API returns 403 Forbidden
**And** no node is created

**Given** a user with `contributor` role
**When** they attempt to DELETE a taxonomy node
**Then** the API returns 403 Forbidden (only owners can delete)

### AC-5.4: Safe Document Reassignment on Delete

**Given** a root-level taxonomy node "Miscellaneous" with 10 documents
**And** no parent node exists (root level)
**When** an owner deletes "Miscellaneous"
**Then** documents are reassigned to a sibling root node or remain with `taxonomy_node_id = NULL`
**And** no documents are left in an inconsistent state
**And** the API returns 200 with the count of reassigned documents

---

## Quality Gates

### Definition of Done

- [ ] All 8 API endpoints return correct responses for success and error cases
- [ ] Taxonomy tree builds correctly from flat database rows (3+ levels deep)
- [ ] Proposal approval executes type-specific logic atomically
- [ ] Org isolation prevents cross-tenant data access
- [ ] Role-based access control enforced on all mutation endpoints
- [ ] Frontend Taxonomy tab renders tree with expand/collapse and doc counts
- [ ] Frontend proposal queue shows pending proposals with approve/reject
- [ ] Viewer mode hides all mutation controls
- [ ] Badge count shows pending proposals on tab header
- [ ] All UI strings are in Paraglide with EN + NL translations
- [ ] `taxonomyLogger` added to `lib/logger.ts` and used (no `console.log`)
- [ ] All semantic colors use CSS variable tokens (no raw Tailwind color classes)
- [ ] Alembic migration runs cleanly (up and down)

### Verification Methods

| Method | Scope |
|--------|-------|
| pytest with async fixtures | Backend API endpoints, access control, tree building |
| Manual curl/httpx | API integration testing, error responses |
| Vite dev server + browser | Frontend rendering, tab navigation, interactions |
| Playwright spot-check | Full flow: create node -> view in tree -> delete |
| Database query | Verify data integrity, index usage, constraint enforcement |

### Test Scenarios Summary

| Category | Count | Coverage |
|----------|-------|----------|
| Org isolation | 1 | REQ-U1 |
| Referential integrity | 2 | REQ-U2 |
| Sibling uniqueness | 2 | REQ-U3 |
| Audit trail | 1 | REQ-U4 |
| Proposal lifecycle | 7 | REQ-E1 through REQ-E7 |
| Tab display | 1 | REQ-E8 |
| Role-based UI | 3 | REQ-S1 through REQ-S3 |
| Empty state | 1 | REQ-O1 |
| Confidence display | 1 | REQ-O2 |
| Error handling | 4 | REQ-N1 through REQ-N4 |
| **Total** | **23** | **20 requirements** |
