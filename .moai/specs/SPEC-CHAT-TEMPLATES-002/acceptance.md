# Acceptance Criteria — SPEC-CHAT-TEMPLATES-002

**SPEC:** SPEC-CHAT-TEMPLATES-002 — Prompt Templates frontend CRUD pages (`/app/templates`)
**Status:** draft
**Author:** Mark Vletter
**Created:** 2026-04-23

---

## Overview

This document defines the concrete, testable acceptance criteria for SPEC-CHAT-TEMPLATES-002. Criteria are organized into four areas:

1. **List page** (`/app/templates/`) — SCEN-LIST-*
2. **Form** (new + edit) — SCEN-FORM-*
3. **Paraglide i18n** — SCEN-I18N-*
4. **Design compliance** — SCEN-DESIGN-*
5. **RouteTree** — SCEN-RT-*
6. **Playwright e2e** (happy path + chat-bar integration) — SCEN-E2E-*

All scenarios use the Given/When/Then format. A feature is only considered delivered when every scenario passes.

---

## 1. List page scenarios

### SCEN-LIST-1 — Empty state renders for a fresh org with no templates

**Given** an org with zero templates visible to the caller
**And** the caller is authenticated and has `product="chat"` enabled
**When** the caller navigates to `/app/templates/`
**Then** the page SHALL render the empty-state container with:
- `rounded-lg border border-dashed border-gray-200 py-16 text-center` classes
- A `Sliders` icon sized `h-10 w-10 text-gray-300`
- NL copy from `templates_empty_title` and `templates_empty_description`
- A primary CTA button labelled "Nieuwe template" (admin) or "Nieuwe persoonlijke template" (non-admin)
- The CTA links to `/app/templates/new`

---

### SCEN-LIST-2 — Populated list renders the 4 NL default templates from SPEC-CHAT-TEMPLATES-001

**Given** an org with the 4 default NL templates seeded (Klantenservice, Formeel, Creatief, Samenvatter)
**And** the caller has "admin" role
**When** the caller navigates to `/app/templates/`
**Then** the page SHALL render a divider-row list with 4 rows
**And** each row SHALL display:
- Template name in bold `text-gray-900`
- Truncated description in `text-gray-400`
- Scope badge showing "Organisatie" (using `templates_list_scope_org`)
- Edit button (Pencil icon, `h-4 w-4`)
- Delete control (visible, enabled — admin privilege)
**And** the list container SHALL use `divide-y divide-gray-200 border-t border-b border-gray-200`
**And** NO card-wrapper classes (`bg-white rounded-lg shadow-sm`) SHALL be present on rows

---

### SCEN-LIST-3 — Admin sees delete control on all rows

**Given** an org with 4 seed templates (all `created_by = system` or other user)
**And** the caller has "admin" role
**When** the caller navigates to `/app/templates/`
**Then** each row SHALL show an enabled delete control
**And** clicking any delete control SHALL trigger the `InlineDeleteConfirm` flow

---

### SCEN-LIST-4 — Non-admin cannot delete others' templates

**Given** an org with 4 seed templates created by admin/system
**And** a personal template created by another non-admin member
**And** the caller has "member" (non-admin) role and owns zero of the templates
**When** the caller navigates to `/app/templates/`
**Then** delete controls on org-scope rows SHALL be hidden OR disabled
**And** delete control on other members' personal rows SHALL be hidden OR disabled
**And** an attempted DELETE via direct API call from the UI SHALL NOT be triggered

---

### SCEN-LIST-5 — Delete flow via InlineDeleteConfirm removes the row and refreshes ChatConfigBar

**Given** the caller is on `/app/templates/` with the 4 seed templates visible
**And** the caller is admin
**When** the caller clicks the delete control on the "Creatief" row
**Then** `InlineDeleteConfirm` SHALL appear inline on that row with confirm text from `templates_list_delete_confirm`
**When** the caller confirms deletion
**Then** a `DELETE /api/app/templates/creatief` HTTP request SHALL be sent
**And** on 204 response, the "Creatief" row SHALL disappear from the list
**And** the TanStack Query cache keys `['app-templates']` AND `['app-templates-for-bar']` SHALL be invalidated
**And** opening the ChatConfigBar template picker (on `/app`) SHALL no longer show "Creatief"

---

### SCEN-LIST-6 — Active-indicator renders when slug is in caller's active_template_ids

**Given** the caller has `active_template_ids = ["klantenservice"]` in their KB-preference
**And** the "Klantenservice" template is visible in the list
**When** the caller navigates to `/app/templates/`
**Then** the "Klantenservice" row SHALL show an active-indicator (dot or badge using `templates_list_active_label`)
**And** other rows SHALL NOT show the indicator

---

### SCEN-LIST-7 — CTA label depends on role

**Given** the caller is admin
**When** the caller navigates to `/app/templates/`
**Then** the primary CTA button SHALL read "Nieuwe template" (`templates_list_create_button`)

**Given** the caller is non-admin
**When** the caller navigates to `/app/templates/`
**Then** the primary CTA button SHALL read "Nieuwe persoonlijke template" (`templates_list_create_personal_button`)

---

### SCEN-LIST-8 — Clicking edit navigates to `/app/templates/{slug}/edit`

**Given** the caller is on `/app/templates/` with the "Formeel" row visible
**When** the caller clicks the edit button on the "Formeel" row
**Then** the router SHALL navigate to `/app/templates/formeel/edit`
**And** the form SHALL be pre-filled with the "Formeel" template data

---

## 2. Form scenarios (new + edit)

### SCEN-FORM-1 — Admin sees "Organisatie" as default scope and option is enabled

**Given** the caller is admin
**When** the caller navigates to `/app/templates/new`
**Then** the Bereik `<Select>` SHALL have "Organisatie" selected by default
**And** both "Organisatie" and "Persoonlijk" options SHALL be enabled
**And** NO tooltip "Alleen beheerders" SHALL appear on the "Organisatie" option

---

### SCEN-FORM-2 — Non-admin sees "Persoonlijk" default with "Organisatie" disabled

**Given** the caller is non-admin (member role)
**When** the caller navigates to `/app/templates/new`
**Then** the Bereik `<Select>` SHALL have "Persoonlijk" selected by default
**And** the "Organisatie" option SHALL be disabled
**And** hovering/focusing the disabled option SHALL show tooltip `templates_form_scope_org_disabled_tooltip` ("Alleen beheerders")

---

### SCEN-FORM-3 — Empty name triggers client-side validation

**Given** the caller is on `/app/templates/new`
**When** the caller leaves the Naam field empty and clicks "Opslaan"
**Then** NO HTTP request SHALL be sent
**And** the error `templates_form_error_name_required` ("Naam is verplicht") SHALL render near the Naam field
**And** the error element SHALL use the class `text-sm text-[var(--color-destructive)]`

---

### SCEN-FORM-4 — Empty prompt_text triggers client-side validation

**Given** the caller is on `/app/templates/new`
**And** the Naam field is filled ("Test")
**When** the caller leaves Prompt-instructies empty and clicks "Opslaan"
**Then** NO HTTP request SHALL be sent
**And** the error `templates_form_error_prompt_required` SHALL render near the textarea
**And** the error element SHALL use `text-[var(--color-destructive)]`

---

### SCEN-FORM-5 — Prompt exceeding 8000 chars triggers validation (paste scenario)

**Given** the caller is on `/app/templates/new`
**And** Naam is filled
**When** the caller pastes 8001 characters into Prompt-instructies
**Then** the textarea's `maxLength={8000}` attribute SHALL truncate to 8000
**Or** if the input somehow exceeds 8000 (programmatic), the error `templates_form_error_prompt_too_long` SHALL render on submit
**And** the character counter SHALL display "8000 / 8000" in `text-[var(--color-destructive)]`

---

### SCEN-FORM-6 — Non-admin bypassing frontend-gate receives server 403 with NL message

**Given** the caller is non-admin
**And** the caller has manipulated the DOM/state to submit `scope="org"`
**When** the caller clicks "Opslaan"
**Then** the POST `/api/app/templates` SHALL return HTTP 403 with NL error message
**And** the form SHALL render `templates_form_error_org_admin_only` ("Alleen beheerders mogen organisatie-templates aanmaken")
**And** the error element SHALL use `text-[var(--color-destructive)]`
**And** the form SHALL NOT navigate away from `/app/templates/new`

---

### SCEN-FORM-7 — Edit-mode loads existing template into the form

**Given** the "Klantenservice" template exists with name="Klantenservice", description="Vriendelijke klantenservice-toon", prompt_text="Je bent een vriendelijke medewerker...", scope="org"
**When** the caller navigates to `/app/templates/klantenservice/edit`
**Then** a GET `/api/app/templates/klantenservice` request SHALL be sent
**While** the query is pending, the form SHALL render loading state (`templates_form_loading`)
**And** on success, the form SHALL pre-fill:
- Naam = "Klantenservice"
- Beschrijving = "Vriendelijke klantenservice-toon"
- Prompt-instructies = full prompt text
- Bereik = "Organisatie" (selected)
**And** the page title SHALL read `templates_form_edit_title` ("Template bewerken")

---

### SCEN-FORM-8 — Edit save happy path invalidates caches and redirects

**Given** the caller is admin on `/app/templates/klantenservice/edit` with form pre-filled
**When** the caller changes prompt_text to "Je bent een superhulpvaardige medewerker..." and clicks "Opslaan"
**Then** a PATCH `/api/app/templates/klantenservice` request SHALL be sent with the updated body
**And** on 200 response, the router SHALL navigate to `/app/templates`
**And** the query keys `['app-templates']`, `['app-templates-for-bar']`, `['kb-preference']` SHALL be invalidated
**And** the list SHALL re-render with the updated description visible (if changed) or unchanged

---

### SCEN-FORM-9 — Slug-conflict 409 renders inline error on Naam field

**Given** the caller is admin on `/app/templates/new`
**And** a template with slug "test-template" already exists
**When** the caller enters name="Test Template" (which generates slug "test-template") and clicks "Opslaan"
**Then** POST `/api/app/templates` returns HTTP 409
**And** the error `templates_form_error_slug_conflict` SHALL render near the Naam field
**And** the caller SHALL remain on `/app/templates/new`

---

### SCEN-FORM-10 — Character counter transitions to warning/error colors at thresholds

**Given** the caller is on `/app/templates/new`
**When** the Prompt-instructies textarea contains 7000 characters
**Then** the counter SHALL display "7000 / 8000" in `text-gray-400`

**When** the textarea contains 7600 characters (> 7500 = 90%)
**Then** the counter SHALL turn warning color (amber or configured warn token)

**When** the textarea contains 8000 characters (= limit)
**Then** the counter SHALL display "8000 / 8000" in `text-[var(--color-destructive)]`

---

## 3. Paraglide i18n scenarios

### SCEN-I18N-1 — Page renders all copy from paraglide messages (NL locale)

**Given** the user's locale is "nl"
**When** the caller navigates to `/app/templates/` and `/app/templates/new`
**Then** every user-facing string (heading, subtitle, CTA button, form labels, placeholders, error messages, badges) SHALL come from `@/paraglide/messages`
**And** scanning the rendered DOM with Playwright's text-search SHALL find NO inline Dutch literals from source files (e.g., no "Nog geen templates" written directly in TSX — only via `m.templates_empty_title()`)

---

### SCEN-I18N-2 — Page renders English when locale is "en"

**Given** the user's locale is "en"
**When** the caller navigates to `/app/templates/`
**Then** the page heading, subtitle, CTA, and empty-state copy SHALL render in English from `messages/en.json`
**And** all new keys defined in REQ-I18N-U3 SHALL resolve (no fallback-to-key display like "templates_form_name_label")

---

### SCEN-I18N-3 — Missing locale entries are caught at build/test time

**Given** the source code references a paraglide message key
**When** that key is present in `nl.json` but missing in `en.json` (or vice versa)
**Then** paraglide's compile step or a pre-commit check SHALL fail
**And** no PR SHALL be mergeable with mismatched locales

---

## 4. Design compliance scenarios

### SCEN-DESIGN-1 — List page container width matches portal-patterns.md

**Given** the caller is on `/app/templates/`
**When** the rendered DOM is inspected
**Then** the outer page container SHALL have classes `mx-auto max-w-3xl px-6 py-10`
**And** NO class `max-w-2xl`, `max-w-4xl`, `max-w-lg` on the outer container

---

### SCEN-DESIGN-2 — Form container width matches portal-patterns.md

**Given** the caller is on `/app/templates/new` or `/app/templates/{slug}/edit`
**When** the rendered DOM is inspected
**Then** the outer form container SHALL have classes `mx-auto max-w-lg px-6 py-10`
**And** NO class `max-w-md`, `max-w-xl`

---

### SCEN-DESIGN-3 — Primary button uses rounded-full + bg-gray-900

**Given** any page in `/app/templates/*`
**When** a primary action button is rendered (CTA, submit)
**Then** the button's className SHALL include `bg-gray-900`, `text-white`, `rounded-full`
**And** the className SHALL NOT include `bg-amber-*`, `bg-yellow-*`, `rounded-lg` (on primary), `rounded-md` (on primary)

---

### SCEN-DESIGN-4 — Error messages use semantic destructive token

**Given** any error state (form validation, API error) renders on `/app/templates/*`
**When** the error `<p>` is inspected
**Then** its className SHALL include `text-[var(--color-destructive)]`
**And** its className SHALL NOT include `text-red-600`, `text-red-500`, `text-red-700`

---

### SCEN-DESIGN-5 — No anti-pattern utility classes present

**Given** any rendered DOM under `/app/templates/*`
**When** classNames are audited
**Then** NO element SHALL use `uppercase` (Tailwind)
**And** NO element SHALL use `tracking-wider` or `tracking-[0.04em]` on prose
**And** NO element SHALL use inline `style={{ fontFamily: ... }}`
**And** NO button SHALL use `bg-amber-*` or `bg-yellow-*`
**And** NO error text SHALL use `text-red-*` (use `text-[var(--color-destructive)]`)
**And** list rows SHALL NOT be wrapped in card-style containers (`bg-white rounded-lg shadow-sm`)

---

### SCEN-DESIGN-6 — Textarea has resize-y and bounded max-height

**Given** the caller is on `/app/templates/new`
**When** the Prompt-instructies textarea is inspected
**Then** its className SHALL include `resize-y`, `min-h-[200px]`, `max-h-[400px]`
**And** its className SHALL include `rounded-lg`, `border-gray-200`, `text-sm`

---

### SCEN-DESIGN-7 — Icons use Lucide React with correct sizing

**Given** any rendered DOM under `/app/templates/*`
**When** icons are inspected
**Then** inline action-icons (edit, delete) SHALL use Lucide React with classes `h-4 w-4`
**And** empty-state hero icon SHALL use Lucide React (`Sliders`) with classes `h-10 w-10 text-gray-300`

---

## 5. RouteTree scenarios

### SCEN-RT-1 — RouteTree registers both new.tsx and $slug.edit.tsx

**Given** the portal dev-server is running
**And** files `new.tsx` and `$slug.edit.tsx` exist under `src/routes/app/templates/`
**When** the TanStack Router codegen runs (dev-server hot reload or explicit typegen command)
**Then** `src/routeTree.gen.ts` SHALL contain route entries for:
- `/app/templates/new`
- `/app/templates/$slug/edit`
**And** the file `-template-form.tsx` SHALL NOT be registered as a route (prefix `-` is ignored by router)
**And** navigating to both URLs in the browser SHALL load the respective pages without 404

---

## 6. Playwright e2e scenarios (scope for run-fase)

### SCEN-E2E-1 — Full happy-path: login → create → verify in list

**Given** a test org is seeded with admin user credentials
**And** zero user-created templates (only the 4 defaults from SPEC-CHAT-TEMPLATES-001 may be present or cleaned)
**When** the test:
1. Logs in as admin
2. Navigates to `/app/templates/`
3. Clicks "Nieuwe template"
4. Fills name="E2E Happy Path", description="E2E", prompt_text="Je bent een test-bot."
5. Clicks "Opslaan"
**Then** the test SHALL assert:
- URL is `/app/templates` after save
- A row with name "E2E Happy Path" is visible
- The row shows scope badge "Organisatie"

---

### SCEN-E2E-2 — Edit flow: open → modify → save

**Given** the "E2E Happy Path" template from SCEN-E2E-1 exists
**When** the test:
1. Clicks the edit icon on the "E2E Happy Path" row
2. Changes prompt_text to "Je bent een bijgewerkte test-bot."
3. Clicks "Opslaan"
**Then** the test SHALL assert:
- URL is `/app/templates` after save
- Row is still present
- Opening detail (or re-editing) shows the updated prompt_text

---

### SCEN-E2E-3 — Delete flow via InlineDeleteConfirm

**Given** the "E2E Happy Path" template exists
**When** the test:
1. Clicks the delete control on the "E2E Happy Path" row
2. Observes `InlineDeleteConfirm` appearing inline
3. Clicks the confirm button
**Then** the test SHALL assert:
- DELETE `/api/app/templates/e2e-happy-path` returns 204
- The row disappears from the list within the test's default timeout

---

### SCEN-E2E-4 — ChatConfigBar integration: created template appears in picker and activates

**Given** the test has created "E2E Happy Path" via SCEN-E2E-1 and NOT deleted it
**When** the test:
1. Navigates to `/app`
2. Opens the ChatConfigBar template-picker dropdown
**Then** "E2E Happy Path" SHALL appear in the dropdown options
**When** the test toggles "E2E Happy Path" on (activation)
**Then** the KB-preference PATCH request SHALL include the slug in `active_template_ids`
**When** the test sends a chat message
**Then** the backend call to LiteLLM SHALL inject the activated template's prompt_text (verified via network-intercept or internal log assertion — implementation detail for run-fase)

---

## Edge cases and non-functional checks

### EDGE-1 — Network failure during save shows retry-able error

**Given** the network is offline OR API returns 503
**When** the caller clicks "Opslaan"
**Then** the error `templates_form_error_generic` SHALL render
**And** the button SHALL re-enable to permit retry

### EDGE-2 — Stale cache after external deletion

**Given** the caller has the list loaded
**When** another user deletes a template via API
**Then** the list MAY be stale until next refetch; no runtime error SHALL occur
**And** clicking edit on the stale row SHALL show a 404-handling error (not a white screen)

### EDGE-3 — Double-submit protection

**Given** the caller clicks "Opslaan" twice quickly
**When** the first mutation is still pending
**Then** the second click SHALL NOT trigger a second POST
**And** the button SHALL be visibly disabled with `templates_form_saving` copy

---

## Definition of Done

A SPEC-CHAT-TEMPLATES-002 implementation is considered DONE when **all** of the following are TRUE:

1. [ ] All files listed in spec.md "Files to Create / Modify" exist with the approximate LOC budgets honored
2. [ ] All new paraglide keys in spec.md REQ-I18N-U3 exist in BOTH `messages/nl.json` and `messages/en.json`
3. [ ] `routeTree.gen.ts` contains `/app/templates/new` and `/app/templates/$slug/edit` entries
4. [ ] Unit tests in `__tests__/templates-form.test.tsx` and `__tests__/templates-list.test.tsx` pass (`pnpm --filter klai-portal-frontend run test templates` green)
5. [ ] Playwright e2e `tests/e2e/templates.spec.ts` passes for SCEN-E2E-1 through SCEN-E2E-4
6. [ ] Portal-patterns.md compliance checklist in plan.md Appendix A has ALL items checked
7. [ ] All EARS requirements in spec.md are covered by at least one acceptance scenario in this document
8. [ ] No inline Dutch literals appear in any TSX file under `src/routes/app/templates/` (grep-check)
9. [ ] No `text-red-*`, `bg-amber-*`, `bg-yellow-*`, `uppercase`, `tracking-wider` classes appear in templates source (grep-check)
10. [ ] MX tags (`@MX:ANCHOR`, `@MX:NOTE`, `@MX:WARN`, `@MX:REASON`) are present on the symbols listed in plan.md "MX Plan"
11. [ ] Manual smoke test: create template → see in list → activate in ChatConfigBar → new chat message reflects template's prompt_text (verified via VictoriaLogs or LiteLLM request log)
12. [ ] No regressions in existing `/app/templates/` placeholder flow (empty-state still renders correctly for fresh orgs)
13. [ ] Lighthouse or axe-core accessibility audit on the form page scores zero critical violations
14. [ ] The changes land in the existing branch `feature/SPEC-CHAT-TEMPLATES-001` (shared with backend SPEC); both SPECs ship in one PR
15. [ ] Confidence statement ending implementation message: `Confidence: [0-100] — [evidence summary: tests green, browser verified, no anti-patterns]`

---

End of acceptance.md
