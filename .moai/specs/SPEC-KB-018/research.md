# Research: SPEC-KB-018 — Knowledge Base Sharing & Access Wizard

## Research Summary

Deep research conducted via three parallel exploration agents covering:
1. KB data model, API endpoints, and docs generation
2. Zitadel integration for user/group management
3. Frontend UX flow and component patterns

---

## 1. Current KB Model

**File:** `klai-portal/backend/app/models/knowledge_bases.py`

### PortalKnowledgeBase fields:
- `id`, `org_id`, `name`, `slug`, `description`
- `visibility` (Text, default "internal") — values: `internal`, `public`, `private`
- `docs_enabled` (Boolean, default true) — triggers Gitea repo provisioning
- `gitea_repo_slug` (nullable)
- `owner_type` (Text, default "org") — values: `org`, `user`
- `owner_user_id` (nullable) — set when `owner_type == "user"`

### Access Models (already exist):

**PortalUserKBAccess:**
- `kb_id`, `user_id` (Zitadel ID), `org_id`, `role`, `granted_at`, `granted_by`
- Roles: `"viewer"`, `"contributor"`, `"owner"`
- Unique constraint: `(kb_id, user_id)`

**PortalGroupKBAccess:**
- `kb_id`, `group_id`, `role` (default `"viewer"`), `granted_at`, `granted_by`
- Unique constraint: `(group_id, kb_id)`

### Role Resolution (access.py):
- Checks direct user access + all group memberships
- **Highest role wins** across all sources
- Role rank: viewer=1, contributor=2, owner=3

---

## 2. Current Creation Flow

### Backend (app_knowledge_bases.py):
1. Create `PortalKnowledgeBase` row
2. Add creator as `"owner"` in `PortalUserKBAccess`
3. Provision Gitea docs repo if `docs_enabled=true`
   - Portal "internal" maps to docs "private"
   - Portal "public" maps to docs "public"
4. Sync visibility to knowledge-ingest (Qdrant)

### Frontend (knowledge/new.tsx):
Current form collects only:
- **Scope** — org or personal (2-button picker)
- **Name** — with auto-slug
- **Visibility** — internal or public (only for org scope)

**Missing:** No sharing step, no group/user selection, no default role configuration.

---

## 3. Members Tab (exists, disconnected from creation)

**File:** `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/members.tsx`

- Two sections: users and groups
- Inline invite forms (email + role for users, group picker + role for groups)
- Role options: viewer / contributor / owner
- Default invite role: `"viewer"`
- Owner-only: all member mutations require owner role

---

## 4. Zitadel Integration

- **Identity only** — Zitadel handles auth (OIDC, MFA), not resource-level permissions
- **Portal-side groups** — `PortalGroup` lives in PostgreSQL, not Zitadel
- **Group membership** — `PortalGroupMembership` table
- **Admin panel** — full CRUD for users (`/admin/users/`) and groups (`/admin/groups/`)
- **Live identity fetch** — portal stores only `zitadel_user_id`, fetches name/email from Zitadel

---

## 5. Identified Gap: default_org_role

Current behavior:
- Public/Internal KB: all org members can **see** the KB (implicit via visibility check)
- But there are no `PortalUserKBAccess` rows for implicit viewers
- No way to say "all org members = contributor" without bulk-inserting rows

**Solution agreed with user:** Add `default_org_role` field to `PortalKnowledgeBase`:
- Checked as fallback in `access.py` when no explicit access row exists
- Values: `"viewer"` (default), `"contributor"`, `None` (for restricted KBs)
- Avoids bulk-insert scalability issues

---

## 6. Architecture Decisions (confirmed with user)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Permission storage | Portal-side (PostgreSQL) | Zitadel = identity, portal = authorization |
| Org-wide default role | `default_org_role` field on KB | No bulk-inserts, scales well |
| Override mechanism | Existing access tables + highest-wins | Already built and proven |
| UI pattern | Show only exceptions on the default | Simple, not overwhelming |
| Page-level permissions | Future scope (restriction model) | KB-level is ceiling, page-level narrows |
| Docs visibility sync | Automatic from KB visibility | Already works, make visible in UI |

---

## 7. UI Patterns Available

| Pattern | Example | Reuse for |
|---------|---------|-----------|
| 2-button picker | Scope selector in new.tsx | Visibility card selection |
| Inline expand form | Member invite in members.tsx | Quick group/user add in wizard |
| Card grid | KB list cards | Visibility option cards |
| AlertDialog | Delete confirmation | Role change confirmation |
| Tab navigation | KB detail tabs | Wizard steps (if multi-step) |
| Role badge | members.tsx roleBadge() | Role display in sharing UI |

---

## 8. Docs-Side Limitations

- Docs-app has **no member management** — only binary public/private
- No per-user roles on docs side
- Visibility is KB-level only
- Page-level permissions would require new `PortalDocAccess` table (future)

---

## 9. Translation Keys

Existing namespace: `knowledge_new_*` (creation), `knowledge_members_*` (members tab)
New namespace needed: `knowledge_sharing_*` for the sharing wizard step
