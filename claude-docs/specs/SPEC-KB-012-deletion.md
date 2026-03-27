# SPEC-KB-012: Knowledge Base Deletion

> Status: COMPLETED — 2026-03-26
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-003-app-layer.md (KB detail page, owner role)

---

## What this SPEC covers

End-to-end deletion of a knowledge base: cascading cleanup across all systems, a
slug tombstone that permanently blocks reuse, and a text-validation confirmation
modal in the portal frontend.

---

## Current state (gaps to close)

- `DELETE /app/knowledge-bases/{kb_slug}` exists but only deletes the
  `portal_knowledge_bases` row. It leaves orphaned data in docs.knowledge_bases,
  Gitea, Qdrant, and knowledge-ingest.
- No tombstone mechanism exists. A deleted slug can be reused immediately,
  which could attach orphaned Qdrant vectors to a new KB.
- There is no deletion UI in the frontend.

---

## Design Decisions

### D1: Self-service deletion — no admin approval required

The KB owner already has full authority over the KB's contents, members, and
connectors. Requiring an org admin to approve deletion adds friction with no
security benefit: the owner can already destroy all content by deleting
connectors and clearing items.

**Decision:** KB deletion is self-service for the KB owner. The text-validation
modal (type the slug to confirm) is the safety gate.

**Rationale for rejecting admin approval:**
- The owner pattern is established throughout SPEC-KB-003.
- Admin approval workflows are an enterprise feature with dedicated UX; adding
  them here would couple unrelated concerns.
- If an enterprise tier ever needs approval workflows, that is a separate SPEC.

### D2: Slug tombstone is permanent and org-scoped

Once a KB is deleted, its slug is permanently reserved for that org. A new KB
with the same slug cannot be created, even by the same owner.

**Rationale:** Qdrant vectors are keyed on `(org_id, kb_slug)`. Even after
cleanup, eventual-consistency windows mean a new KB with the same slug could
temporarily receive stale vectors from a previous KB. The tombstone closes this
risk permanently.

### D3: External services are cleaned up before the portal DB row is deleted

If Gitea or docs-app deletion fails, the portal KB row is NOT deleted. The user
receives an error and can retry. Partial deletion (some systems cleaned, others
not) is never committed.

**Exception:** Qdrant deletion failure is retried once. If it still fails, the
operation is aborted before touching any other system.

### D4: docs-app DELETE endpoint handles both Gitea and docs.knowledge_bases

The docs-app currently exposes `POST /api/orgs/{org_slug}/kbs` for provisioning.
This SPEC requires a new `DELETE /api/orgs/{org_slug}/kbs/{kb_slug}` endpoint on
docs-app that deletes:
1. The `docs.knowledge_bases` row
2. The Gitea repository

This mirrors the existing provision flow and keeps Gitea credentials confined to
docs-app.

### D5: knowledge-ingest items

If knowledge-ingest maintains a separate index of crawled/synced items, the
portal backend calls the ingest service to remove that index. If ingest items
live only in Qdrant (no separate store), Qdrant deletion in Phase 1 covers them.
The implementation must confirm which applies and add the ingest call only if
needed.

---

## Functional Requirements (EARS format)

### F1 — Tombstone

**F1.1** When a knowledge base is deleted, the system SHALL create a tombstone
record keyed on `(org_id, slug)`.

**F1.2** When a user attempts to create a knowledge base with a slug that matches
an existing tombstone for the same org, the system SHALL reject the request with
HTTP 409 and the error message:
> "This slug was previously used for a deleted knowledge base and cannot be
> reused."

**F1.3** The tombstone SHALL be permanent (no expiry, no override).

---

### F2 — Cascading deletion

**F2.1** When a KB owner initiates deletion, the system SHALL remove ALL of the
following before committing the deletion:

| System | What is deleted |
|--------|----------------|
| Qdrant | All vectors in `klai_knowledge` collection with matching `org_id` + `kb_slug` |
| docs-app | `docs.knowledge_bases` row (via `DELETE /api/orgs/{org_slug}/kbs/{kb_slug}`) |
| Gitea | Repository for the KB (handled by docs-app, see D4) |
| knowledge-ingest | Indexed items for this KB (if separate store exists) |
| portal DB | `portal_user_kb_access` rows (via CASCADE on FK) |
| portal DB | `portal_group_kb_access` rows (via CASCADE on FK) |
| portal DB | `portal_knowledge_bases` row |
| portal DB | `portal_kb_tombstones` INSERT (org_id, slug, deleted_at, deleted_by) |

**F2.2** If any external service deletion fails, the system SHALL abort and
return an error. No portal DB rows SHALL be deleted in that case.

**F2.3** The portal DB cleanup (delete KB row + insert tombstone) SHALL be
performed in a single database transaction.

---

### F3 — Deletion order

The backend SHALL execute deletion in the following order to minimise data
leakage on failure:

1. **Qdrant** — vectors are the highest-risk orphan; delete first.
2. **knowledge-ingest** — remove ingest index for this KB (if applicable).
3. **docs-app** — calls `DELETE /api/orgs/{org_slug}/kbs/{kb_slug}`, which
   removes the docs.knowledge_bases row and the Gitea repo.
4. **Portal DB transaction** — delete `portal_knowledge_bases` row (cascades
   user/group access rows) and insert tombstone.

If step 1–3 all succeed and step 4 fails (DB transaction error), this is a
critical error. Log at ERROR level with full context. The KB row still exists,
but the external data has been cleaned. This edge case is acknowledged but not
automatically recovered — surface the error to the user and alert ops.

---

### F4 — Error states

**F4.1** If Qdrant deletion fails (after one retry), the system SHALL return HTTP
502 with detail `"Qdrant cleanup failed"`. No other system is touched.

**F4.2** If docs-app deletion fails, the system SHALL return HTTP 502 with detail
`"Docs/Gitea cleanup failed"`. Qdrant vectors have already been removed at this
point; the KB row is NOT deleted.

**F4.3** If knowledge-ingest cleanup fails, the system SHALL return HTTP 502 with
detail `"Ingest index cleanup failed"`. No other system is touched beyond Qdrant.

**F4.4** All 502 responses SHALL include a `retry` hint in the response body so
the frontend can offer a retry action.

---

### F5 — Authorization

**F5.1** Only the KB owner (role = `owner` in `portal_user_kb_access`) MAY
initiate deletion.

**F5.2** Org admins do NOT get automatic KB deletion rights unless they are also
listed as KB owner.

**F5.3** Attempting deletion without owner role SHALL return HTTP 403.

---

### F6 — Frontend — text-validation modal

**F6.1** The KB detail page (`/app/knowledge/$kbSlug`) SHALL contain a "Danger
zone" section, visible only to KB owners, styled with a red border and placed at
the bottom of the page below all other content. This section contains the delete
action and no other actions.

**F6.2** Clicking "Delete knowledge base" in the danger zone SHALL open a modal
that lists exactly:
- Knowledge base name
- Number of indexed items (from KB stats)
- Number of connectors that will be disconnected
- Whether a Gitea repository will be deleted (if `gitea_repo_slug` is set)
- Whether a Docs site will be removed (if `docs_enabled` is true)

**F6.3** The modal SHALL include a text input where the user must type the exact
KB slug to enable the confirm button.

**F6.4** The confirm button SHALL remain disabled until the typed value matches
the KB slug exactly (case-sensitive).

**F6.5** While deletion is in progress, the modal SHALL show a loading state and
prevent re-submission.

**F6.6** On success, the frontend SHALL navigate to `/app/knowledge` and
invalidate the KB list query.

**F6.7** On error (502), the modal SHALL remain open and display the error
message with a retry option. The error SHALL NOT be dismissible without
acknowledging it.

**F6.8** The modal style SHALL match the existing text-validation modals in the
portal (same as the current `DeleteConfirmButton` pattern, but extended with the
item list).

---

### F7 — Edge cases

**F7.1** KB with active connectors: deletion is allowed. The modal warns that all
connectors will be disconnected. Active sync jobs for those connectors SHALL be
cancelled before Qdrant cleanup begins.

**F7.2** KB with 0 items: deletion proceeds normally. The Qdrant delete call is
still made (it is a no-op if no vectors match) to ensure consistency.

**F7.3** KB with `docs_enabled = false` and `gitea_repo_slug = null`: the
docs-app deletion call is skipped. The modal does not mention Gitea or Docs.

**F7.4** Concurrent deletion: if two deletion requests arrive simultaneously for
the same KB, one will succeed (commit tombstone + delete row) and the other will
receive 404 from `_get_kb_or_404`. This is acceptable.

---

## Acceptance Criteria

### AC-1 — Tombstone blocks slug reuse

GIVEN a KB with slug `my-kb` in org `acme` has been deleted
WHEN a user in org `acme` creates a new KB with slug `my-kb`
THEN the API returns HTTP 409 with the slug-reserved error message
AND the portal shows this error inline on the "new KB" form

### AC-2 — Full cascade: all systems cleaned

GIVEN a KB with Gitea repo, docs enabled, Qdrant vectors, and 1 connector
WHEN the owner completes the text-validation modal
THEN after deletion:
- Qdrant has no vectors matching `{org_id, kb_slug}`
- `docs.knowledge_bases` row is gone
- Gitea repo is deleted
- `portal_knowledge_bases` row is gone
- `portal_user_kb_access` rows for this KB are gone
- `portal_kb_tombstones` has a row for `(org_id, slug)`

### AC-3 — Gitea failure aborts deletion

GIVEN docs-app returns 500 on the DELETE call
WHEN the deletion is in progress
THEN Qdrant vectors are cleaned up
AND the portal KB row remains in the database
AND the API returns HTTP 502
AND the frontend shows the error with a retry option

### AC-4 — Text input enforces slug match

GIVEN the delete modal is open for KB with slug `my-kb`
WHEN the user types `My-KB` (wrong case)
THEN the confirm button remains disabled
WHEN the user types `my-kb` (exact match)
THEN the confirm button becomes enabled

### AC-5 — Non-owner cannot delete

GIVEN user has role `contributor` on a KB
WHEN they call `DELETE /app/knowledge-bases/{kb_slug}`
THEN the API returns HTTP 403

### AC-6 — KB with 0 items deletes cleanly

GIVEN a KB with no Qdrant vectors and no connectors
WHEN the owner deletes it
THEN the deletion completes without error
AND the tombstone is created

### AC-7 — Connector sync cancelled before deletion

GIVEN a KB with a connector that has an active sync job
WHEN deletion is initiated
THEN the sync job is cancelled before Qdrant cleanup
AND deletion proceeds to completion

---

## API Contract

### DELETE /app/knowledge-bases/{kb_slug}

**Auth:** Bearer token, owner role required

**Success:** HTTP 204 No Content

**Error responses:**

| Status | detail | When |
|--------|--------|------|
| 403 | `"Owner role required"` | Caller is not KB owner |
| 404 | `"Knowledge base not found"` | KB does not exist for this org |
| 409 | *(not applicable on delete)* | — |
| 502 | `"Qdrant cleanup failed"` | Qdrant delete failed after retry |
| 502 | `"Ingest index cleanup failed"` | Ingest service unavailable |
| 502 | `"Docs/Gitea cleanup failed"` | docs-app DELETE returned non-2xx |

**Cleanup sequence (backend):**

```
1. _get_kb_or_404(kb_slug, org_id)
2. _require_owner(kb, caller_id)
3. Cancel active sync jobs for KB connectors
4. DELETE qdrant vectors (org_id + kb_slug filter), retry once on failure
5. DELETE ingest index (if applicable)
6. DELETE docs-app: DELETE http://docs-app:3010/docs/api/orgs/{org_slug}/kbs/{kb_slug}
7. BEGIN TRANSACTION
   a. DELETE portal_knowledge_bases WHERE id = kb.id  (cascades access rows)
   b. INSERT portal_kb_tombstones (org_id, slug, deleted_at, deleted_by)
   c. COMMIT
8. Return 204
```

---

## New data model: portal_kb_tombstones

```sql
CREATE TABLE portal_kb_tombstones (
    id          SERIAL PRIMARY KEY,
    org_id      VARCHAR NOT NULL,
    slug        VARCHAR NOT NULL,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_by  VARCHAR NOT NULL,         -- user_id of the deleting user
    UNIQUE (org_id, slug)
);
```

This table is append-only. No rows are ever deleted from it.

---

## Changes required

### Backend

| File | Change |
|------|--------|
| `portal/backend/app/models/knowledge_bases.py` | Add `PortalKBTombstone` model |
| `portal/backend/app/api/app_knowledge_bases.py` | Rewrite `delete_app_knowledge_base` to execute full cascade |
| `portal/backend/app/api/app_knowledge_bases.py` | Add tombstone check to `POST /knowledge-bases` |
| `portal/backend/app/services/docs_client.py` | Add `deprovision_kb(org_slug, kb_slug)` function |
| `portal/backend/alembic/versions/` | Migration: create `portal_kb_tombstones` table |

### docs-app

| File | Change |
|------|--------|
| `docs/src/server/api/orgs/[orgSlug]/kbs/[kbSlug].delete.ts` (or equivalent) | New DELETE handler: delete `docs.knowledge_bases` row + delete Gitea repo via `GITEA_ADMIN_TOKEN` |

### Frontend

| File | Change |
|------|--------|
| `portal/frontend/src/routes/app/knowledge/$kbSlug.tsx` | Add delete button (owner-only), open confirmation modal |
| `portal/frontend/src/components/ui/` | New `DeleteKbModal` component (text-validation modal) |

---

## Out of scope

- Admin approval workflows (see D1)
- Restoring / undeleting a KB
- Bulk deletion of multiple KBs
- Org-level deletion (which would cascade to all KBs — separate SPEC)

---

## Implementation Notes

Implemented 2026-03-26. Commits on `main`:
- `feat(knowledge): full KB deletion cascade with tombstone (SPEC-KB-012)`
- `fix(knowledge): fix floating promises in DeleteKbModal`
- `fix(knowledge): fix alembic migration merge heads for kb tombstones`
- `fix(portal-api): regenerate uv.lock to fix duplicate motor package entries`
- `fix(portal-api): use log.exception in deprovision_kb except blocks (TRY400)`
- `feat(knowledge): move danger zone to 4th Settings tab (owner-only)`
- `fix(knowledge): replace "Gitea repository" with user-friendly label in delete modal`

### Deviations from SPEC

**F6.1 — Danger zone placement:** The danger zone was moved from "bottom of the
page" to a dedicated 4th tab ("Instellingen") visible only to KB owners. This
improves UX by separating destructive actions from operational content.

**Modal copy — Gitea label:** The modal lists "Docs pagina's en
versiegeschiedenis" instead of "Gitea repository" (which was not meaningful to
end users).

**F6.7 — Retry option:** On 502 error the modal stays open and shows the error
message, but does not show an explicit retry button. The user can re-click the
confirm button to retry after clearing the error.

**knowledge-ingest (F2.1 / F3.2):** Ingest items live only in Qdrant; there is
no separate ingest store for KB items. The Qdrant delete in Phase 1 covers them.
The ingest cleanup step (D5) was confirmed unnecessary and not implemented.

**docs-app DELETE endpoint:** The endpoint `DELETE
/api/orgs/{org_slug}/kbs/{kb_slug}` already existed in docs-app. No new endpoint
was needed; `deprovision_kb()` in `docs_client.py` calls it directly.

**org_id type:** The SPEC data model shows `org_id VARCHAR`. The actual
implementation uses `org_id INTEGER` (matching `portal_orgs.id`) with a proper
FK constraint.

**Alembic migration:** The migration uses `down_revision` as a tuple of three
existing heads `("b4c5d6e7f8g9", "b5c6d7e8f9a0", "c4d5e6f7a8b9")` — a merge
migration. The migration was applied directly via psql due to a pycache
duplicate-revision issue, then stamped with `alembic stamp a3b4c5d6e7f8`.
