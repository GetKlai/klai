---
id: SPEC-CHAT-TEMPLATES-002
version: 0.1.0
status: draft
created: 2026-04-23
updated: 2026-04-23
author: Mark Vletter
priority: high
issue_number: 0
---

# SPEC-CHAT-TEMPLATES-002 — Prompt Templates frontend CRUD pages (`/app/templates`)

## HISTORY

| Version | Date       | Author       | Change |
|---------|------------|--------------|--------|
| 0.1.0   | 2026-04-23 | Mark Vletter | Initial draft — frontend CRUD pagina's voor prompt templates op `/app/templates/*`. Upgrade placeholder naar volledige list/new/edit/delete met paraglide i18n (NL + EN). Backend geleverd door SPEC-CHAT-TEMPLATES-001 (zelfde worktree). Gebonden aan portal-patterns.md design standaard. |

---

## Overview

### Waarom

Het portal heeft sinds SPEC-CHAT-TEMPLATES-001 (backend, reeds geïmplementeerd in branch `feature/SPEC-CHAT-TEMPLATES-001`) volledige CRUD endpoints voor prompt templates:

- `GET /api/app/templates` — list (org + personal, per caller gefilterd)
- `POST /api/app/templates` — create (scope=org gated op admin)
- `GET /api/app/templates/{slug}` — fetch single
- `PATCH /api/app/templates/{slug}` — update
- `DELETE /api/app/templates/{slug}` — delete
- `GET /internal/templates/effective` — LiteLLM hook consumer
- Provisioning-seeder met 4 NL defaults (Klantenservice, Formeel, Creatief, Samenvatter)
- Rate-limits + cache-invalidatie

De chat-config-bar in `klai-portal/frontend/src/routes/app/index.tsx` (uit SPEC-PORTAL-REDESIGN-002 Phase 3) heeft de template-picker én rules-status-chip al klaar staan. Er is echter alleen nog een placeholder op `/app/templates/` — een empty-state met copy "Nog geen templates". Eindgebruikers kunnen nog geen templates aanmaken, bewerken of verwijderen.

### Wat

Deze SPEC upgrade de placeholder `/app/templates/` naar een volledige CRUD UI waarmee:

1. Alle org-members hun zichtbare templates (org-scope + eigen personal) zien in een lijst
2. Org-admins nieuwe templates kunnen aanmaken met `scope="org"` (zichtbaar voor iedereen in de org) of `scope="personal"` (alleen zichtbaar voor henzelf)
3. Non-admin members alleen personal templates kunnen aanmaken (organisatie-optie gedisabled met tooltip)
4. Owners + admins bestaande templates kunnen bewerken en verwijderen (met InlineDeleteConfirm)
5. Wijzigingen direct doorwerken in de ChatConfigBar template-picker via TanStack Query cache invalidation

### Afbakening t.o.v. gerelateerde SPECs

- **SPEC-CHAT-TEMPLATES-001 (backend, zelfde worktree):** levert alle CRUD endpoints + LiteLLM hook + seeder. Niet gewijzigd door deze SPEC.
- **SPEC-PORTAL-REDESIGN-002 Phase 3 (chat-config-bar):** levert template-picker dropdown met `active_template_ids` activation flow op KB-preference endpoint. Deze SPEC raakt die code niet aan maar triggert wel cache-invalidatie zodat de picker refresh'd.
- **SPEC-CHAT-GUARDRAILS-001 (rules frontend, niet in deze worktree):** rules CRUD UI komt later. Deze SPEC raakt de rules-flow niet.

---

## Requirements

### REQ-TEMPLATES-UI-LIST — Templates list page (`/app/templates/`)

#### Ubiquitous

- **REQ-LIST-U1:** The `/app/templates/` route SHALL render within a `ProductGuard product="chat"` wrapper so non-chat-enabled tenants never see the page.
- **REQ-LIST-U2:** The list page SHALL use the portal-patterns.md list container: `mx-auto max-w-3xl px-6 py-10`.
- **REQ-LIST-U3:** The page SHALL fetch templates visible to the caller via `GET /api/app/templates` (backend filters org-scope plus own-personal; as admin also all personal in org).
- **REQ-LIST-U4:** The page heading SHALL use the `page-title` utility class with `text-[26px] font-display-bold text-gray-900` and sentence-case copy.
- **REQ-LIST-U5:** The page subtitle SHALL come from paraglide key `templates_page_subtitle` and use `text-sm text-gray-500` (or portal equivalent muted style).

#### Event-Driven

- **REQ-LIST-E1:** WHEN the list is empty, the empty-state SHALL render with a dashed-border container (`rounded-lg border border-dashed border-gray-200 py-16 text-center`), a `Sliders` icon (`h-10 w-10 text-gray-300`), NL copy from existing paraglide keys `templates_empty_title` + `templates_empty_description`, and a "Nieuwe template" / "Eerste template aanmaken" CTA button.
- **REQ-LIST-E2:** WHEN the list contains templates, each row SHALL display name (bold `text-gray-900`), description (truncated muted `text-gray-400`), scope badge (`<Badge variant="secondary">` with rounded-full), active-indicator (when slug is present in caller's `active_template_ids`), edit button (Pencil icon `h-4 w-4`), and delete control.
- **REQ-LIST-E3:** WHEN the user clicks the edit button on a row, the router SHALL navigate to `/app/templates/{slug}/edit`.
- **REQ-LIST-E4:** WHEN the user clicks the delete control, `InlineDeleteConfirm` from `@/components/ui/inline-delete-confirm` SHALL appear inline; confirmation SHALL invoke `DELETE /api/app/templates/{slug}`.
- **REQ-LIST-E5:** WHEN deletion succeeds, the row SHALL disappear via optimistic or refetch update, AND the ChatConfigBar template-picker SHALL refresh by invalidating both `['app-templates']` and `['app-templates-for-bar']` query keys.
- **REQ-LIST-E6:** WHEN the user clicks the "Nieuwe template" / "Nieuwe persoonlijke template" primary CTA, the router SHALL navigate to `/app/templates/new`.

#### State-Driven

- **REQ-LIST-S1:** WHILE the caller's role is not `"admin"`, the delete control SHALL be disabled (or hidden) on rows where `created_by !== callerId`; the edit button SHALL be similarly restricted to own rows.
- **REQ-LIST-S2:** WHILE a delete mutation is pending for a given row, the delete button SHALL be disabled and show pending copy from `templates_form_deleting`.
- **REQ-LIST-S3:** WHILE the primary CTA label depends on role: admin sees "Nieuwe template" (`templates_list_create_button`), non-admin sees "Nieuwe persoonlijke template" (new paraglide key if needed).

#### Unwanted Behavior

- **REQ-LIST-N1:** IF the list renders inline Dutch literals instead of paraglide messages, THIS IS A BUG.
- **REQ-LIST-N2:** IF a row uses a card wrapper (`bg-white rounded-lg shadow-sm`) instead of the divider-row section pattern, THIS IS A BUG per portal-patterns.md (Tables / Collection List section).
- **REQ-LIST-N3:** IF the CTA button uses `bg-amber-*` or `bg-yellow-*` Tailwind classes, THIS IS A BUG (amber is focus-ring + logo reserve only).

---

### REQ-TEMPLATES-UI-FORM — Template form (new + edit)

#### Ubiquitous

- **REQ-FORM-U1:** The form container SHALL use `mx-auto max-w-lg px-6 py-10` (portal-patterns.md Form/edit width).
- **REQ-FORM-U2:** The form SHALL contain the following fields in this order:
  - Naam: `<Input>` required, `maxLength=128`
  - Beschrijving: `<Input>` optional, `maxLength=500`
  - Prompt-instructies: `<textarea>` required, `maxLength=8000`, `min-h-[200px]`, `resize-y`, with inline character counter
  - Bereik: `<Select>` with options "Organisatie" / "Persoonlijk"
- **REQ-FORM-U3:** The Prompt-instructies textarea SHALL display a `[current]/8000` character counter (paraglide key `templates_form_prompt_char_count`) below-right the textarea that turns `text-[var(--color-destructive)]` when current equals 8000.
- **REQ-FORM-U4:** The primary submit button SHALL use `bg-gray-900 text-white rounded-full` styling and label from `templates_form_submit`; while the mutation is pending, the label SHALL change to the value of `templates_form_saving` and the button SHALL be disabled.
- **REQ-FORM-U5:** The cancel/back control SHALL be a muted text link (`text-gray-400 hover:text-gray-900`), labelled from `templates_form_cancel`, navigating back to `/app/templates`.
- **REQ-FORM-U6:** Form field spacing SHALL follow portal-patterns.md: overall `space-y-4`, each label+input group `space-y-1.5`, action row `gap-3`.
- **REQ-FORM-U7:** The form title SHALL read `templates_form_new_title` in new mode and `templates_form_edit_title` in edit mode; subtitle from `templates_form_subtitle`.
- **REQ-FORM-U8:** Inputs and textarea SHALL use `rounded-lg border-gray-200 text-sm` base styling and focus via `--color-ring` (amber), inherited from shadcn/ui primitives, NOT via ad-hoc inline styling.

#### Event-Driven

- **REQ-FORM-E1:** WHEN a non-admin caller loads the form, the "Organisatie" option in the Bereik select SHALL be disabled with a tooltip reading `templates_form_scope_org_disabled_tooltip` ("Alleen beheerders"); the default selection SHALL be "Persoonlijk".
- **REQ-FORM-E2:** WHEN a user submits with Naam empty, client-side validation SHALL show `templates_form_error_name_required` next to the Naam field and SHALL NOT invoke the API.
- **REQ-FORM-E3:** WHEN a user submits with Prompt-instructies empty, client-side validation SHALL show `templates_form_error_prompt_required` and SHALL NOT invoke the API.
- **REQ-FORM-E4:** WHEN a user types Prompt-instructies exceeding 8000 characters, the textarea's `maxLength` SHALL prevent the 8001st character; if somehow submitted (paste/programmatic), validation SHALL show `templates_form_error_prompt_too_long`.
- **REQ-FORM-E5:** WHEN the backend returns HTTP 403 for a non-admin attempting `scope="org"`, the form SHALL render the NL error `templates_form_error_org_admin_only` ("Alleen beheerders mogen organisatie-templates aanmaken") below the form, matching the server-side NL message.
- **REQ-FORM-E6:** WHEN the backend returns HTTP 409 (slug conflict), the form SHALL render a NL error pointing at the Naam field suggesting a different name.
- **REQ-FORM-E7:** WHEN save succeeds, the router SHALL navigate back to `/app/templates` AND the following query keys SHALL be invalidated: `['app-templates']`, `['app-templates-for-bar']`, `['kb-preference']`.
- **REQ-FORM-E8:** WHEN the edit-mode page loads, a `useQuery(['app-template', slug], ...)` SHALL fetch the single template via `GET /api/app/templates/{slug}`; while loading, the form SHALL render a skeleton or "Laden..." state; on error, an inline error with retry hint SHALL render.

#### State-Driven

- **REQ-FORM-S1:** WHILE an edit-mode caller is non-admin AND the template being edited has `scope="org"`, the form MAY render read-only (non-admins cannot mutate org templates); alternatively, the PATCH mutation MAY rely on backend 403 as the authority and render the error on submit. (Frontend defense-in-depth: disable inputs to be safe.)
- **REQ-FORM-S2:** WHILE the Prompt-instructies character count exceeds 7500 (90% of limit), the counter SHALL turn amber/warning color as a visual cue.
- **REQ-FORM-S3:** WHILE the form is in new-mode for a non-admin, the Bereik select SHALL be locked to "Persoonlijk" with the "Organisatie" option disabled and tooltipped.

#### Unwanted Behavior

- **REQ-FORM-N1:** IF the form uses inline `style={{ fontFamily: ... }}`, any Tailwind `uppercase` class, `tracking-wider`, `tracking-[0.04em]`, or any `bg-amber-*` / `bg-yellow-*` class on buttons, THIS IS A BUG per portal-patterns.md anti-patterns table.
- **REQ-FORM-N2:** IF an error message uses `text-red-600` instead of the semantic token `text-[var(--color-destructive)]`, THIS IS A BUG.
- **REQ-FORM-N3:** IF the form relies ONLY on client-side admin-gate without server-side re-check on save, THIS IS A BUG — backend 403 is the authority, frontend gate is UX-only.
- **REQ-FORM-N4:** IF the primary button renders with a width other than content-auto or uses `w-full` on desktop, THIS IS A BUG (portal-patterns.md: primary buttons are content-auto pills).
- **REQ-FORM-N5:** IF the textarea lacks a `resize-y` and `max-h-[400px]` constraint, THIS IS A BUG (unbounded growth breaks layout on long prompts).

---

### REQ-TEMPLATES-UI-I18N — Paraglide messages

#### Ubiquitous

- **REQ-I18N-U1:** Every user-facing string on the templates pages SHALL be sourced from `@/paraglide/messages`. No inline Dutch or English literals in TSX except for technical identifiers (icon names, aria-hidden helpers).
- **REQ-I18N-U2:** Every new message key SHALL exist in BOTH `klai-portal/frontend/messages/nl.json` AND `klai-portal/frontend/messages/en.json` with matching key structure and equivalent semantic content.
- **REQ-I18N-U3:** New keys SHALL use prefix `templates_list_` or `templates_form_` to avoid collisions with existing keys from SPEC-PORTAL-REDESIGN-002 (which owns `templates_page_title`, `templates_empty_title`, `templates_empty_description` — NOT modified by this SPEC).

Minimum new keys required (both locales):

- `templates_page_subtitle`
- `templates_list_create_button`
- `templates_list_create_personal_button`
- `templates_list_delete_confirm`
- `templates_list_edit_label`
- `templates_list_delete_label`
- `templates_list_scope_org`
- `templates_list_scope_personal`
- `templates_list_active_label`
- `templates_form_new_title`
- `templates_form_edit_title`
- `templates_form_subtitle`
- `templates_form_name_label`
- `templates_form_name_placeholder`
- `templates_form_description_label`
- `templates_form_description_placeholder`
- `templates_form_prompt_label`
- `templates_form_prompt_placeholder`
- `templates_form_prompt_char_count`
- `templates_form_scope_label`
- `templates_form_scope_org_disabled_tooltip`
- `templates_form_submit`
- `templates_form_cancel`
- `templates_form_deleting`
- `templates_form_saving`
- `templates_form_loading`
- `templates_form_error_org_admin_only`
- `templates_form_error_prompt_too_long`
- `templates_form_error_name_required`
- `templates_form_error_prompt_required`
- `templates_form_error_slug_conflict`
- `templates_form_error_generic`

#### Unwanted Behavior

- **REQ-I18N-N1:** IF any new key is added to only one locale file, THIS IS A BUG. Keys MUST exist in nl.json AND en.json.
- **REQ-I18N-N2:** IF any inline Dutch literal appears in TSX source, THIS IS A BUG.

---

### REQ-TEMPLATES-UI-ROUTETREE — Route tree regeneration

#### Ubiquitous

- **REQ-RT-U1:** After adding route files `/app/templates/new.tsx` and `/app/templates/$slug.edit.tsx`, the file `klai-portal/frontend/src/routeTree.gen.ts` SHALL be regenerated via the portal's TanStack Router codegen step (dev-server hot regeneration or explicit `pnpm --filter klai-portal-frontend typegen` command).
- **REQ-RT-U2:** The `-template-form.tsx` file (prefix `-`) SHALL NOT be registered as a route — the prefix signals TanStack Router to ignore it.

#### Unwanted Behavior

- **REQ-RT-N1:** IF `routeTree.gen.ts` is edited manually instead of regenerated, THIS IS A BUG (the file is generated; manual edits are overwritten).

---

## Files to Create / Modify

### NEW files

| Path | Purpose | Approx LOC |
|------|---------|-----------|
| `klai-portal/frontend/src/routes/app/templates/new.tsx` | TanStack Router route wrapper for new-mode, renders `<TemplateFormPage mode="new" />` | 15 |
| `klai-portal/frontend/src/routes/app/templates/$slug.edit.tsx` | Route wrapper for edit-mode, fetches single template via `useQuery`, renders `<TemplateFormPage mode="edit" initialForm={...} />` | 60–80 |
| `klai-portal/frontend/src/routes/app/templates/-template-form.tsx` | Shared form component (prefix `-` = ignored by router); handles both new and edit modes, validation, mutations, error surfaces | ~220 |
| `klai-portal/frontend/src/routes/app/templates/__tests__/templates-form.test.tsx` | Vitest unit tests: renders container with correct classes, admin vs non-admin scope-option visibility, validation surfaces | ~150 |
| `klai-portal/frontend/src/routes/app/templates/__tests__/templates-list.test.tsx` | Vitest unit tests: empty state, populated list, delete button gated by ownership/admin | ~120 |
| `klai-portal/frontend/tests/e2e/templates.spec.ts` | Playwright e2e stub (happy path + chat-bar integration) — scope for run-fase, implementer verfijnt | ~100 |

### MODIFY files

| Path | Change | Reason |
|------|--------|--------|
| `klai-portal/frontend/src/routes/app/templates/index.tsx` | Complete rewrite: placeholder → full list with query, rows, delete, CTA | From empty-state to functional CRUD list |
| `klai-portal/frontend/messages/nl.json` | Add ~32 new keys under `templates_list_*` + `templates_form_*` prefixes | Dutch i18n for new pages |
| `klai-portal/frontend/messages/en.json` | Add same ~32 new keys with English values | English i18n parity |
| `klai-portal/frontend/src/routeTree.gen.ts` | Regenerated (not hand-edited) | Register new.tsx + $slug.edit.tsx routes |

---

## Exclusions (What NOT to Build)

The following is explicitly **out of scope** for SPEC-CHAT-TEMPLATES-002:

1. **ChatConfigBar wijzigingen** — the chat-config-bar (template picker + rules chip) is fully delivered by SPEC-PORTAL-REDESIGN-002 Phase 3. No changes to `klai-portal/frontend/src/routes/app/index.tsx` or its sub-components in this SPEC.
2. **Rules frontend** — guardrails/rules CRUD UI is covered by SPEC-CHAT-GUARDRAILS-001 (future). This SPEC does not add any rules page or component.
3. **Backend CRUD endpoints** — already delivered by SPEC-CHAT-TEMPLATES-001 in the same worktree. No backend changes.
4. **Per-KB template-scoping in UI** — not in v1. Templates are org-wide or personal; no knowledge-base filter yet.
5. **Bulk operations (import/export)** — not in v1. One-at-a-time create/edit/delete only.
6. **Template-versioning UI** — not in v1. Backend has no versioning table; no rollback/history view.
7. **Modifications to existing paraglide keys** — `templates_page_title`, `templates_empty_title`, `templates_empty_description` remain untouched (owned by SPEC-PORTAL-REDESIGN-002).
8. **LiteLLM hook adjustments** — `/internal/templates/effective` is fully functional via SPEC-CHAT-TEMPLATES-001. No hook changes.
9. **New design primitives** — use existing shadcn/ui `<Input>`, `<Textarea>`, `<Select>`, `<Badge>`, `<Button>`, `<InlineDeleteConfirm>`. No new base-components created.
10. **Analytics/product events** — no `product_events` emission for template CRUD in v1 (can be added later via a follow-up SPEC if needed).

---

## References

### Design standards (leidend)

- `.claude/rules/klai/design/portal-patterns.md` — **primary design authority** for this SPEC (list layout, form layout, button styles, empty state, badges, error tokens, anti-patterns)
- `.claude/rules/klai/design/styleguide.md` — brand DNA (secondary; overrides from portal-patterns.md win)

### Dependent / related SPECs

- **SPEC-CHAT-TEMPLATES-001** (backend, same worktree) — CRUD endpoints, LiteLLM hook, provisioning-seeder, admin-gate, rate-limit, cache-invalidation
- **SPEC-PORTAL-REDESIGN-002 Phase 3** — ChatConfigBar with template-picker (`active_template_ids` activation via KB-preference endpoint) and rules-status chip
- **SPEC-CHAT-GUARDRAILS-001** (future, out of scope) — rules CRUD frontend

### Existing code references

- `klai-portal/frontend/src/routes/app/templates/index.tsx` — current placeholder to upgrade
- `klai-portal/frontend/src/routes/app/index.tsx` — ChatConfigBar (reference only, not modified)
- `klai-portal/frontend/src/components/ui/inline-delete-confirm.tsx` — delete UI primitive
- `klai-portal/frontend/src/lib/api-fetch.ts` (or equivalent) — `apiFetch` helper for all mutations
- `klai-portal/frontend/messages/nl.json` + `en.json` — paraglide i18n source-of-truth

### External resources

- Portal-patterns.md anti-patterns table (expliciete list die we willen voorkomen)
- Shadcn/ui Select + Input + Textarea + Badge component docs (inherited patterns)

---

## MX Tag Planning

| Symbol | Tag | Reason |
|--------|-----|--------|
| `src/routes/app/templates/-template-form.tsx:TemplateFormPage` | `@MX:ANCHOR` | fan_in = 2 (new + edit routes beide renderen dit component); invariant contract |
| `src/routes/app/templates/index.tsx:TemplatesPage` | `@MX:NOTE` | "Mirror portal-patterns.md list-row pattern; wijzigingen in design-rule moeten hier ook landen" |
| `TemplateFormPage:validateScope` (admin-gate) | `@MX:WARN` + `@MX:REASON` | "Server is autoriteit; frontend-gate is UX-voorziening. Backend 403 is ground-truth voor scope='org' non-admin." |
| Prompt textarea `maxLength={8000}` | `@MX:NOTE` | "Verwijst naar backend CHECK constraint `portal_templates.ck_portal_template_prompt_len` — houd waarden synchroon" |

---

End of spec.md
