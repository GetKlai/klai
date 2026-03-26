# SPEC-KB-003: Knowledge Base App Layer

> Status: COMPLETED — 2026-03-26
> Author: Mark Vletter (design) + Claude (SPEC)
> Supersedes: SPEC-KB-001-unification.md (remaining open items)
> Builds on: SPEC-KB-002-integration.md (already implemented)

---

## What KB-002 already implemented

Do not re-implement these — they are done:

- `portal_knowledge_bases` has `visibility`, `docs_enabled`, `gitea_repo_slug`, `owner_type`, `owner_user_id`
- `portal_group_kb_access` has `role` column (viewer / contributor / owner)
- `portal_connectors` table exists as child of KB
- `portal_docs_libraries` and `portal_group_docs_access` no longer exist
- Qdrant visibility tagging and filtering at retrieval
- MCP `write_to_kb` tool replacing `save_org_knowledge`
- `/app/knowledge/new` creates a KB (verify Gitea provisioning — see AC-1)
- `/app/docs` no longer has its own "new KB" creation flow

---

## What this SPEC adds

1. KB detail page (`/app/knowledge/$kbSlug`) as an overview dashboard
2. Connectors UI moved from admin into the KB detail page
3. Role management UI — invite groups and individual users to a KB
4. Personal named KBs visible in `/app/knowledge`
5. `/app/docs` coupled to portal KBs — shows the same KB list, adds access request
6. New data model: `portal_user_kb_access` for individual KB access
7. Admin cleanup: remove `/admin/connectors` once connectors are in the app

---

## Design Decisions

### D1: /app/docs is a view on portal KBs

`/app/docs` stays as a navigation item. It shows the same KBs as `/app/knowledge`, filtered to `docs_enabled = true`. The list is driven by `portal_knowledge_bases` via the portal API — not by the klai-docs API directly.

A user sees:
- KBs they have access to → clickable, opens the docs editor (existing behaviour)
- KBs that exist but they cannot access → visible but locked, with a "Request access" action

### D2: Access requests are parked as a future sub-task

The "request access" feature requires researching the industry-standard notification pattern (in-app notification vs email vs admin queue). This SPEC defines the trigger point (the locked KB card in `/app/docs`) but does not implement the notification flow. That is a separate sub-task requiring research first.

### D3: KB detail page is an overview dashboard

`/app/knowledge/$kbSlug` is not a tabbed editor — it is a dashboard. It shows at a glance:

- Linked docs: is there a docs layer? How many pages?
- Connectors: which connectors are connected, last sync status
- Volume: how many items are indexed in Qdrant
- Usage: how often is this KB queried (from chat / Focus)

From this page, the KB Owner can manage connectors and members. Viewers and Contributors see the stats but cannot manage.

### D4: Connectors are managed per KB, not per org

Connectors move from `/admin/connectors` to the KB detail page. Only the KB Owner can add, edit, or delete connectors for a KB. The admin connectors route is removed once this is in place.

### D5: Two access assignment methods, one access check

KB membership can be granted in two ways:
- **Via group**: KB Owner assigns a portal group to the KB with a role → `portal_group_kb_access`
- **Via individual**: KB Owner invites a specific user with a role → `portal_user_kb_access` (new table)

At runtime, access is checked against both tables:
```
Has user X access to KB Y?
  → member of a group with access to KB Y?  (portal_group_kb_access)
  → OR direct individual assignment?         (portal_user_kb_access)
```

Zitadel is the source of truth for group memberships. KB-specific roles live in the portal DB because Zitadel has no concept of "Contributor on KB X".

### D6: Who can manage KB membership

| Who | Can do |
|-----|--------|
| KB Owner | Invite/remove groups and individuals, change roles, change KB settings, delete KB |
| Org admin | All of the above for any KB in the org |
| Group manager | Manage group-level KB access for groups they manage |
| Contributor | Cannot manage membership |
| Viewer | Cannot manage membership |

Multiple Owners per KB are allowed.

### D7: Personal named KBs are private

A user can create a personal KB (`owner_type = 'user'`). Personal KBs:
- Are visible only to their owner
- Cannot be shared with others (no group or individual access assignments)
- Appear in a separate section in `/app/knowledge` below org KBs
- Can have docs enabled and connectors (owned by the same user)

---

## Acceptance Criteria

### AC-1: Verify Gitea provisioning on KB creation

**WHEN** a user creates a new KB via `/app/knowledge/new`,
**THEN** the portal backend calls the klai-docs API to provision a Gitea repo,
**AND** `gitea_repo_slug` is set on the `portal_knowledge_bases` row after creation,
**AND** the KB is accessible in `/app/docs` immediately after creation.

**IF** Gitea provisioning is not yet implemented in the current code,
**THEN** this must be implemented as Phase 1 of this SPEC.

### AC-2: /app/knowledge shows personal and org KBs separately

**WHEN** a user navigates to `/app/knowledge`,
**THEN** they see the existing Personal knowledge base card (unchanged),
**AND** they see org KBs they have access to (one card each),
**AND** if they have personal named KBs, those appear in a separate section below org KBs,
**AND** a "Create knowledge base" action is available for both org and personal scope.

### AC-3: KB detail page shows overview dashboard

**WHEN** a user navigates to `/app/knowledge/$kbSlug`,
**THEN** they see:
  - Docs section: whether docs is enabled, page count, link to `/app/docs/$kbSlug`
  - Connectors section: list of connected connectors with name, type, last sync status
  - Volume: total indexed item count in Qdrant for this KB
  - Usage: query count (last 30 days)
**AND** KB Owner sees management actions (add connector, manage members, KB settings).

### AC-4: Connectors managed from KB detail page

**WHEN** a KB Owner is on the KB detail page,
**THEN** they can add a new connector (type: GitHub; others shown as "coming soon"),
**AND** they can edit or delete an existing connector,
**AND** they can trigger a manual sync.

**WHEN** a non-Owner visits the same page,
**THEN** they see connector status (read-only) but no add/edit/delete actions.

### AC-5: Role management UI on KB detail page

**WHEN** a KB Owner opens the Members section of the KB detail page,
**THEN** they see a list of current members (groups and individuals) with their roles,
**AND** they can invite a group (from the org's portal groups) with a chosen role,
**AND** they can invite an individual user (by email or name) with a chosen role,
**AND** they can change the role of an existing member,
**AND** they can remove a member,
**AND** available roles are: Viewer, Contributor, Owner.

### AC-6: /app/docs shows portal KBs with access state

**WHEN** a user navigates to `/app/docs`,
**THEN** the list is fetched from the portal API (not from klai-docs directly),
**AND** KBs they have access to are shown as before (clickable, opens editor),
**AND** KBs that exist in the org but they cannot access are shown as locked cards,
**AND** locked cards have a "Request access" action (UI only — notification not yet implemented, see D2).

### AC-7: Personal named KB creation

**WHEN** a user creates a new KB and selects "Personal" as scope,
**THEN** `owner_type = 'user'` and `owner_user_id` is set to their Zitadel user ID,
**AND** the KB does not appear in any other user's KB list,
**AND** the membership management section is hidden (personal KBs cannot be shared).

### AC-8: Admin connectors route removed

**WHEN** the connectors UI is live under `/app/knowledge/$kbSlug`,
**THEN** `/admin/connectors` and `/admin/connectors/$connectorId` routes are removed,
**AND** any direct navigation to those URLs returns 404 or redirects to `/app/knowledge`.

### AC-9: portal_user_kb_access enforced at API level

**WHEN** a request is made to any KB-scoped API endpoint,
**THEN** the access check queries both `portal_group_kb_access` (via user's group memberships) AND `portal_user_kb_access` (direct assignments),
**AND** the highest role from either source is used.

---

## Data Model Changes

### portal_user_kb_access (new)

```sql
CREATE TABLE portal_user_kb_access (
  id          SERIAL PRIMARY KEY,
  kb_id       INTEGER NOT NULL REFERENCES portal_knowledge_bases(id) ON DELETE CASCADE,
  user_id     TEXT NOT NULL,  -- zitadel_user_id
  org_id      INTEGER NOT NULL REFERENCES portal_orgs(id) ON DELETE CASCADE,
  role        TEXT NOT NULL CHECK (role IN ('viewer', 'contributor', 'owner')),
  granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  granted_by  TEXT NOT NULL,
  UNIQUE (kb_id, user_id)
);
```

No other schema changes — all other required columns are already in place from KB-002.

---

## What is NOT in scope

- Access request notification flow (research task, separate sub-task — see D2)
- Non-GitHub connector types (Google Drive, Notion — stay "coming soon")
- Focus multi-KB read scope
- Re-indexing on visibility change (async, separate task)
- Billing / KB limits per org or user
- Public docs site auth for private KBs (`{slug}.getklai.com/docs/{kb}/...` external URL stays unchanged)

---

## Implementation Phases

### Phase 1 — Verify and fix Gitea provisioning (if needed)
- Check whether `POST /api/app/knowledge-bases` calls klai-docs API and sets `gitea_repo_slug`
- If not: implement the provisioning call + rollback on failure
- Verify KB appears in `/app/docs` immediately after creation

### Phase 2 — portal_user_kb_access + access check
- Alembic migration: create `portal_user_kb_access` table
- Update `get_accessible_kb_slugs()` to check both tables
- Add API endpoints: `GET/POST/PATCH/DELETE /api/app/knowledge-bases/{kb_slug}/members`

### Phase 3 — KB detail page
- Build `/app/knowledge/$kbSlug` as overview dashboard
- Docs section: page count from klai-docs API, link to editor
- Connectors section: list from `portal_connectors`, sync status
- Volume: Qdrant point count for this KB
- Usage: query count from portal audit log or knowledge service

### Phase 4 — Connectors UI in app
- Add connector management actions to KB detail page (Owner only)
- Add/edit/delete connector forms (GitHub only for now)
- Manual sync trigger button
- Remove `/admin/connectors` and `/admin/connectors/$connectorId` routes

### Phase 5 — Role management UI
- Members section on KB detail page
- Invite group (dropdown from org groups) with role selector
- Invite individual (user search) with role selector
- Change role / remove member actions
- Respect Owner-only constraint: only Owner, org admin, group manager can manage

### Phase 6 — Personal named KBs
- Scope selector on `/app/knowledge/new` (Org / Personal)
- Personal KBs shown in separate section on `/app/knowledge`
- Hide membership management for personal KBs

### Phase 7 — /app/docs coupled to portal
- `/app/docs` fetches KB list from portal API instead of klai-docs API
- Add locked state for KBs user cannot access
- Add "Request access" button on locked cards (UI only, no backend yet)

---

## Resolved design decisions

| # | Question | Answer |
|---|---|---|
| Q1 | Does /app/docs stay as separate nav item? | Yes, stays — but fed from portal KB list |
| Q2 | Who manages connectors? | KB Owner only |
| Q3 | Individual invites: separate from groups? | Yes — via new portal_user_kb_access table |
| Q4 | Personal KBs: shareable? | No — private to owner only |
| Q5 | KB detail page: tabs or dashboard? | Dashboard overview (docs, connectors, volume, usage) |
| Q6 | Access request notifications: how? | Research needed — parked as future sub-task |
| Q7 | Admin connectors route: redirect or 404? | Remove — no redirect needed once connectors are in app |
| Q8 | Multiple KB Owners allowed? | Yes |
| Q9 | KB role access via Zitadel or portal DB? | Portal DB for KB-specific roles; Zitadel for group memberships |

---

## Implementation Notes

> Added: 2026-03-26

### What was built

All seven implementation phases were completed as specified. Key commits:

- `feat(portal): SPEC-KB-003 knowledge base app layer` — main implementation commit covering Phases 1-7 (Gitea provisioning, portal_user_kb_access, KB detail page, connectors UI, role management, personal KBs, /app/docs portal coupling)
- `fix(lint): ruff format app_knowledge_bases and access` — lint cleanup pass after main implementation
- `fix(admin): remove broken tiles (knowledge-bases, docs-libraries, connectors)` — AC-8 cleanup, admin connectors route removed
- `feat(security): harden knowledge base against 9 audit vulnerabilities` — security hardening applied to the KB surface area
- `feat(groups): group-admin rol + knowledge bases en docs libraries toegangsbeheer` — group-admin role + group-level KB and docs library access management (related prerequisite work)

### Deviations from SPEC

None recorded. All acceptance criteria (AC-1 through AC-9) were implemented as specified.

### Parked items (as designed)

- **Access request notification flow** (D2 / AC-6): The "Request access" button is present on locked KB cards in `/app/docs` as UI only. The notification backend is a separate future sub-task requiring research into in-app vs email vs admin queue patterns.
- **Non-GitHub connector types**: Google Drive, Notion remain "coming soon" as specified.
- **Focus multi-KB read scope**: Out of scope, not implemented.
- **Re-indexing on visibility change**: Async task, separate backlog item.

### Key decisions confirmed during implementation

- `portal_user_kb_access` table created exactly as modelled in the SPEC (no schema deviations).
- Access check queries both `portal_group_kb_access` (via Zitadel group memberships) and `portal_user_kb_access` (direct assignments); highest role wins.
- Admin connectors routes removed entirely (no redirect) per D4 / AC-8.
- Personal KBs (`owner_type = 'user'`) are not shareable; membership management section is hidden for personal KBs.
