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

# SPEC-CHAT-TEMPLATES-002 — Compact

Auto-generated from spec.md + acceptance.md. Used by `/moai run` to save ~30% tokens vs full spec.md.
For full context (Overview, Rationale, Portal-patterns appendix) see `spec.md`.

---

## Requirements (EARS)

### REQ-TEMPLATES-UI-LIST — Templates list page (`/app/templates/`)

**Ubiquitous**
- U1: Page SHALL render within `ProductGuard product="chat"`.
- U2: Container: `mx-auto max-w-3xl px-6 py-10` (portal-patterns.md List width).
- U3: Templates fetched via `GET /api/app/templates`.

**Event-Driven**
- E1: WHEN list is empty → empty-state: `rounded-lg border border-dashed border-gray-200 py-16 text-center`, Sliders icon `h-10 w-10 text-gray-300`, CTA "Nieuwe template" / "Eerste template aanmaken".
- E2: WHEN populated → divider-row list (`divide-y divide-gray-200 border-t border-b border-gray-200`), row = naam + description (truncate) + scope badge + edit button + delete (`InlineDeleteConfirm`).
- E3: WHEN user clicks edit → navigate `/app/templates/{slug}/edit`.
- E4: WHEN user clicks delete → `InlineDeleteConfirm` shown; confirm → `DELETE /api/app/templates/{slug}`.
- E5: WHEN delete succeeds → row disappears AND invalidate `['app-templates']` + `['app-templates-for-bar']` + `['kb-preference']`.
- E6: WHEN admin → "Nieuwe template"; non-admin → "Nieuwe persoonlijke template".

**State-Driven**
- S1: WHILE caller role != "admin" → delete control disabled on rows where `created_by !== caller.zitadel_user_id`.
- S2: WHILE slug is in caller's `active_template_ids` → active-indicator badge/dot renders on the row.

**Unwanted**
- N1: IF list container uses width other than `max-w-3xl` → bug.
- N2: IF empty state uses non-dashed border / different icon → bug.
- N3: IF list-row uses card wrapper instead of divider-row pattern → bug.

### REQ-TEMPLATES-UI-FORM — Template form (new + edit)

**Ubiquitous**
- U1: Container: `mx-auto max-w-lg px-6 py-10` (portal-patterns.md Form width).
- U2: Fields: Naam (Input max 128), Beschrijving (Input max 500 optional), Prompt-instructies (textarea max 8000, char-counter), Bereik (Select org/personal).
- U3: Textarea char-counter `[current]/8000` turns `text-[var(--color-destructive)]` at ≥ 7800 (warning) and ≥ 8000 (block).
- U4: Primary submit: `bg-gray-900 text-white rounded-full`.
- U5: Cancel/back: muted text link `text-gray-400 hover:text-gray-900` labelled "Terug".
- U6: Labels use `<Label>` component; `space-y-1.5` label+input; form `space-y-4`.

**Event-Driven**
- E1: WHEN non-admin views form → "Organisatie" option disabled + tooltip "Alleen beheerders"; default selection "Persoonlijk".
- E2: WHEN admin views new form → default selection "Organisatie"; both options enabled.
- E3: WHEN Naam empty on submit → client-side validation `templates_form_error_name_required`; no API hit.
- E4: WHEN Prompt-instructies empty on submit → `templates_form_error_prompt_required`.
- E5: WHEN Prompt-instructies > 8000 chars → `templates_form_error_prompt_too_long`.
- E6: WHEN backend returns HTTP 403 scope="org" by non-admin → render `templates_form_error_org_admin_only` (NL, matches server message).
- E7: WHEN backend returns HTTP 409 (slug collision) → inline error on Naam field.
- E8: WHEN save succeeds → navigate(`/app/templates`) + invalidate `['app-templates']`, `['app-templates-for-bar']`, `['kb-preference']`.
- E9: WHEN edit mode loads → `useQuery(['app-template', slug])` populates initial form state from `GET /api/app/templates/{slug}`.

**State-Driven**
- S1: WHILE submission in-flight → submit button disabled with "Laden..." / "Opslaan..." label.
- S2: WHILE character count ≥ 7800 → counter `text-amber-600`; ≥ 8000 → `text-[var(--color-destructive)]` (warning → block).

**Unwanted**
- N1: IF `style={{ fontFamily: ... }}` used anywhere → bug (anti-pattern in portal-patterns.md).
- N2: IF any `uppercase` or `tracking-wider` / `tracking-[0.04em]` class on prose → bug.
- N3: IF error uses `text-red-600` instead of `text-[var(--color-destructive)]` → bug.
- N4: IF button uses `rounded-lg` / `rounded-md` instead of `rounded-full` → bug.
- N5: IF any `bg-amber-*` / `bg-yellow-*` on buttons (amber reserved for focus-ring + logo) → bug.

### REQ-TEMPLATES-UI-I18N — Paraglide messages

**Ubiquitous**
- U1: Every user-facing string in `/app/templates/*` SHALL resolve via `@/paraglide/messages`. No inline Dutch literals in TSX.
- U2: New keys exist in BOTH `messages/nl.json` AND `messages/en.json` with identical set.

New keys (minimum): `templates_page_subtitle`, `templates_list_create_button`, `templates_list_create_personal_button`, `templates_list_delete_confirm`, `templates_list_edit_label`, `templates_list_scope_org`, `templates_list_scope_personal`, `templates_list_active_label`, `templates_form_new_title`, `templates_form_edit_title`, `templates_form_subtitle`, `templates_form_name_label`, `templates_form_name_placeholder`, `templates_form_description_label`, `templates_form_description_placeholder`, `templates_form_prompt_label`, `templates_form_prompt_placeholder`, `templates_form_prompt_char_count`, `templates_form_scope_label`, `templates_form_scope_org_disabled_tooltip`, `templates_form_submit`, `templates_form_cancel`, `templates_form_deleting`, `templates_form_saving`, `templates_form_error_org_admin_only`, `templates_form_error_prompt_too_long`, `templates_form_error_name_required`, `templates_form_error_prompt_required`, `templates_form_error_slug_conflict`.

### REQ-TEMPLATES-UI-ROUTETREE — Route tree regeneration

**Ubiquitous**
- U1: After adding `/app/templates/new` and `/app/templates/$slug/edit`, `src/routeTree.gen.ts` SHALL be regenerated via the portal-frontend's codegen command.

---

## Acceptance Scenarios (Given-When-Then, 36 total)

Observable evidence required for each scenario (DOM assertion, network-request log, router state, CSS class string).

### List page (SCEN-LIST-1 … 8)
- **LIST-1** Empty org → empty-state + CTA button with "Eerste template aanmaken".
- **LIST-2** Populated with the 4 NL defaults from backend seeder → divider-row list with all 4 names.
- **LIST-3** Admin sees delete control on EVERY row, including rows created by other users.
- **LIST-4** Non-admin cannot delete others' templates (control disabled + tooltip explaining).
- **LIST-5** Delete happy-path via `InlineDeleteConfirm`: confirm → DELETE request fires → row vanishes → ChatConfigBar template-picker drops the option.
- **LIST-6** Active-indicator renders when row's template ID is in caller's `active_template_ids`.
- **LIST-7** Admin CTA label = "Nieuwe template"; non-admin CTA label = "Nieuwe persoonlijke template".
- **LIST-8** Click edit → `useNavigate` to `/app/templates/{slug}/edit` with exact slug.

### Form (SCEN-FORM-1 … 10)
- **FORM-1** Admin new-form → default scope "Organisatie", both options enabled.
- **FORM-2** Non-admin new-form → default scope "Persoonlijk", "Organisatie" disabled + tooltip "Alleen beheerders".
- **FORM-3** Empty name → client-side error, no POST.
- **FORM-4** Empty prompt_text → client-side error, no POST.
- **FORM-5** Paste 8001-char prompt → client-side `templates_form_error_prompt_too_long`.
- **FORM-6** Non-admin bypass (devtools) → server 403 with NL `templates_form_error_org_admin_only` renders inline.
- **FORM-7** Edit mode pre-populates all 4 fields from GET `/{slug}`.
- **FORM-8** Edit PATCH succeeds → redirect + 3 cache-invalidations + ChatConfigBar reflects change.
- **FORM-9** Slug conflict 409 → inline error on Naam field.
- **FORM-10** Char counter transitions: < 7800 gray → ≥ 7800 amber → ≥ 8000 destructive red.

### Paraglide i18n (SCEN-I18N-1 … 3)
- **I18N-1** NL locale → all strings from `messages/nl.json`, no hard-coded Dutch in DOM.
- **I18N-2** EN locale → strings from `messages/en.json`; key parity verified.
- **I18N-3** Missing locale entry → caught at build/test time (paraglide codegen fails, or test assertion detects).

### Design compliance (SCEN-DESIGN-1 … 7)
- **DESIGN-1** List container = `mx-auto max-w-3xl px-6 py-10` (CSS computed style).
- **DESIGN-2** Form container = `mx-auto max-w-lg px-6 py-10`.
- **DESIGN-3** Primary submit button has class strings `rounded-full` AND `bg-gray-900` AND `text-white`.
- **DESIGN-4** Error messages use `text-[var(--color-destructive)]` — no `text-red-*` grep-hit in `/app/templates/**`.
- **DESIGN-5** No `uppercase`, no `tracking-wider`, no `tracking-[0.04em]` classes anywhere in `/app/templates/**`.
- **DESIGN-6** No inline `style={{ fontFamily }}` in `/app/templates/**`.
- **DESIGN-7** Page title uses `page-title text-[26px] font-display-bold text-gray-900`.

### RouteTree (SCEN-RT-1)
- **RT-1** After committing route additions, `src/routeTree.gen.ts` includes `/app/templates/new` and `/app/templates/$slug/edit` paths.

### Playwright e2e (SCEN-E2E-1 … 4) — implementer stubs + fills
- **E2E-1** Login → navigate `/app/templates` → "Nieuwe template" → fill form → Opslaan → back on list with new row.
- **E2E-2** Edit → change prompt_text → Opslaan → list shows updated row.
- **E2E-3** Delete → InlineDeleteConfirm → row removed.
- **E2E-4** Full chat integration: create template → open ChatConfigBar → activate → send chat via iframe → verify template prompt_text reaches LiteLLM system-message (check `_klai_kb_meta` or direct LiteLLM log).

### Edge cases (EDGE-1 … 3)
- **EDGE-1** Network failure during save → mutation error toast, form fields retained.
- **EDGE-2** Double-submit while in-flight → button disabled blocks the second click.
- **EDGE-3** Stale cache after delete → optimistic update removes row immediately; onSettled refetch confirms.

---

## Files to Modify / Create

### NEW
- `klai-portal/frontend/src/routes/app/templates/new.tsx` — 15-line route wrapper
- `klai-portal/frontend/src/routes/app/templates/$slug.edit.tsx` — 60-80 lines, fetch + form
- `klai-portal/frontend/src/routes/app/templates/-template-form.tsx` — ~220 lines shared form
- `klai-portal/frontend/src/routes/app/templates/__tests__/templates-list.test.tsx`
- `klai-portal/frontend/src/routes/app/templates/__tests__/templates-form.test.tsx`
- `klai-portal/frontend/tests/e2e/templates.spec.ts` — Playwright stub

### MODIFY
- `klai-portal/frontend/src/routes/app/templates/index.tsx` — REWRITE placeholder → full list + delete flow
- `klai-portal/frontend/messages/nl.json` — +~29 keys
- `klai-portal/frontend/messages/en.json` — same keys (EN)
- `klai-portal/frontend/src/routeTree.gen.ts` — regenerated (auto, not manual)

---

## Exclusions (What NOT to Build)

- **ChatConfigBar changes** — already complete in SPEC-PORTAL-REDESIGN-002 Phase 3; do not touch.
- **Rules frontend** — belongs to SPEC-CHAT-GUARDRAILS-001.
- **Backend CRUD endpoints** — already built in SPEC-CHAT-TEMPLATES-001 (same worktree).
- **Per-KB template-scoping UI** — v1 only `org` / `personal`.
- **Bulk operations** (import/export) — not in v1.
- **Template versioning UI** — no rollback mechanic in backend.
- **Existing paraglide keys** (`templates_page_title`, `templates_empty_title`, `templates_empty_description`) — keep as-is.
- **LiteLLM hook adjustments** — already implemented.
- **Backend route or model changes** — frozen for this SPEC.
- **Amber re-introduction on buttons** — deferred to `SPEC-PORTAL-POLISH-001`.

---

## Klai-architectuur hard constraints (checklist in plan.md Appendix A)

- [x] Page layout widths per portal-patterns.md
- [x] Typography — Parabole only in h1, sentence-case elsewhere, no `uppercase`
- [x] Buttons `rounded-full`, primary `bg-gray-900`, no amber
- [x] Inputs `rounded-lg`, `border-gray-200`, focus via `--color-ring`
- [x] Tables/lists divider-row, no card wrapper
- [x] Empty state with dashed border + Sliders icon
- [x] Form structure `space-y-4` / `space-y-1.5`
- [x] Error via semantic `--color-destructive`, not `red-*`
- [x] Text colors `text-gray-900` primary, `text-gray-400` muted
- [x] Borders `border-gray-200` literal
- [x] Icons Lucide React, sizes per portal-patterns.md
- [x] No black-alpha layering on form pages
- [x] Every user-facing string via paraglide (NL + EN parity)
- [x] No AskUserQuestion anywhere
