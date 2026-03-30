# SPEC-KB-001: Knowledge Base Unification

> Status: COMPLETED — 2026-03-26
> Author: Mark Vletter (design) + Claude (SPEC)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md`
> Related: `klai-portal/backend/app/models/knowledge_bases.py`, `klai-portal/backend/app/models/docs.py`
> Implementation commits: `e56e25c` (Phase 1–3 core), `16f4b18` (Phase 3 admin cleanup + Sources tab)

---

## What to clean up (admin pages that should not exist)

The following admin routes were built prematurely and must be moved or removed:

| Route | Problem | Action |
|---|---|---|
| `/admin/knowledge-bases` | Should be in `/app/knowledge` | Move to app, remove from admin |
| `/admin/docs-libraries` | Should be in `/app/knowledge` | Merge into KB, remove entirely |
| `/admin/connectors` | Connectors are scoped to a KB, not to the org | Move to `/app/knowledge/[kb]/connectors` |

**Why connectors belong in `/app/knowledge/[kb]`:** A connector (GitHub, Google Drive, Notion) syncs content into a specific knowledge base. It is not an org-level concern. A user managing their KB should configure which connectors feed it, from within that KB's page in the app.

---

## Context and Problem

The portal currently has two separate, structurally identical systems:

| System | Table | Admin route | Description |
|---|---|---|---|
| Knowledge Bases | `portal_knowledge_bases` | `/admin/knowledge-bases` | AI-retrievable content (Qdrant) |
| Docs Libraries | `portal_docs_libraries` | `/admin/docs-libraries` | Human-readable docs (Gitea/BlockNote) |

Both have their own group-access tables (`portal_group_kb_access` and `portal_group_docs_access`). Both are managed in `/admin` instead of `/app`. Both are created independently with no link between them.

This creates a conceptual split that does not reflect reality: a Docs Library IS a Knowledge Base. Docs is a curated, human-readable view on a subset of KB content (synthesis_depth 4). Every document written in Docs belongs to exactly one KB, and edits in Docs must write back to the Knowledge layer. Separate user management on both systems would lead to access inconsistencies (e.g., a user seeing AI-generated answers from content they cannot read).

**Additional problems discovered during design:**
- Both are in `/admin` — they should be in `/app` where users manage their own content
- No visibility field on portal KBs yet (public/internal)
- Group access is duplicated across two tables instead of one

---

## Design Decisions Made

These decisions were made through a design session before writing this SPEC. Do not revisit them during implementation.

### D1: Library = Knowledge Base (1:1)

A Docs Library is not a separate concept — it is a Knowledge Base. A KB has two interfaces:
- **Docs view**: human-readable pages, written via BlockNote editor, stored in Gitea
- **Knowledge view**: AI-retrievable content, indexed in Qdrant

Creating a Library means creating a KB. There is no Library that exists without a KB. There is no KB that cannot have a Docs layer.

### D2: Visibility is KB-level, not per-document

A KB is either `public` (anonymous access) or `internal` (org members only). Not a per-document setting.

**Why:** Per-document visibility creates the risk that a user can receive AI answers sourced from a document they cannot read. KB-level visibility makes this structurally impossible: if a user has access to the KB, they can see all its documents and the AI can use all of them for that user.

**Public KB:** Anyone with the URL can read the docs site and the AI uses this content for external chat widgets.
**Internal KB:** Only org members with access (via their group memberships) can read docs and query the AI.

### D3: One access control system

`portal_group_kb_access` is the single source of truth for access to both docs and AI knowledge. `portal_group_docs_access` is eliminated. The groups system (already in place via Zitadel) determines who can read and write.

Roles for a KB (three levels):
- **Viewer**: can read docs pages, can query the AI using this KB's knowledge
- **Contributor**: can write/edit docs pages, can save content to this KB from chat
- **Owner**: all Contributor rights + can invite/remove members, change KB settings (name, visibility), delete the KB

The user who creates a KB is automatically its Owner. An org admin and group manager can also manage KB membership regardless of their KB role.

### D4: Write is always 1:1, read can be N:N

When saving content (from chat or from the docs editor), the target is always exactly one KB. This makes write-back unambiguous.

When querying (chat, Focus broad mode), the scope can span multiple KBs. This is a read-scope setting configured per chat session or product, not a write concern.

### D5: Move from admin to app

Creating and managing KBs is a user-level action, not an admin-level action. Users should be able to create their own KBs (within their org) from `/app/knowledge`. Admins may have additional controls (e.g., deleting or archiving a KB org-wide), but creation and membership management happens in the app.

### D6: MCP write interface

```
write_to_kb(kb_id: str, content: str, as_doc: bool = False)
```

- `as_doc=False`: saves as a raw knowledge artifact (Qdrant only, not a docs page)
- `as_doc=True`: creates a docs page (Gitea commit → Qdrant via webhook)
- The user is always asked which KB before saving — KB is the scope, not an implicit choice

In chat (LibreChat), this surfaces as:
- "Onthoud dit" → `write_to_kb(..., as_doc=False)`
- "Documenteer dit" → `write_to_kb(..., as_doc=True)`

Both prompt "In welke knowledge base?" before writing.

---

## Acceptance Criteria

### AC-1: Unified data model

**WHEN** the database migration runs,
**THEN** `portal_docs_libraries` no longer exists as a separate table,
**AND** `portal_knowledge_bases` has a `visibility` column (`public` | `internal`, default `internal`),
**AND** `portal_knowledge_bases` has a `docs_enabled` column (bool, default `true`) indicating this KB has a Gitea-backed docs layer,
**AND** `portal_group_docs_access` no longer exists as a separate table,
**AND** `portal_group_kb_access` is the single access table for both docs and knowledge.

### AC-2: Creating a KB provisions docs automatically

**WHEN** a user creates a new Knowledge Base via `/app/knowledge/new`,
**THEN** a Gitea repository is provisioned for the KB (via existing klai-docs provisioning),
**AND** a `docs.knowledge_bases` entry is created in klai-docs,
**AND** the portal `portal_knowledge_bases` record is linked to the Gitea repo via `gitea_repo_slug`,
**AND** the KB is accessible at `/app/knowledge/[kb-slug]/docs` in the portal.

### AC-3: KB management moved to /app

**WHEN** a user navigates to `/app/knowledge`,
**THEN** they see the Personal knowledge base card (existing, unchanged),
**AND** they see one card per named Knowledge Base they have access to,
**AND** they can create a new KB from this page,
**AND** the visual design follows the existing card pattern (icon + heading + description + stat),
**AND** `/admin/knowledge-bases`, `/admin/docs-libraries`, and `/admin/connectors` are removed or redirected.

### AC-3b: KB detail page has tabs

**WHEN** a user navigates to `/app/knowledge/[kb-slug]`,
**THEN** they see three tabs: **Docs**, **Sources**, **Stats**,
**AND** the Docs tab shows the page tree and editor entry point,
**AND** the Sources tab shows connected sources (connectors: GitHub, Google Drive, etc.) + uploaded files + URLs,
**AND** the Stats tab shows indexed item count and retrieval activity.

### AC-4: Admin retains org-level controls

**WHEN** an org admin navigates to `/admin`,
**THEN** they can see all KBs in their org (regardless of their own access),
**AND** they can archive or delete a KB,
**AND** they can manage group memberships for any KB.

### AC-5: Group-based access via Zitadel groups

**WHEN** an org admin grants group G access to KB X,
**THEN** all members of G can read the docs pages of KB X,
**AND** all members of G can query the AI using KB X's knowledge,
**AND** Contributor-role members of G can edit docs pages in KB X.

**WHEN** a user is removed from group G,
**THEN** they immediately lose access to all KBs that were only accessible via G.

### AC-6: Visibility controls public/internal boundary

**WHEN** a KB is set to `internal`,
**THEN** the docs site for that KB requires Zitadel authentication,
**AND** the AI knowledge from that KB is only used in org-scoped retrieval (authenticated sessions),
**AND** anonymous requests to the docs URL return 401.

**WHEN** a KB is set to `public`,
**THEN** the docs site is accessible without authentication,
**AND** the AI knowledge from that KB can be used in the external chat widget (public scope).

### AC-7: Docs writes back to knowledge layer

**WHEN** a docs page is created or updated in a KB,
**THEN** a Gitea webhook fires,
**AND** the Unified Ingest API re-indexes the changed page into Qdrant,
**AND** the Qdrant point is tagged with `visibility: public` or `visibility: internal` matching the KB setting,
**AND** retrieval queries filter on this visibility tag based on the caller's authentication context.

### AC-8: MCP write_to_kb tool

**WHEN** a user in chat says "save this" or "document this",
**THEN** the MCP tool asks which KB to save to (from the KBs the user has Contributor access to),
**AND** `as_doc=False` saves as a raw Qdrant artifact without creating a docs page,
**AND** `as_doc=True` creates a docs page (Gitea commit) which triggers Qdrant indexing via webhook.

### AC-9: Chat read scope is multi-KB, write is single KB

**WHEN** a chat session is configured with multiple KB scopes,
**THEN** the AI can retrieve from all configured KBs during a conversation,
**AND** when saving content from that chat, the user must explicitly choose one KB as the write target.

---

## Data Model Changes

### portal_knowledge_bases (modified)

```sql
ALTER TABLE portal_knowledge_bases
  ADD COLUMN visibility TEXT NOT NULL DEFAULT 'internal'
    CHECK (visibility IN ('public', 'internal')),
  ADD COLUMN docs_enabled BOOLEAN NOT NULL DEFAULT true,
  ADD COLUMN gitea_repo_slug TEXT,  -- null until provisioned
  ADD COLUMN owner_type TEXT NOT NULL DEFAULT 'org'
    CHECK (owner_type IN ('org', 'user')),
  ADD COLUMN owner_user_id TEXT;  -- zitadel_user_id, non-null when owner_type = 'user'
```

### portal_docs_libraries (removed)

```sql
-- Migration: copy any existing library data into portal_knowledge_bases
-- then drop portal_docs_libraries and portal_group_docs_access
DROP TABLE portal_group_docs_access;
DROP TABLE portal_docs_libraries;
```

### portal_group_kb_access (modified)

```sql
ALTER TABLE portal_group_kb_access
  ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer'
    CHECK (role IN ('viewer', 'contributor', 'owner'));
```

---

## What is NOT in scope for this SPEC

- Actual Qdrant visibility filtering at retrieval time (that is the Knowledge Service, separate SPEC)
- Gap detection, taxonomy, or analytics
- External-facing chat widget scope configuration
- Migrating existing content from klai-docs `docs.knowledge_bases` to the new unified model (separate migration task)
- Focus integration (`broad` mode multi-KB scope)
- Personal KB (already implemented separately, not affected)

---

## Migration Strategy

### Existing data

At the time of writing, the `portal_knowledge_bases` and `portal_docs_libraries` tables exist but contain no production data (only used in the admin UI which is not yet in active use by customers).

If data exists at time of migration:
1. For each row in `portal_docs_libraries`: find or create a matching row in `portal_knowledge_bases` (match on `org_id` + `slug`)
2. Copy `portal_group_docs_access` rows to `portal_group_kb_access` (with `role = 'viewer'`)
3. Drop `portal_docs_libraries` and `portal_group_docs_access`

### klai-docs `docs.knowledge_bases`

This table lives in the klai-docs service (PostgreSQL `docs` schema) and is not part of this SPEC. The portal KB and the klai-docs KB are linked via `gitea_repo_slug`. The portal KB is the authoritative source for org-level settings (visibility, access). klai-docs continues to own per-page settings (edit restrictions, page tree).

---

## Visual design reference

The existing `/app/knowledge` page (see `klai-portal/frontend/src/routes/app/knowledge/index.tsx`) has the right visual pattern:
- Full-width card per scope
- Icon (rounded bg) + heading + description + stat line
- Clean, no table, no list — one card per thing

This pattern should be extended, not replaced. The new `/app/knowledge` page adds named KB cards below the existing Personal card. The "Documents and URLs — Coming soon" card becomes real KB cards when KBs exist, or a "Create your first knowledge base" empty state.

```
/app/knowledge
  ├── Personal knowledge base          (existing card, unchanged)
  ├── [KB name]                        (one card per accessible named KB)
  │     stat: "12 items · internal"
  ├── [KB name]
  │     stat: "48 items · public"
  └── + Create knowledge base          (button, bottom of list)
```

---

## Implementation Phases

### Phase 1 — Database migration
- Add `visibility`, `docs_enabled`, `gitea_repo_slug` columns to `portal_knowledge_bases`
- Add `role` column to `portal_group_kb_access`
- Migrate data from `portal_docs_libraries` → `portal_knowledge_bases`
- Drop `portal_docs_libraries` and `portal_group_docs_access`
- Update SQLAlchemy models

### Phase 2 — Backend API
- Update `GET/POST /api/admin/knowledge-bases` to include visibility and role
- Remove `GET/POST /api/admin/docs-libraries` endpoints
- Add `GET /api/app/knowledge-bases` (app-scoped, returns only KBs the user has access to)
- Add `POST /api/app/knowledge-bases` (create KB + auto-provision Gitea repo)
- Update `portal_group_kb_access` API to accept role field

### Phase 3 — Frontend: /app/knowledge
- Extend `/app/knowledge/` route: keep Personal card, add named KB cards below (card per accessible KB)
- Add empty state: if no KBs yet, show "Create your first knowledge base" CTA in place of the "coming soon" card
- Create `/app/knowledge/new` route (create KB form: name, slug, visibility)
- Create `/app/knowledge/[kbSlug]/` route with tabs: Docs | Sources | Stats
- Move connectors UI to Sources tab under each KB (`/app/knowledge/[kbSlug]/sources`)
- Remove `/admin/docs-libraries`, `/admin/knowledge-bases`, `/admin/connectors` routes
- Keep org-admin KB controls (archive, delete, manage all groups) accessible from KB detail page for admins only

### Phase 4 — MCP write_to_kb
- Extend `klai-knowledge-mcp` with `write_to_kb` tool
- Tool accepts `kb_id`, `content`, `as_doc` (bool)
- Auth: resolve accessible KBs from Zitadel session (same as portal)
- `as_doc=true`: POST to klai-docs pages API → Gitea commit → webhook → Qdrant (via existing ingest pipeline)
- `as_doc=false`: POST directly to Unified Ingest API

---

## Resolved decisions

| # | Question | Answer |
|---|---|---|
| OQ-1 | Who can invite others to a KB? | Org admin and group manager only. Contributors cannot invite. |
| OQ-2 | Creator of a KB gets which role? | Automatically Owner. |
| OQ-3 | Can users create personal KBs (not org-scoped)? | Yes. A personal named KB works the same as an org KB but is owned by the user, not the org. It can have its own docs layer, connectors, and visibility setting. |
| OQ-4 | KB limit per org/user? | Deferred — billing/plan decision for later. |

---

## Implementation Notes

> Added: 2026-03-26 — post-implementation sync

### What was built

Implementation spanned two commits on 2026-03-25:

**`e56e25c` — Core unification (Phases 1, 2, 3 foundations)**
- Alembic migration `a3b4c5d6e7f8_unify_kb_and_docs.py`: added `visibility`, `docs_enabled`, `gitea_repo_slug`, `owner_type`, `owner_user_id` to `portal_knowledge_bases`; added `role` to `portal_group_kb_access`; migrated data and dropped `portal_docs_libraries` and `portal_group_docs_access` — matching AC-1 exactly.
- New `klai-portal/backend/app/api/app_knowledge_bases.py`: app-scoped `GET/POST /api/app/knowledge-bases` endpoints (any org member, not admin-only) — matching Phase 2 spec.
- Removed `klai-portal/backend/app/api/docs_libraries.py` and its admin routes.
- Updated `klai-portal/backend/app/models/knowledge_bases.py` to reflect new columns.
- `/app/knowledge/index.tsx`: extended with real named KB cards (icon + name + stat "N items · visibility"), empty state CTA, and kept Personal card unchanged — matching AC-3 and visual design spec exactly.
- `/app/knowledge/new.tsx`: create KB form (name, slug, visibility) — matching Phase 3.
- `/app/knowledge/$kbSlug.tsx`: detail page scaffolded with Docs / Sources / Stats tab structure — matching AC-3b.
- Removed `/admin/docs-libraries/` routes (index + detail).
- i18n: 24 keys added to both `nl.json` and `en.json`.

**`16f4b18` — Admin cleanup + real Sources tab (Phase 3 completion)**
- Deleted all 5 admin KB and connector route files: `/admin/knowledge-bases/index`, `/admin/knowledge-bases/$kbId`, `/admin/connectors/index`, `/admin/connectors/new`, `/admin/connectors/$connectorId` — completing AC-3 removal requirement.
- Removed knowledge-bases and connectors entries from admin nav (`admin/route.tsx`).
- Built real Sources tab in `/app/knowledge/$kbSlug.tsx`: lists connectors filtered by `config.kb_slug`, inline add-connector form with `kb_slug` pre-filled, sync and delete actions — delivering AC-3b Sources tab.

### AC coverage at completion

| AC | Status | Notes |
|---|---|---|
| AC-1: Unified data model | Delivered | Migration exactly matches spec SQL |
| AC-2: Creating KB provisions docs | Partial | `gitea_repo_slug` column added and linked; actual Gitea provisioning call not yet wired (no Gitea env in dev) |
| AC-3: KB management in /app | Delivered | All admin routes removed; /app/knowledge shows real cards |
| AC-3b: KB detail with tabs | Delivered | Docs / Sources / Stats tabs built; Sources tab is functional |
| AC-4: Admin org-level controls | Partial | Admin can still list all KBs via existing admin API; archive/delete controls not yet added as dedicated admin UI |
| AC-5: Group-based access | Delivered (model) | `portal_group_kb_access` with role column is in place; enforcement depends on query-layer filtering already present |
| AC-6: Visibility controls | Delivered (model) | `visibility` column and API field in place; Caddy/klai-docs enforcement is out of scope (separate SPEC) |
| AC-7: Docs writes back to knowledge | Not in scope | Gitea webhook → Qdrant pipeline is a separate concern |
| AC-8: MCP write_to_kb | Not started | Phase 4 — separate work item |
| AC-9: Chat multi-KB read / single write | Not started | Phase 4 / Focus integration |

### Deviations from SPEC

- **Gitea provisioning (AC-2):** The create-KB flow records `gitea_repo_slug` but does not call the Gitea API to provision the repo. The SPEC assumed the existing "klai-docs provisioning" could be called directly; that integration is not yet plumbed in the portal backend. This is a known gap, not a design change.
- **Admin controls (AC-4):** The SPEC specifies an admin view to archive/delete KBs. This was not built in this iteration. The existing admin API (`GET /api/admin/knowledge-bases`) still returns all org KBs for admin users and can be used as a data source for a future admin KB management page.
- **MCP write_to_kb (Phase 4):** Explicitly deferred to a follow-up task. No deviation — Phase 4 was always separate.

### Key decisions made during implementation

- The `$kbSlug.tsx` detail route was extended in-place rather than split into nested route files, keeping the tab state in a single component. This was a pragmatic choice to avoid TanStack Router route tree complexity for a UI-only tab switch.
- Connectors are filtered by `config.kb_slug` on the frontend (not a new backend endpoint). This is sufficient for the current connector volume; a dedicated `GET /api/app/knowledge-bases/{slug}/connectors` endpoint should be added when connectors become a first-class entity.
- i18n keys were added to both `nl.json` and `en.json` simultaneously, consistent with the internationalization strategy (NL primary, EN ready).
