---
id: SPEC-PORTAL-ADMIN-UI-001
version: "0.2.0"
status: ready-for-run
created: 2026-05-05
updated: 2026-05-05
author: Mark Vletter
priority: high
related:
  - SPEC-PORTAL-RBAC-001 (backend RBAC model — DONE)
  - SPEC-PORTAL-PROFILES-001 (5-rung profile ladder — DONE)
  - SPEC-AUTH-008 (groups + memberships — KEPT, scope narrowed to KB-team scoping)
---

# SPEC-PORTAL-ADMIN-UI-001: Admin UI — Users, Profiles, Groups

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-05 | 0.1.0 | Initial draft after the post-RBAC-001 UX feedback. Three-sidebar model (Users / Profiles / Groups). Driven by competitive research on Linear, Notion, GitHub, Slack. |
| 2026-05-05 | 0.2.0 | Sparring resolved with Mark. All open questions answered. Ready for /klai:auto. |

### Sparring decisions (v0.2.0)

| # | Vraag | Keuze |
|---|---|---|
| 1 | Inline profielwissel in users-lijst | Geen inline. Edit-knop in de rij navigeert naar bestaande user-edit pagina; profielwissel gebeurt daar via de bestaande radio-card profile-picker. |
| 2 | Naam van nieuwe pagina | `Profiles` (matcht bestaande UI-terminologie). |
| 3 | Layout van Profiles pagina | Volg `/admin/groups` 1-op-1 als template: lijst-view + detail-view per item. Geen aparte ontwerp-overweging. |
| 4 | Default profiel bij invite | `personal`. Bestaande dropdown-positie behouden, opties vervangen door 5 profielen, label "Role" → "Profile". |
| 5 | Search/filter op users-lijst | Standaard search-Input boven de tabel volgens TranscriptionTable.tsx-patroon. Client-side filter op naam + email. Geen filter-chips. |
| 6 | Groups detail-pagina | Niet aanraken. Bestaande functionaliteit voor KB-team-scoping (gebruikt op `/app/knowledge/<kb>/members`) blijft zoals het is. |

---

## Summary

After SPEC-PORTAL-RBAC-001 collapsed the backend model to three concepts (workspace features = plan ∪ addons; user permissions = profile rank; groups = KB-access scoping), the admin UI was left in a confused state:

- `/admin/users/invite` still uses a "member / admin" dropdown — the legacy pre-ladder values that the backend no longer accepts.
- `/admin/users` (list) shows a "member / admin" badge per row — same legacy mismatch. A Knowledge manager and a Personal chat user render identically.
- `/admin/users/<id>/edit` has the new 5-rung profile picker AND a groups section, with no clarification that they govern different things.
- `/admin/groups` is now empty for most tenants (since the system_groups got truncated by the RBAC-001 migration) which feels broken even though the backend works.
- Wording mengt "role" en "profile" door drie schermen heen voor één DB-veld.

This SPEC re-aligns the admin sidebar with the canonical model used by Linear / Notion / GitHub / Slack (members + roles + groups separated), adds a Klai-specific "Profiles" batch-management view, and fixes the wording inconsistency.

After this SPEC ships:

```
Sidebar (admin):
  Overview
  Users         ← members table — primary view, inline profile change
  Profiles      ← role-oriented batch view — 5 profiles, drill into members per profile
  Groups        ← KB-team scoping — custom groups only (system_key IS NULL)
  Billing, API keys, Chat widgets, Templates, MCPs, Settings, Danger zone
```

---

## Motivation

1. **Production incident 2026-05-04**: Mark reported "the toggle didn't work, the sidebar still shows it" — root cause was layered (backend cache + UI confusion), but the symptom surfaced because the UI for managing members/profiles was in a half-migrated state. Two follow-up incidents (#304, #307) pile-on.

2. **Industry-standard alignment**: every comparable B2B SaaS (Linear, Notion, GitHub, Slack, Auth0) presents members + groups as separate sidebar items, with roles inline-editable in the members table. Klai today has neither — invite uses legacy values, list shows wrong badges.

3. **Mental model from sparring** (Mark): "profiles bepaalt welke tools je mag gebruiken; groups bepaalt welke rechten binnen die tools (welke KB's)". This matches the RBAC-001 backend exactly. Surface the same separation in the UI.

4. **Wording cleanup**: "role" vs "profile" is the same DB field but rendered as different concepts on different pages. Pick one — "Profile" — and use everywhere in the UI. DB column stays `role` (no migration).

---

## Scope

### In scope

**Sidebar — three top-level items, one purpose each**

- `Users` — primary members table. Default landing for member management.
- `Profiles` — role-oriented batch view. New page.
- `Groups` — KB-team scoping. Existing page, copy + filter tightened.

**`/admin/users` (members table) redesign**

- Columns: `Name (avatar + name)` | `Email` | `Profile` | `Status` | `Last active` | `Actions`
- `Profile` column: badge with profile name, **inline editable** via overflow-menu (`⋯`) → submenu with the 5 profiles. Pattern from Linear (overflow-menu → modal/submenu) — matches their density and keyboard-first style. Notion uses an inline dropdown which is slightly faster but visually busier; for Klai's "calm" brand-DNA the overflow-menu pattern fits better.
- Filter chips above the table: `All | Personal chat | Company chat | Knowledge manager | Group manager | Admin | Suspended`. Click a chip to filter; multi-select for combined filter.
- No "Role" badge column (replaced by Profile).
- Suspend / offboard remain as overflow-menu actions, not as columns.

**`/admin/profiles` (new page)**

- Top-level list: 5 profile rows in ladder order. Each row shows profile name, short description (the `profile_*_description` i18n string), and member count.
- Click a profile row → drill-in view: list of members in that profile.
- Each member in the drill-in: avatar + name + email + "Move to..." overflow action. "Move to..." opens a submenu with the other 4 profiles. Single click → `PATCH /api/admin/users/<id>/role` → row moves to the new profile's count.
- Bulk action: checkbox-select multiple members → "Move selected to..." button. Calls the same endpoint per user (no bulk endpoint needed for v1).
- This is the view that replaces the muscle-memory of the old `/admin/groups` (which showed the 5 role_* groups + 2 addon_* groups). Mark explicitly asked to keep this functionality. Industry-standard pattern is filter-chips on the members table, but a dedicated batch view is a Klai-specific addition that costs little and helps power-admins.

**`/admin/groups` (custom groups only)**

- Already filters on `system_key IS NULL` after RBAC-001. Cosmetic-only changes:
  - Empty-state copy: "No groups yet. Create one to give a team access to specific knowledge bases."
  - Page subtitle: "Groups scope which knowledge bases a team can use. To assign profiles, go to Profiles."
- No structural change — the existing list-view + detail-view + members-list works fine for the custom KB-team use case.

**`/admin/users/<id>/edit`**

- Profile-picker (radio-card list with description) — already exists, no changes.
- Groups section — already filters on `system_key IS NULL`, no changes.
- Page header subtitle clarifies: "Profiles control what tools the user can use. Groups control which knowledge bases the user can access within those tools."

**`/admin/users/invite`**

- Replace the `<Select>` "member / admin" dropdown with the same radio-card profile picker used on user-edit.
- Default selection: `company` (most common onboarding rung; admin opt-in stays explicit).
- Form layout: name fields → email → profile picker (full width, with descriptions) → language → submit.

**Wording**

- All UI labels: "Profile" (English) / "Profiel" (Dutch). Never "Role" in UI.
- DB column `portal_users.role` stays — no migration. Only UI strings change.
- API endpoints stay (`PATCH /api/admin/users/<id>/role`) — no breaking change for any caller.

**i18n strings to add or rename**

- New: `admin_profiles_title`, `admin_profiles_description`, `admin_profiles_member_count`, `admin_profiles_move_to`, `admin_profiles_drill_in_title`, `admin_profiles_bulk_move_button`, `admin_profiles_empty_state` (per profile).
- Add (if absent): `admin_users_field_profile`, `admin_users_filter_*` for each profile + suspended.
- Keep: `admin_users_role_admin/member` strings for backward compat in any CLI tooling, but stop using them in UI.

### Out of scope

- DB schema changes (none — column stays `role`, value-set stays the 5 profiles per RBAC-001).
- Backend endpoint changes (`PATCH /role` already accepts the 5 profiles; no new endpoints needed for the bulk-move — frontend loops if user selected multiple).
- Mobile-responsive redesign of these pages (existing breakpoints stay).
- Permissions to ASSIGN profiles (admin-only stays as-is — only org admins can change anyone's profile).
- Re-introducing system_groups in the database. The Profiles page reads from `portal_users.role` directly, no group-membership intermediary.

---

## Requirements (EARS)

**REQ-1**: WHEN an admin views `/admin/users` THEN the table SHALL display the columns Name, Email, Profile, Status, Last active, Actions in this order. The Profile column SHALL show the user's `portal_users.role` value rendered as the matching `profile_*_label` i18n string.

**REQ-2**: WHEN an admin clicks the overflow menu on a user row in `/admin/users` THEN a submenu SHALL appear with "Change profile to..." → submenu of the 5 profiles, plus "Suspend / Reactivate" (depending on current status) and "Offboard". Selecting a different profile SHALL trigger `PATCH /api/admin/users/<id>/role` with the new value.

**REQ-3**: WHEN an admin views `/admin/users` THEN filter chips SHALL appear above the table for each of the 5 profiles plus "Suspended". Multiple chips MAY be active simultaneously (OR-filter). The default state SHALL be no chip active (= show all).

**REQ-4**: WHEN an admin opens `/admin/profiles` THEN the page SHALL list 5 rows in ladder order, each showing the profile label, the profile description, and a numeric count of users currently on that profile (from a single SELECT against `portal_users` grouped by role).

**REQ-5**: WHEN an admin clicks a profile row in `/admin/profiles` THEN a drill-in view SHALL list every user currently on that profile, with avatar, name, email, and a "Move to..." overflow action.

**REQ-6**: WHEN an admin selects "Move to..." → another profile in the `/admin/profiles` drill-in THEN the system SHALL call `PATCH /api/admin/users/<id>/role` with the new profile, and on success move the row to the new profile's drill-in.

**REQ-7**: WHEN an admin checks multiple members in the drill-in THEN a "Move selected to..." button SHALL appear with a profile-picker submenu. Selecting a target profile SHALL call `PATCH /api/admin/users/<id>/role` once per selected user (frontend loop), and on completion refresh the count and the drill-in list.

**REQ-8**: WHEN an admin opens `/admin/groups` THEN the page SHALL list only groups where `system_key IS NULL` and SHALL show the subtitle "Groups scope which knowledge bases a team can use. To assign profiles, go to Profiles."

**REQ-9**: WHEN an admin opens `/admin/users/invite` THEN the form SHALL include a radio-card profile picker (the same component used on `/admin/users/<id>/edit`) with the 5 profile options and their descriptions. The default-selected option SHALL be "Company chat".

**REQ-10**: WHERE the UI surfaces a user's profile role THEN the label SHALL be "Profile" / "Profiel". The string "Role" / "Rol" SHALL NOT appear as a label or heading in any admin UI surface.

**REQ-11**: WHEN the admin sidebar renders THEN it SHALL include the entries `Users`, `Profiles`, `Groups` as separate items in this order, between Overview and Billing.

---

## Acceptance Criteria

**AC-1**: `/admin/users` shows the new columns, filter chips work (single + multi-select), inline profile change via overflow menu actually updates the user's profile and refreshes the row badge.

**AC-2**: `/admin/profiles` lists 5 profiles with counts. Voys tenant counts match `SELECT role, count(*) FROM portal_users WHERE org_id=<voys> GROUP BY role`.

**AC-3**: Drill into "Company chat" → all current Company chat users are listed. Clicking "Move to → Knowledge manager" on one user moves them in the UI without a page reload, and the API call hits `PATCH /api/admin/users/<id>/role`.

**AC-4**: Bulk-select 3 users → "Move selected to → Personal chat" — three API calls fire, all succeed, drill-in refreshes with the three users now removed from the Company chat list.

**AC-5**: `/admin/groups` shows the empty state copy on a tenant with no custom groups, and the "Support" group on getklai.

**AC-6**: `/admin/users/invite` form has 5 radio-cards for profile, defaults to "Company chat". Submitting with each profile creates a user with that profile.

**AC-7**: `git grep -i 'role'` in `klai-portal/frontend/src/routes/admin/` returns no UI string labels (only DB-field references and type names where unavoidable). All visible labels say "Profile".

**AC-8**: Sidebar order is Overview, Users, Profiles, Groups, Billing, ... — verified visually on each tenant.

---

## Technical approach

### Frontend

- New file `klai-portal/frontend/src/routes/admin/profiles/index.tsx` — top-level list view.
- New file `klai-portal/frontend/src/routes/admin/profiles/$profile.tsx` — drill-in detail view (`profile` is the path param: `personal`, `company`, etc.).
- Modified `klai-portal/frontend/src/routes/admin/users/index.tsx` — add Profile column, filter chips, overflow-menu action for change-profile. Drop the legacy RoleBadge.
- Modified `klai-portal/frontend/src/routes/admin/users/invite.tsx` — replace dropdown with radio-card profile picker. Reuse the component from user-edit (extract to `_components/ProfilePicker.tsx` so it's shared).
- Modified `klai-portal/frontend/src/routes/admin/groups/index.tsx` — empty-state copy, page subtitle.
- Modified sidebar nav (likely `klai-portal/frontend/src/components/layout/AdminSidebar.tsx` — verify path during impl).

### Component extraction

- `_components/ProfilePicker.tsx` — radio-card list with profile + description + selected state. Used by:
  - User-edit (existing radio list, refactored to use this)
  - Invite (new)
  - Profiles drill-in "Move to..." submenu (compact variant)

### Backend

- No changes. `PATCH /api/admin/users/<id>/role` already accepts the 5 profiles. `GET /api/admin/users` already returns `role` per user.
- Optional optimisation (not required): a `GET /api/admin/profiles/counts` endpoint that returns `{personal: 3, company: 12, kb_manager: 2, group_manager: 1, admin: 1}` in one query, instead of the frontend computing counts from the users list. Defer to v0.2.0; v0.1.0 computes counts client-side from the existing users-list response.

### Tests

- Component test: `ProfilePicker` renders 5 cards with correct labels and selection state.
- Component test: filter-chips on `/admin/users` correctly filter the table.
- Component test: overflow-menu on user row triggers profile-change mutation with correct payload.
- E2E (J05-profiles-batch-move.spec.ts in prod-tenant suite, runs in CI when secrets are refreshed): login → /admin/profiles → drill into a profile → move a user → verify count update on parent view.

### Performance

- No N+1 queries. The users-list endpoint already returns `role` per user, so the Profiles page count is `O(users)` in the browser, not a DB roundtrip per profile.
- Filter chips: client-side filter on the already-fetched users list. No extra fetches.

---

## Out-of-scope follow-ups (separate SPECs)

- Bulk-move endpoint (`PATCH /api/admin/users/bulk-role` accepting a list) for tenants with hundreds of users where N HTTP calls becomes slow. Wait for actual scale need.
- Mobile-responsive redesign of the admin section.
- Audit-log surface in the admin UI showing "Mark moved Lisa from Company to KB manager at 2026-05-05 14:23" — useful for compliance, separate SPEC.

---

## Open questions

Alle open vragen uit v0.1.0 zijn beantwoord — zie de sparring-decisions tabel bovenaan. Geen open einden meer voor v0.2.0.

---

## Estimated effort

- Frontend: ~350 LOC nieuw, ~80 LOC verwijderd. Net positief ~270 LOC.
- Component extraction: 1 nieuw shared component (~60 LOC).
- Tests: 4-6 component tests, 1 E2E test (E2E pas nuttig na CI secrets refresh).
- Backend: 0 LOC.
- DB: 0 migrations.
- Calendar: 1 werkdag voor implementatie + tests + lokaal verifiëren.

---

## Definition of done

- All REQ-1 t/m REQ-11 implemented, covered by component tests.
- All AC-1 t/m AC-8 verifiable on the Voys tenant after deploy.
- `git grep -in '"role"' klai-portal/frontend/src/routes/admin/` returns only DB-field references (no visible UI labels).
- Sidebar reordered, three items in correct positions, on every tenant.
- Mark verifies the E2E flow on Voys: invite a test user as Personal chat → see them in `/admin/users` with the right badge → on `/admin/profiles` see them in the Personal chat drill-in → batch-move them to Company chat → see the change reflect in both views.
