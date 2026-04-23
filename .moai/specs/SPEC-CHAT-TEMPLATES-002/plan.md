# Implementation Plan — SPEC-CHAT-TEMPLATES-002

**SPEC:** SPEC-CHAT-TEMPLATES-002 — Prompt Templates frontend CRUD pages (`/app/templates`)
**Status:** draft
**Author:** Mark Vletter
**Created:** 2026-04-23

---

## Approach

**Bottom-up:** bouw van fundament (paraglide i18n) via het shared form-component naar de route-wrappers, dan de list rewrite, en sluit af met tests + e2e. Deze volgorde voorkomt "laten we alvast UI bouwen en i18n later doen" (een bekend anti-pattern in de codebase) en garandeert dat er nooit inline Dutch literals in TSX belanden.

**Scope-gedreven:** backend is klaar (SPEC-CHAT-TEMPLATES-001 op dezelfde branch). We bouwen uitsluitend onder `klai-portal/frontend/src/routes/app/templates/*` plus paraglide messages. We raken de ChatConfigBar niet aan — die heeft alleen een cache-invalidation-hook nodig via query-keys die we al kennen (`['app-templates-for-bar']`).

**Design-gedreven:** elk zichtbaar onderdeel wordt vooraf getoetst aan `.claude/rules/klai/design/portal-patterns.md`. De compliance-checklist in Appendix A is het exit-criterium per fase; geen fase mag afsluiten met een openstaand item uit die lijst.

---

## Dependency Graph

```
Fase A (paraglide i18n)
  |
  v
Fase B (shared form component -template-form.tsx)
  |
  +---> Fase C (route wrappers new.tsx + $slug.edit.tsx)
  |            |
  |            v
  v            (+ routeTree regeneration — Fase E)
Fase D (list index.tsx rewrite)
  |
  +-----> Fase E (routeTree regen + dev-server smoke)
              |
              v
Fase F (unit tests op form + list)
  |
  v
Fase G (Playwright e2e happy-path + chat-bar integratie)
```

Fases A, B, C, D kunnen niet parallel — elke fase levert het fundament voor de volgende. Fase F en G kunnen parallel uitgevoerd worden door de implementer-agent zodra D + E stabiel zijn.

---

## Milestones (Priority-based)

### Priority High — Foundation (Fase A + B)

#### Fase A — Paraglide messages (NL + EN)

**Deliverable:** alle nieuwe keys uit REQ-TEMPLATES-UI-I18N toegevoegd aan `messages/nl.json` en `messages/en.json` met matching structure.

**Tasks:**

1. Voeg in `klai-portal/frontend/messages/nl.json` de ~32 nieuwe keys toe onder prefix `templates_list_*` en `templates_form_*` (zie spec.md REQ-I18N-U3 lijst).
2. Voeg dezelfde keys toe aan `klai-portal/frontend/messages/en.json` met Engelse vertalingen.
3. Controleer dat `templates_page_title`, `templates_empty_title`, `templates_empty_description` ongewijzigd blijven (owned by SPEC-PORTAL-REDESIGN-002).
4. Run paraglide compile-step (`pnpm --filter klai-portal-frontend run paraglide` of dev-server) om `src/paraglide/messages.ts` types te regenereren.
5. Verifieer: grep op één van de nieuwe keys in de gegenereerde TypeScript output — moet als exported function bestaan.

**Acceptance hook:** SCEN-I18N-1, SCEN-I18N-2 uit acceptance.md.

**Duration:** Priority High, blokt alles ná deze fase.

---

#### Fase B — Shared form component `-template-form.tsx`

**Deliverable:** `klai-portal/frontend/src/routes/app/templates/-template-form.tsx` — een herbruikbaar formulier-component dat zowel new- als edit-mode afhandelt, inclusief validatie, mutations, error surfaces en admin-gate.

**Tasks:**

1. Definieer TypeScript interface:
   ```ts
   type TemplateFormMode = "new" | "edit";
   interface TemplateFormProps {
     mode: TemplateFormMode;
     initialForm: TemplateFormState;
     slug?: string; // required for edit mode
   }
   interface TemplateFormState {
     name: string;
     description: string;
     prompt_text: string;
     scope: "org" | "personal";
   }
   const EMPTY_TEMPLATE_FORM: TemplateFormState = {
     name: "",
     description: "",
     prompt_text: "",
     scope: "personal", // default overridden to "org" for admins in component
   };
   ```
2. Bouw layout: `<div className="mx-auto max-w-lg px-6 py-10">` → `<header>` (page-title + subtitle) → `<form className="space-y-4">` → fields → action row.
3. Fields bouwen met bestaande shadcn/ui primitives:
   - Naam: `<Input>` met `maxLength={128}` + paraglide label/placeholder
   - Beschrijving: `<Input>` met `maxLength={500}` + paraglide label/placeholder
   - Prompt-instructies: `<Textarea>` (of `<textarea className="rounded-lg border border-gray-200 text-sm min-h-[200px] max-h-[400px] resize-y focus:outline-none focus:ring-2 focus:ring-[var(--color-ring)]">`) met `maxLength={8000}` + character counter component
   - Bereik: `<Select>` met opties "Organisatie" / "Persoonlijk"; scope="org" option `disabled={!isAdmin}` + tooltip via `<TooltipProvider>` wrapper
4. Character counter: kleine `<p className="mt-1 text-xs text-gray-400 text-right">` onder de textarea, met formatter `{current} / 8000`; conditional classes: `text-amber-600` als > 7500, `text-[var(--color-destructive)]` als === 8000.
5. Action row: `<div className="flex items-center justify-between gap-3 pt-2">` met:
   - Primary submit: `<Button className="bg-gray-900 text-white hover:bg-gray-800 rounded-full">` — label via `templates_form_submit` / `templates_form_saving` conditional
   - Back link: `<Link to="/app/templates" className="text-sm text-gray-400 hover:text-gray-900">` — label via `templates_form_cancel`
6. Client-side validatie (vóór mutation):
   - name.trim() === "" → error via `templates_form_error_name_required`
   - prompt_text.trim() === "" → error via `templates_form_error_prompt_required`
   - prompt_text.length > 8000 → error via `templates_form_error_prompt_too_long` (zou niet mogen gebeuren door maxLength maar paste-protectie)
7. Mutations via TanStack Query:
   - **Create:** `useMutation({ mutationFn: (body) => apiFetch('/api/app/templates', { method: 'POST', body: JSON.stringify(body) }), onSuccess: invalidateAll, onError: handleError })`
   - **Update:** `useMutation({ mutationFn: (body) => apiFetch('/api/app/templates/${slug}', { method: 'PATCH', body: JSON.stringify(body) }), onSuccess: invalidateAll, onError: handleError })`
   - `invalidateAll` = `Promise.all([queryClient.invalidateQueries(['app-templates']), queryClient.invalidateQueries(['app-templates-for-bar']), queryClient.invalidateQueries(['kb-preference'])])`
   - `onSuccess` also triggers `navigate({ to: '/app/templates' })`
8. Error handling:
   - Parse error response; als status === 403 en path bevat `/api/app/templates` → render `templates_form_error_org_admin_only` boven of onder action row
   - Status === 409 → render `templates_form_error_slug_conflict` aan Naam field
   - Status === 400 en body.prompt_text too long → render `templates_form_error_prompt_too_long`
   - Andere errors → render `templates_form_error_generic` met message fallback
9. Admin check: haal `isAdmin` uit bestaande user context (session query of role-hook); als non-admin, forceer `scope: "personal"` in state en disable "Organisatie" option.
10. Voeg `@MX:ANCHOR` JSDoc-comment boven `TemplateFormPage` export (fan_in = 2).
11. Voeg `@MX:WARN` + `@MX:REASON` comment bij de admin-gate logica (server is autoriteit).

**Acceptance hook:** SCEN-FORM-1 t/m SCEN-FORM-10, SCEN-DESIGN-1, SCEN-DESIGN-2, SCEN-DESIGN-3.

**Duration:** Priority High. Dit is het hart van de SPEC; verwacht meerdere iteraties om portal-patterns.md 100% compliant te krijgen.

---

### Priority High — Route wiring (Fase C + D + E)

#### Fase C — Route wrappers

**Deliverable:** twee wrapper route-files die `TemplateFormPage` mounten voor new- en edit-mode.

**Tasks:**

1. **`new.tsx`** (~15 regels):
   ```tsx
   import { createFileRoute } from '@tanstack/react-router';
   import { TemplateFormPage, EMPTY_TEMPLATE_FORM } from './-template-form';
   import { ProductGuard } from '@/components/ProductGuard';

   export const Route = createFileRoute('/app/templates/new')({
     component: NewTemplatePage,
   });

   function NewTemplatePage() {
     return (
       <ProductGuard product="chat">
         <TemplateFormPage mode="new" initialForm={EMPTY_TEMPLATE_FORM} />
       </ProductGuard>
     );
   }
   ```

2. **`$slug.edit.tsx`** (~60-80 regels):
   - `createFileRoute('/app/templates/$slug/edit')`
   - Gebruik `useParams()` → `slug`
   - `useQuery(['app-template', slug], () => apiFetch<Template>('/api/app/templates/${slug}'))`
   - While loading → render skeleton of `<p>{m.templates_form_loading()}</p>` in de max-w-lg container
   - On error → inline error met `templates_form_error_generic` en retry button
   - On success → `<ProductGuard product="chat"><TemplateFormPage mode="edit" slug={slug} initialForm={mapToFormState(data)} /></ProductGuard>`
   - `mapToFormState` = helper die `Template` → `TemplateFormState` projecteert
3. Route-files krijgen GEEN inline Dutch literals — alleen paraglide.

**Acceptance hook:** SCEN-FORM-7 (edit load flow), SCEN-FORM-8 (edit save flow).

**Duration:** Priority High.

---

#### Fase D — List rewrite `index.tsx`

**Deliverable:** `klai-portal/frontend/src/routes/app/templates/index.tsx` opnieuw geschreven van placeholder naar volledige list met delete flow.

**Tasks:**

1. Container: `<div className="mx-auto max-w-3xl px-6 py-10">`.
2. Header block:
   ```tsx
   <div className="flex items-start justify-between gap-4 mb-8">
     <div>
       <h1 className="page-title text-[26px] font-display-bold text-gray-900">
         {m.templates_page_title()}
       </h1>
       <p className="mt-1 text-sm text-gray-500">{m.templates_page_subtitle()}</p>
     </div>
     <Button
       asChild
       className="bg-gray-900 text-white rounded-full"
     >
       <Link to="/app/templates/new">
         {isAdmin ? m.templates_list_create_button() : m.templates_list_create_personal_button()}
       </Link>
     </Button>
   </div>
   ```
3. Query: `useQuery(['app-templates'], () => apiFetch<Template[]>('/api/app/templates'))`.
4. Conditional render:
   - Loading → skeleton rows
   - Error → inline error
   - `data.length === 0` → empty state (hergebruik bestaande copy):
     ```tsx
     <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
       <Sliders className="h-10 w-10 text-gray-300 mx-auto" aria-hidden />
       <h2 className="mt-4 text-base font-medium text-gray-900">{m.templates_empty_title()}</h2>
       <p className="mt-1 text-sm text-gray-400">{m.templates_empty_description()}</p>
       <Button asChild className="mt-6 bg-gray-900 text-white rounded-full">
         <Link to="/app/templates/new">
           {isAdmin ? m.templates_list_create_button() : m.templates_list_create_personal_button()}
         </Link>
       </Button>
     </div>
     ```
   - `data.length > 0` → divider-row list (portal-patterns.md section-style):
     ```tsx
     <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
       {data.map((t) => (
         <TemplateRow key={t.slug} template={t} isAdmin={isAdmin} callerId={callerId} />
       ))}
     </div>
     ```
5. `TemplateRow` component (extract inline or co-located):
   - `<div className="flex items-start justify-between gap-4 py-3.5 px-2">`
   - Left: name (bold text-gray-900), description (truncate text-gray-400 text-sm), scope badge (`<Badge variant="secondary" className="rounded-full mt-1">{m.templates_list_scope_org()} / m.templates_list_scope_personal()}</Badge>`), active-indicator chip als in `activeTemplateIds`
   - Right: edit-button (`<Button variant="ghost" size="icon"><Pencil className="h-4 w-4" /></Button>`), delete-control wrapped in `<InlineDeleteConfirm>` (hidden if not owner/admin)
6. Delete mutation:
   ```tsx
   const deleteMutation = useMutation({
     mutationFn: (slug) => apiFetch(`/api/app/templates/${slug}`, { method: 'DELETE' }),
     onSuccess: () => Promise.all([
       queryClient.invalidateQueries(['app-templates']),
       queryClient.invalidateQueries(['app-templates-for-bar']),
     ]),
   });
   ```
7. Gate edit + delete controls op `isAdmin || template.created_by === callerId`.
8. Voeg `@MX:NOTE` boven `TemplatesPage` export (mirror portal-patterns.md list-row pattern).

**Acceptance hook:** SCEN-LIST-1 t/m SCEN-LIST-8.

**Duration:** Priority High.

---

#### Fase E — RouteTree regeneratie + dev-server smoke

**Deliverable:** `routeTree.gen.ts` regenerated, dev-server laadt `/app/templates`, `/app/templates/new`, `/app/templates/slug-van-seed/edit` zonder errors.

**Tasks:**

1. Start portal dev-server: `pnpm --filter klai-portal-frontend run dev` (of het repo-standaard commando).
2. Verifieer dat TanStack Router's file-based router de nieuwe routes oppikt — check console voor regeneratie-messages.
3. Als regeneratie niet auto-triggers: run expliciet `pnpm --filter klai-portal-frontend run typegen` (of vervangend codegen commando uit de workspace).
4. Open in browser: `/app/templates` (moet list/empty state tonen), `/app/templates/new` (form), `/app/templates/klantenservice/edit` (seed-slug uit SPEC-CHAT-TEMPLATES-001).
5. Commit `routeTree.gen.ts` — niet met de hand editen.

**Acceptance hook:** SCEN-RT-1.

**Duration:** Priority High (klein maar gating).

---

### Priority Medium — Tests

#### Fase F — Unit tests (Vitest)

**Deliverable:** twee test-files onder `__tests__/` dir naast de components.

**Tasks:**

1. **`templates-form.test.tsx`:**
   - Renders with `mode="new"` + admin user → scope select heeft beide opties enabled, default is "Organisatie"
   - Renders with `mode="new"` + non-admin → "Organisatie" option disabled, default "Persoonlijk"
   - Submit met lege name → shows `templates_form_error_name_required`
   - Submit met lege prompt_text → shows `templates_form_error_prompt_required`
   - Submit met >8000 char prompt_text (paste-sim) → shows `templates_form_error_prompt_too_long`
   - Container heeft class `max-w-lg` (design check via testing-library `toHaveClass`)
   - Primary button heeft class `rounded-full` (design check)
   - Error text gebruikt CSS var `--color-destructive` (design check — snapshot of computed style)
2. **`templates-list.test.tsx`:**
   - Renders empty state wanneer `data = []`
   - Renders rows wanneer `data` niet-leeg
   - Delete button niet zichtbaar op row waar `template.created_by !== callerId` en user is non-admin
   - Delete button zichtbaar op zelfde row als user IS admin
   - Empty-state container heeft `border-dashed`
   - List container heeft `divide-y` (geen card wrapper!)
3. Mock `apiFetch` via `vi.fn()` + mock paraglide messages via module-mock.
4. Run: `pnpm --filter klai-portal-frontend run test templates`.

**Acceptance hook:** Definition of Done items 1-3.

**Duration:** Priority Medium. Blokt merge maar niet preview.

---

#### Fase G — Playwright e2e `templates.spec.ts`

**Deliverable:** stub van e2e scenario's die in de run-fase volledig geïmplementeerd worden; deze SPEC levert de scenario-skeletten + eerste werkende happy-path.

**Tasks:**

1. Setup: login als org-admin seed-account, navigate to `/app/templates`.
2. **Happy flow:**
   - Assert: "Nieuwe template" button visible
   - Click → url === `/app/templates/new`
   - Fill name="E2E Test Template", description="E2E", prompt_text="Je bent een test-bot."
   - Scope blijft default "Organisatie" (admin)
   - Click "Opslaan"
   - Assert: url === `/app/templates` en row met name "E2E Test Template" zichtbaar
3. **Edit flow:**
   - Click edit-icon op E2E row
   - Wijzig prompt_text naar "Je bent een bijgewerkte test-bot."
   - Save
   - Assert: terug op list, row update reflected (open detail of check tooltip)
4. **Delete flow:**
   - Click delete op E2E row → `InlineDeleteConfirm` verschijnt
   - Click confirm
   - Assert: row verdwijnt
5. **ChatConfigBar integratie:**
   - Na create van "E2E Test Template": navigate naar `/app`
   - Open template-picker dropdown in ChatConfigBar
   - Assert: "E2E Test Template" in dropdown
   - Activate template (toggle on)
   - Assert: `active_template_ids` in KB-preference bevat de slug (via network mock of preference query)
6. Run: `pnpm --filter klai-portal-frontend exec playwright test templates`.

**Acceptance hook:** SCEN-E2E-1 t/m SCEN-E2E-4.

**Duration:** Priority Medium. Kan parallel met Fase F.

---

## Technical Approach

### State management
- TanStack Query voor alle server-state (templates list, single template, delete mutation, create/update mutation).
- Geen Redux, geen Zustand — volg portal-convention.
- Local form state via `useState` in `-template-form.tsx`; geen React Hook Form nodig voor 4 velden.

### Styling
- Uitsluitend Tailwind CSS met tokens uit `portal-patterns.md`.
- Geen inline `style={{}}` objects voor visuele props (kleur, font, spacing). Uitzondering: dynamische widths die geen Tailwind-class hebben.
- Gebruik semantische CSS variables (`--color-ring`, `--color-destructive`) in plaats van hardcoded palette-namen.

### Routing
- TanStack Router file-based. De file-prefix `-` voor `-template-form.tsx` garandeert dat het component niet als route geregistreerd wordt.
- `createFileRoute` paths MUST match directory path; `$slug.edit.tsx` → `/app/templates/$slug/edit`.

### i18n
- Paraglide voor alle user-facing copy. Geen `react-intl` / `i18next` / `lingui` (portal-standard).
- Nieuwe keys gebruiken namespace-prefix `templates_list_` of `templates_form_`.

### Testing
- Vitest + `@testing-library/react` voor unit.
- Playwright voor e2e (bestaat al in repo volgens observations — scope stub hier, implementer refined).

---

## Risks + Mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|-----------|
| 1 | `routeTree.gen.ts` conflicts door parallel-werk of stale regeneratie | Medium | Regenereer na elke route-toevoeging. Document dev-server start in README van deze SPEC. Commit `routeTree.gen.ts` als part of de feature-commit. |
| 2 | Textarea grows boundless bij lange prompts | Low | `resize-y` + `max-h-[400px]` + char counter; gecovered door REQ-FORM-U2. |
| 3 | Race bij optimistic update (delete flow) | Medium | Gebruik `onSettled` (niet enkel `onSuccess`) om zeker te refetchen; disable delete-button tijdens pending. |
| 4 | `InlineDeleteConfirm` gedrag bij rapid click (multiple triggers) | Low | Button disabled tijdens mutation via TanStack Query `isPending` state. |
| 5 | Admin-gate inconsistency tussen frontend en backend | High | Frontend render-logic is **defensief** (verberg wat niet mag); backend 403 is **autoriteit**. Beide layers required. REQ-FORM-N3 verbiedt frontend-only gating. |
| 6 | Scope-promotion flow (personal → org) in edit-mode door non-admin | Medium | Frontend disable'd de "Organisatie" option ook in edit-mode voor non-admin; backend 403 fires bij bypass. REQ-FORM-S1 + REQ-FORM-E5. |
| 7 | Paraglide key-name collisions met bestaande keys uit SPEC-PORTAL-REDESIGN-002 | Low | Alle nieuwe keys krijgen prefix `templates_list_` of `templates_form_`; `templates_page_title`, `templates_empty_title`, `templates_empty_description` blijven ongewijzigd. REQ-I18N-U3. |
| 8 | Edit-route slug-conflict na rename (backend genereert nieuwe slug) | Medium | Backend returnt nieuwe slug in response; frontend `onSuccess` navigeert naar `/app/templates` (list) — niet naar oude edit-URL. Geen 404 risk. |

---

## MX Plan

| Symbol | Tag | Phase added |
|--------|-----|-------------|
| `TemplateFormPage` (export in `-template-form.tsx`) | `@MX:ANCHOR` | Fase B |
| `TemplatesPage` (export in `index.tsx`) | `@MX:NOTE` | Fase D |
| Admin-gate branch in `TemplateFormPage` | `@MX:WARN` + `@MX:REASON` | Fase B |
| Textarea `maxLength={8000}` literal | `@MX:NOTE` | Fase B |
| Delete mutation in `TemplatesPage` | `@MX:NOTE` "Invalidate both app-templates and app-templates-for-bar for ChatConfigBar sync" | Fase D |

---

## Appendix A — portal-patterns.md compliance checklist

Doorlopen **per fase** voor elk visueel onderdeel. Elk vinkje = positief. Leeg vinkje = blocker.

### Layout
- [ ] List page: `mx-auto max-w-3xl px-6 py-10`
- [ ] Form page: `mx-auto max-w-lg px-6 py-10`
- [ ] Geen card-wrapper (`bg-white rounded-lg shadow-sm`) om list-rows
- [ ] Empty-state: `rounded-lg border border-dashed border-gray-200 py-16 text-center`

### Typography
- [ ] Page heading: `page-title text-[26px] font-display-bold text-gray-900`
- [ ] Body: system-ui (default), GEEN inline `fontFamily`
- [ ] Sentence-case overal
- [ ] GEEN `uppercase` op prose/buttons
- [ ] GEEN `tracking-wider` / `tracking-[0.04em]` op prose

### Buttons
- [ ] Primary: `bg-gray-900 text-white rounded-full`
- [ ] Secondary/back: `text-gray-400 hover:text-gray-900`
- [ ] Destructive: via `InlineDeleteConfirm` component (geen custom rood)
- [ ] GEEN `bg-amber-*` / `bg-yellow-*` op buttons (amber = focus-ring + logo only)

### Inputs / Selects / Textarea
- [ ] `rounded-lg`
- [ ] `border-gray-200`
- [ ] `text-sm`
- [ ] Focus via `--color-ring` (amber)
- [ ] Textarea: `min-h-[200px]`, `max-h-[400px]`, `resize-y`

### Tables / Lists
- [ ] Section-style (geen card)
- [ ] `divide-y divide-gray-200 border-t border-b border-gray-200`
- [ ] Row: `py-3.5 px-2`

### Badges
- [ ] `rounded-full`
- [ ] `<Badge variant="secondary">` voor scope labels

### Form Structure
- [ ] Form: `space-y-4`
- [ ] Label + input group: `space-y-1.5`
- [ ] Action row: `gap-3`

### Error text
- [ ] `text-sm text-[var(--color-destructive)]`
- [ ] GEEN `text-red-600`, `text-red-500`

### Colors (prose)
- [ ] Primary text: `text-gray-900`
- [ ] Muted/meta: `text-gray-400`

### Borders
- [ ] `border-gray-200` literal (geen `border-slate-*`, `border-neutral-*`)

### Icons
- [ ] Lucide React
- [ ] Inline: `h-4 w-4` (16px)
- [ ] Empty-state hero: `h-10 w-10` (40px)

### Layering
- [ ] Geen black-alpha overlays op form-pagina's
- [ ] Plain `bg-white` (of geen expliciete bg → inherit)

### i18n
- [ ] Elke TSX-string via `@/paraglide/messages` (geen inline NL)
- [ ] Beide locales (nl.json + en.json) gesynced

---

End of plan.md
