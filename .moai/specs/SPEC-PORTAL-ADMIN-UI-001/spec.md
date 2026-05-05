---
id: SPEC-PORTAL-ADMIN-UI-001
version: "0.3.0"
status: in-progress-polish
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
| 2026-05-05 | 0.3.0 | Polish round after PR #317 deviated from Sparring decisions. v0.3.0 records ALL UI decisions explicitly so a follow-up implementer cannot "industry-standard" their way around them. Adds decisions for /admin/users/$userId/edit (was "no changes" in v0.2.0 — now: cards weg, groups weg, één save). Records what stays from PR #317 (users list view, backend min-1-admin invariant) and what gets reverted (invite radio-cards, users overflow change-profile submenu, profiles list ul-style, profiles drill-in bulk-move). |

### Sparring decisions (v0.3.0 — supersedes v0.2.0)

| # | Vraag | Keuze | Bron |
|---|---|---|---|
| 1 | Inline profielwissel in users-lijst | **HERZIEN per Mark 2026-05-05:** de huidige live state op getklai.getklai.com/admin/users (PR #317) met overflow ⋯ → "Change profile to..." submenu blijft ongewijzigd. Niet aanraken. | Mark, post-#317 review |
| 2 | Naam van nieuwe pagina | `Profiles` (matcht bestaande UI-terminologie). | v0.2.0 |
| 3 | Layout van Profiles pagina | **STRENG:** Letterlijke kopie van `klai-portal/frontend/src/routes/admin/groups/index.tsx` als startpunt voor `profiles/index.tsx`, en `groups/$groupId/index.tsx` voor `profiles/$profile.tsx`. Geen `<ul>` met chevrons, geen eigen layout. Description als sub-text in dezelfde Name-cel (niet als aparte kolom). | v0.2.0 + Mark verduidelijkt 2026-05-05 |
| 4 | Default profiel bij invite | `personal`. Bestaande **dropdown** (`<Select>`) blijft — geen radio-cards, geen ProfilePicker-component. Opties vervangen door 5 profielen, label "Role" → "Profile". | v0.2.0 |
| 5 | Search/filter op users-lijst | Standaard search-Input boven de tabel volgens TranscriptionTable.tsx-patroon. Client-side filter op naam + email. Geen filter-chips. | v0.2.0 |
| 6 | Groups index + detail | **NIET AANRAKEN.** Bestaande functionaliteit voor KB-team-scoping (gebruikt op `/app/knowledge/<kb>/members`) blijft zoals het is. Geldt ook voor de subtitle en empty-state van `/admin/groups` — die zijn in PR #317 aangepast en blijven als status quo, geen verdere wijzigingen. | v0.2.0 |
| 7 | `/admin/users/$userId/edit` layout | **NIEUW v0.3.0 (vervangt "no changes" uit v0.2.0):** ÉÉN form, ÉÉN save-knop. Geen meerdere `<Card>` wrappers met aparte saves. Velden in volgorde: First name + Last name, Invitation language, Profile (radio-cards behouden — visuele beschrijvingen waardevol), één primaire `[Save]` + secundaire `Cancel` onderaan. Lifecycle-acties (Suspend/Reactivate/Offboard) blijven onder de form als losse sectie met destructieve buttons (geen Save). | Mark 2026-05-05 |
| 8 | Groups-sectie op user-edit | **VERWIJDEREN.** Group-membership beheer gebeurt op `/admin/groups/$groupId/index.tsx` of via `/app/knowledge/<kb>/members`. Niet meer in user-edit. Header subtitle clarificeert: "Profiles control what tools the user can use. Groups control which knowledge bases the user can access within those tools." | Mark 2026-05-05 |
| 9 | Backend `PATCH /role` min-1-admin invariant | **BEHOUDEN** (toegevoegd in PR #317). Mirrors `POST /demote-admin` invariant. Voorkomt tenant-lockout via de unified change-profile flow. Niet rollbacken — security guard. | Mark accept 2026-05-05 |
| 10 | Profiles drill-in member-management UX | **STRENG:** Add member = aparte `/admin/profiles/$profile/add-member` route met dezelfde Popover+Command picker als `groups/$groupId/add-member.tsx`. Remove member = inline `InlineDeleteConfirm` op de rij; on confirm `PATCH /api/admin/users/<id>/role` met `{role: "personal"}` (demote naar laagste). **GEEN bulk-select**, GEEN checkboxes, GEEN "Move to ▾" submenu. | Mark 2026-05-05 |
| 11 | ProfilePicker shared component | **BEHOUDEN** alleen voor user-edit. Niet meer in invite (zie #4). v1-spine compliant: `border-gray-900 bg-black/[0.06]` selected, `border-gray-200` unselected, geen amber. | Mark 2026-05-05 |
| 12 | Klai v1-spine compliance | Alle nieuwe en gewijzigde files in deze SPEC houden zich aan `portal-patterns.md` v1-spine: `mx-auto max-w-3xl px-6 py-10` containers, `border-gray-200` literal voor tables/lists, `bg-black/5` hover + `bg-black/[0.06]` active, `text-gray-900` prose + `text-gray-400` muted, `rounded-full bg-gray-900` buttons, sentence-case, geen amber buiten focus-rings/logo. | Mark 2026-05-05 |
| 13 | Error-message scrubbing | `apiFetch` formatteert errors als `"{status}: {detail}"` — voor UI banners/toasts wordt de `"409: "`/`"404: "` prefix gestript. Helper in `_components/errors.ts::cleanErrorMessage(err, fallback)`. | Mark accept 2026-05-05 |

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

## Scope (v0.3.0)

### Implementation status across PR #317 + this polish round

| Surface | PR #317 status | Polish action |
|---|---|---|
| Sidebar order Overview · Users · Profiles · Groups · Billing | LIVE | Niet aanraken |
| `/admin/users` table + search + Profile column + overflow change-profile submenu | LIVE | Niet aanraken (Sparring #1, herzien — Mark accepteert huidige UI) |
| `/admin/users/invite` met radio-cards ProfilePicker + default `company` | LIVE | **Rollback:** terug naar `<Select>` dropdown, default `personal` (Sparring #4) |
| `/admin/profiles/index` met `<ul>` + chevrons | LIVE | **Volledig herschrijven** als 1-op-1 kopie van `groups/index.tsx` (Sparring #3) |
| `/admin/profiles/$profile` met bulk-select + Move-to ▾ | LIVE | **Volledig herschrijven** als 1-op-1 kopie van `groups/$groupId/index.tsx` met Add member + Remove member (Sparring #3, #10) |
| `/admin/profiles/$profile/add-member` (nieuwe route) | NIET aanwezig | **Nieuw** — kopie van `groups/$groupId/add-member.tsx` (Sparring #10) |
| `/admin/users/$userId/edit` met meerdere `<Card>`s + aparte saves + Groups-sectie | LIVE op main (al van vóór deze SPEC) | **Refactor:** één form, één save; Groups-sectie verwijderen; header subtitle (Sparring #7, #8) |
| `/admin/groups` subtitle + empty-state copy | LIVE | Niet aanraken (Sparring #6) |
| Backend `PATCH /role` met min-1-admin invariant | LIVE | Behouden — security guard (Sparring #9) |
| `_components/ProfilePicker.tsx` (v1-spine compliant, no amber) | LIVE | Behouden, gebruikt alleen in user-edit (niet in invite) |
| `_components/UserAvatar.tsx` | LIVE op polish-branch | Gebruikt in nieuwe `profiles/$profile.tsx` drill-in |
| `_components/errors.ts::cleanErrorMessage` | LIVE op polish-branch | Gebruikt in profiles/$profile + user-edit voor 409-prefix scrub |

### Polish-round files that change

**Refactor (rollback richting Sparring decisions):**
- `klai-portal/frontend/src/routes/admin/users/invite.tsx`
- `klai-portal/frontend/src/routes/admin/profiles/index.tsx`
- `klai-portal/frontend/src/routes/admin/profiles/$profile.tsx`
- `klai-portal/frontend/src/routes/admin/users/$userId/edit.tsx`

**New:**
- `klai-portal/frontend/src/routes/admin/profiles/$profile/add-member.tsx`

**Untouched in polish round:**
- `klai-portal/frontend/src/routes/admin/users/index.tsx` (Sparring #1 herzien)
- `klai-portal/frontend/src/routes/admin/groups/**` (Sparring #6)
- `klai-portal/frontend/src/routes/admin/route.tsx` (sidebar al goed)
- `klai-portal/backend/app/api/admin/users.py::update_user_role` (Sparring #9)

**Wording**
- All UI labels: "Profile" / "Profiel". Never "Role" / "Rol" in UI surfaces.
- DB column `portal_users.role` stays — no migration.
- API endpoint `PATCH /api/admin/users/<id>/role` stays — no breaking change.

**i18n strings**
- `admin_users_field_profile`, `admin_profiles_*` keys: al toegevoegd in PR #317. Hergebruiken.
- Geen nieuwe keys nodig in polish round (de `admin_profiles_bulk_*` en `admin_profiles_move_to` keys raken ongebruikt na rollback van bulk-select; mogen blijven staan voor toekomstige features).

### Out of scope

- DB schema changes (none).
- Bulk-move endpoint of bulk-select UI (Sparring #10 verwerpt bulk in v1).
- Mobile-responsive redesign.
- Permissions: alleen org-admin kan profielen aanpassen (al zo).
- Re-introducing system_groups in DB.
- Filter-chips of tabs op users-list (Sparring #5).
- "Last active" backend-veld; `created_at` (Invited) blijft de gerenderde waarde tot een aparte SPEC `last_active_at` toevoegt.

---

## Requirements (EARS — v0.3.0)

> v0.3.0 vervangt v0.1.0 REQ-1 t/m REQ-11. Sommige zijn al door PR #317 voldaan en blijven staan; andere zijn herzien of verworpen na Mark's polish-feedback. Status-kolom maakt expliciet wat de polish-implementer wel/niet moet aanraken.

| ID | Status | Requirement |
|---|---|---|
| REQ-1 | LIVE in #317 — niet aanraken | The `/admin/users` table SHALL display Name · Email · Profile · Status · Invited · Actions and SHALL render `portal_users.role` als de matching `profile_*_label` i18n string. |
| REQ-2 | LIVE in #317 — niet aanraken | The `/admin/users` row overflow ⋯ submenu SHALL include "Change profile to..." (with the 5 ladder targets), Suspend/Reactivate, and Offboard. Selecting a target SHALL trigger `PATCH /api/admin/users/<id>/role`. (Sparring #1 v0.2.0 verworpen door Mark in v0.3.0; huidige live UI blijft.) |
| ~~REQ-3~~ | VERWORPEN | Filter chips. Sparring #5 v0.2.0: alleen search-Input. PR #317 search-Input is voldoende. |
| REQ-4 | HERZIEN | The `/admin/profiles/index.tsx` page SHALL be a literal copy of `klai-portal/frontend/src/routes/admin/groups/index.tsx`'s structure — `useReactTable` + section-style `<table>` met `border-gray-200` + `divide-y` — adapted to render 5 statische rows in `PROFILE_LADDER` volgorde. Each row SHALL show the profile label as primary text, the profile description as sub-text in the same Name cell (`text-xs text-gray-400`), a numeric member count, and Edit + Eye action icons leading to `/admin/profiles/<role>`. No "Create" button. No Delete action. |
| REQ-5 | HERZIEN | The `/admin/profiles/$profile.tsx` drill-in SHALL be a literal copy of `groups/$groupId/index.tsx`'s structure. Header: `Back to profiles` link + profile label as h1 + profile description as muted paragraph. Members section: section-style `<table>` met `border-gray-200`, columns Name · Email · Joined-at · Actions. Each row uses the shared `<UserAvatar>` component. |
| REQ-6 | HERZIEN | The drill-in's "Add member" button SHALL navigate to `/admin/profiles/$profile/add-member` (a separate route, kopie van `groups/$groupId/add-member.tsx`). The picker SHALL only list users whose current `role` is NOT the target profile. On select + submit: `PATCH /api/admin/users/<id>/role` with body `{role: "<profile>"}`, then redirect back to the drill-in. |
| REQ-7 | HERZIEN | The drill-in's per-row Remove action SHALL use `<InlineDeleteConfirm>` met label `"Demote {name} to Personal chat?"`. On confirm: `PATCH /api/admin/users/<id>/role` met body `{role: "personal"}`. Geen bulk-select, geen "Move to ▾" submenu. (Sparring #10.) |
| REQ-8 | LIVE in #317 — niet aanraken | `/admin/groups` SHALL list only groups where `system_key IS NULL` met subtitle "Groups scope which knowledge bases a team can use. To assign profiles, go to Profiles." |
| REQ-9 | HERZIEN | `/admin/users/invite` SHALL gebruik een `<Select>` dropdown (geen radio-cards, geen ProfilePicker component). Opties: 5 ladder-profielen met `profile_<role>_label`. Default = `personal`. Label "Profile" (niet "Role"). (Sparring #4.) |
| REQ-10 | LIVE in #317 — niet aanraken | All UI labels surface "Profile" / "Profiel". The string "Role" / "Rol" SHALL NOT appear as a label or heading in any admin UI surface. |
| REQ-11 | LIVE in #317 — niet aanraken | Admin sidebar order: Overview · Users · Profiles · Groups · Billing · API keys · Chat widgets · Templates · MCPs · Settings · Danger zone. |
| REQ-12 | NIEUW v0.3.0 | `/admin/users/$userId/edit` SHALL be ONE form with ONE submit button. Geen meerdere `<Card>` wrappers met aparte saves. Velden in volgorde: First name + Last name, Invitation language, Profile (radio-cards via `<ProfilePicker>` shared component). Submit-knop onderaan: primary `[Save]` + secondary `Cancel`. Submit-handler stuurt `PATCH /api/admin/users/<id>` voor naam/taal en (alleen als profile gewijzigd) `PATCH /api/admin/users/<id>/role`. Lifecycle-acties (Suspend/Reactivate/Offboard) blijven onder de form als losse sectie met destructieve buttons (geen Save). |
| REQ-13 | NIEUW v0.3.0 | `/admin/users/$userId/edit` SHALL NOT contain a Groups-section. Group-membership beheer gebeurt op `/admin/groups/$groupId/index.tsx` of via `/app/knowledge/<kb>/members`. Alle group-staging code (`memberGroupIds`, `useQuery(['admin-user-groups', userId])`, `groupsToAdd`/`groupsToRemove`) wordt verwijderd uit `edit.tsx`. |
| REQ-14 | NIEUW v0.3.0 | `/admin/users/$userId/edit` page header SHALL include the subtitle: "Profiles control what tools the user can use. Groups control which knowledge bases the user can access within those tools." |
| REQ-15 | LIVE in #317 — behouden | Backend `PATCH /api/admin/users/<id>/role` SHALL refuse to demote the last admin met HTTP 409 + detail "Cannot change profile: this is the last admin. Promote another user first." Mirrors `POST /demote-admin` invariant via `_lock_org_for_role_change` + admin_count check. (Sparring #9.) |
| REQ-16 | NIEUW v0.3.0 | Frontend error banners en toasts SHALL strip the `"<status>: "` prefix uit `apiFetch`-formatted errors via shared helper `_components/errors.ts::cleanErrorMessage(err, fallback)`. Gebruikt in profiles/$profile.tsx en user-edit. |
| REQ-17 | LIVE in #317 — behouden | All polish-round files SHALL voldoen aan klai-portal v1-spine (`portal-patterns.md`): `mx-auto max-w-3xl px-6 py-10` voor list-views, `mx-auto max-w-lg px-6 py-10` voor forms, `border-gray-200` literal voor tables/lists, `bg-black/5` hover + `bg-black/[0.06]` active, `text-gray-900` prose + `text-gray-400` muted, `rounded-full bg-gray-900` buttons, sentence-case, geen amber buiten focus-rings/logo. ProfilePicker selected state: `border-gray-900 bg-black/[0.06]` (geen `var(--color-accent)`). |

---

## Acceptance Criteria (v0.3.0)

**AC-1** (REQ-1, REQ-2, REQ-10, REQ-11): `/admin/users` toont Profile column, search-Input bovenaan, ⋯ overflow met Change profile submenu. Sidebar volgorde Overview · Users · Profiles · Groups · Billing. Geen "Role"/"Rol" UI strings. ALLE: live op getklai.getklai.com vóór polish-round, blijft staan.

**AC-2** (REQ-4): `/admin/profiles` rendert als section-style table (kopie van groups/index.tsx-pattern), 5 rijen in ladder-volgorde. Counts kloppen tegen `SELECT role, count(*) FROM portal_users WHERE org_id=<tenant> GROUP BY role`. Sub-text description onder profile-naam in dezelfde Name-cel.

**AC-3** (REQ-5): `/admin/profiles/<role>` drill-in toont alle users met die `role`, met `<UserAvatar>` per row. Page header: Back-link + profile label h1 + description.

**AC-4** (REQ-6): Klikken op `Add member` → navigatie naar `/admin/profiles/<role>/add-member`. Picker toont alleen users wiens huidige role NIET het target is. Submit → `PATCH /api/admin/users/<id>/role` met `{role: "<target>"}` → redirect terug naar drill-in. User is nu zichtbaar in drill-in.

**AC-5** (REQ-7): Klikken op trash-icon op een drill-in row → `InlineDeleteConfirm` met `"Demote {name} to Personal chat?"` → confirm → `PATCH /role` met `{role: "personal"}` → row verdwijnt uit huidige drill-in. Geen bulk-select. Geen "Move to ▾" submenu.

**AC-6** (REQ-8): `/admin/groups` ongewijzigd t.o.v. main na PR #317 (subtitle + empty-state copy zoals daar gemerged). Polish-round raakt deze pagina niet aan.

**AC-7** (REQ-9): `/admin/users/invite` heeft een `<Select>` dropdown met 5 opties (Personal chat / Company chat / Knowledge manager / Group manager / Admin) en default `personal`. Geen radio-cards. Geen ProfilePicker component op deze pagina.

**AC-8** (REQ-12, REQ-13, REQ-14): `/admin/users/$userId/edit` heeft ÉÉN `<form>` met ÉÉN submit-button onderaan. Geen Groups-sectie. Header subtitle aanwezig. `git grep -E 'type=.submit.' klai-portal/frontend/src/routes/admin/users/\\$userId/edit.tsx` returnt exact 1 hit (de Save). `git grep 'admin-user-groups' klai-portal/frontend/src/routes/admin/users/\\$userId/edit.tsx` returnt 0.

**AC-9** (REQ-15): Backend test `tests/test_spec_portal_admin_ui_001.py` (4 cases — last-admin demote 409, two-admin demote OK, promote skip-check, non-admin → non-admin OK) blijft groen op main na polish-merge.

**AC-10** (REQ-16): UI-error banner bij min-1-admin-trigger toont `"Cannot change profile: this is the last admin. Promote another user first."` zonder `"409: "` prefix.

**AC-11** (REQ-17): `git grep 'border-\[var(--color-border)\]' klai-portal/frontend/src/routes/admin/profiles/` returnt 0. `git grep 'var(--color-accent)' klai-portal/frontend/src/routes/admin/profiles/ klai-portal/frontend/src/routes/admin/_components/ProfilePicker.tsx` returnt 0. `npx tsc -b` clean. `npm run lint` clean.

---

## Technical approach (v0.3.0 polish)

### Frontend file plan

| File | Polish action | Template/source |
|---|---|---|
| `routes/admin/profiles/index.tsx` | Volledig herschrijven | 1-op-1 kopie van `routes/admin/groups/index.tsx`. Vervang dynamic group-fetch door statische 5 rows uit `PROFILE_LADDER`. Members count = client-side count uit `/api/admin/users` waar `user.role === <profile>`. Geen "Create" knop. Description als sub-text in Name-cel. |
| `routes/admin/profiles/$profile.tsx` | Volledig herschrijven | 1-op-1 kopie van `routes/admin/groups/$groupId/index.tsx`. Profile metadata uit `PROFILE_LADDER` (statisch). Members = filter `users` op `role === profileSlug`. Add member knop → navigate `/admin/profiles/$profile/add-member`. Remove member → InlineDeleteConfirm + `PATCH /role` met `{role: "personal"}`. UserAvatar in name cell. Geen bulk-select, geen Move-to ▾ submenu. |
| `routes/admin/profiles/$profile/add-member.tsx` | Nieuw bestand | 1-op-1 kopie van `routes/admin/groups/$groupId/add-member.tsx`. Picker filtert users wiens `role !== <profile>`. Submit → `PATCH /api/admin/users/<id>/role` met `{role: "<profile>"}` → redirect naar drill-in. |
| `routes/admin/users/invite.tsx` | Refactor (rollback) | Verwijder `<ProfilePicker>` import, herstel `<Select>` met 5 `<option>` waarden uit `PROFILE_LADDER` + `profile_*_label`. Default `form.role = "personal"`. Layout: name fields → email → profile + language in 2-col grid → submit. |
| `routes/admin/users/$userId/edit.tsx` | Refactor | Verwijder beide `<Card>` wrappers met aparte saves. Verwijder Groups-sectie (state + queries + mutations). Eén form met First name + Last name + Invitation language + ProfilePicker. Eén `<Button type="submit">` onderaan + `Cancel` ghost. Submit-handler: `PATCH /api/admin/users/<id>` (naam/taal) en, indien profile gewijzigd, `PATCH /api/admin/users/<id>/role` (sequentieel). Header subtitle (REQ-14) toevoegen. Lifecycle-acties (suspend/reactivate/offboard) blijven onder de form. |

**Untouched** (Sparring #1 herzien, #6, REQ-1/8/10/11 al voldaan):
- `routes/admin/users/index.tsx`
- `routes/admin/groups/**`
- `routes/admin/route.tsx`

### Component patterns to keep

- `_components/ProfilePicker.tsx` — v1-spine compliant, alleen gebruikt in user-edit.
- `_components/UserAvatar.tsx` — gebruikt in profiles/$profile drill-in.
- `_components/errors.ts::cleanErrorMessage` — gebruikt in profiles/$profile + user-edit voor 409 prefix scrub.

### Backend

- `app/api/admin/users.py::update_user_role` — min-1-admin invariant uit PR #317 blijft (REQ-15). Geen verdere wijzigingen.
- Geen migraties. Geen nieuwe endpoints.

### Tests

| Test file | Cases |
|---|---|
| `_components/__tests__/ProfilePicker.test.tsx` | (al groen, 6 cases) Ladder volgorde, selection state, onChange callback, description toggle (default vs compact), disabled mode. |
| `_components/__tests__/UserAvatar.test.tsx` (NIEUW) | Initials uit first+last name, fallback naar email-prefix, decoratieve kleur per uid hash. |
| `routes/admin/profiles/__tests__/index.test.tsx` (NIEUW) | Renders 5 rows in ladder volgorde. Counts aggregate correct uit `/api/admin/users` mock data. Edit + Eye actions navigeren naar `/admin/profiles/<role>`. |
| `routes/admin/profiles/__tests__/$profile.test.tsx` (NIEUW) | Filtert users op huidige profile param. Remove member actie dispatches `PATCH /role` met `{role: "personal"}`. Geen bulk checkboxes in DOM. cleanErrorMessage strips 409 prefix in error banner. |
| `routes/admin/profiles/__tests__/add-member.test.tsx` (NIEUW) | Picker filtert users wiens role !== current profile. Submit dispatches `PATCH /role` met `{role: "<profile>"}`. |
| `routes/admin/users/__tests__/invite.test.tsx` (NIEUW) | Default `form.role === "personal"`. `<Select>` rendert 5 opties met juiste i18n labels. Submit verstuurt body met geselecteerde profile-waarde. |
| `routes/admin/users/$userId/__tests__/edit.test.tsx` (NIEUW) | Eén `<button type="submit">`. Geen Groups-sectie in DOM (`queryByText('Groups')` returnt null). Submit-handler dispatches juiste mutation chain. |
| `klai-portal/backend/tests/test_spec_portal_admin_ui_001.py` | (al groen, 4 cases) min-1-admin invariant op PATCH /role. |

### Performance

- Counts op `/admin/profiles` zijn O(users) client-side. Geen extra DB query.
- Geen filter logica meer (geen filter-chips); search-Input blijft client-side.

---

## Out-of-scope follow-ups (separate SPECs)

- Bulk-move endpoint (`PATCH /api/admin/users/bulk-role` accepting a list) for tenants with hundreds of users where N HTTP calls becomes slow. Wait for actual scale need.
- Mobile-responsive redesign of the admin section.
- Audit-log surface in the admin UI showing "Mark moved Lisa from Company to KB manager at 2026-05-05 14:23" — useful for compliance, separate SPEC.

---

## Open questions

Alle open vragen voor v0.3.0 zijn beantwoord. Sparring decisions table en REQ-status table bovenaan zijn de bron van waarheid. Implementer mag GEEN UX-keuzes uitbreiden buiten wat hier expliciet staat — bij twijfel STOPPEN en vragen, niet "industry-standard" invullen.

---

## Estimated effort (v0.3.0 polish round)

- Frontend rollbacks + nieuwe routes: ~300 LOC herschrijven (profiles/index + profiles/$profile + profiles/$profile/add-member) + ~50 LOC refactor (invite + user-edit). Net ~250 LOC herwerk.
- Tests: 6 nieuwe component test files (~250 LOC).
- Backend: 0 LOC.
- DB: 0 migrations.

---

## Definition of done (v0.3.0)

- Alle REQ-1 t/m REQ-17 voldaan, met de `LIVE in #317 — niet aanraken` rijen ongewijzigd op getklai.getklai.com en my.getklai.com.
- Alle AC-1 t/m AC-11 verifieerbaar:
  - frontend `npx tsc -b` clean
  - frontend `npm run lint` clean
  - frontend tests groen (ProfilePicker + UserAvatar + profiles/index + profiles/$profile + profiles/add-member + invite + user-edit; minimum 7 frontend test files groen)
  - backend `tests/test_spec_portal_admin_ui_001.py` 4/4 groen
  - `git grep 'border-\[var(--color-border)\]' klai-portal/frontend/src/routes/admin/profiles/` → 0 hits
  - `git grep 'var(--color-accent)' klai-portal/frontend/src/routes/admin/profiles/ klai-portal/frontend/src/routes/admin/_components/ProfilePicker.tsx` → 0 hits
  - `git grep 'admin-user-groups' klai-portal/frontend/src/routes/admin/users/\$userId/edit.tsx` → 0 hits
  - `git grep 'type=.submit.' klai-portal/frontend/src/routes/admin/users/\$userId/edit.tsx` → 1 hit
- Mark verifieert post-deploy op getklai.getklai.com:
  - Open `/admin/profiles` → 5 rows + counts + tabel-pattern dat eruitziet als `/admin/groups` index
  - Drill in op een profile → groups-detail-stijl tabel met members
  - Klik Add member → picker → pick user → user verschijnt in drill-in
  - Klik trash op een member → confirm → user is gedemoteerd naar Personal chat (zichtbaar in `/admin/profiles/personal`)
  - Open `/admin/users/<id>/edit` → één form, één Save knop, geen Groups-sectie, header subtitle aanwezig
  - Open `/admin/users/invite` → dropdown met 5 opties, Personal chat default geselecteerd
- Min-1-admin invariant: poging om laatste admin te demoten (via user-edit profile picker) toont `"Cannot change profile: this is the last admin. Promote another user first."` zonder `"409: "` prefix.
