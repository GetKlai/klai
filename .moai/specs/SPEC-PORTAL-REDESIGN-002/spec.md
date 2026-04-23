---
id: SPEC-PORTAL-REDESIGN-002
version: 0.1.0
status: draft
created: 2026-04-23
author: Mark Vletter
priority: high
supersedes: SPEC-PORTAL-REDESIGN-001 (partial — design + IA extended; rules/templates backend added)
source_branch: feat/chat-first-redesign
---

# SPEC-PORTAL-REDESIGN-002: Chat-first Portal + Rules/Templates Foundation

## HISTORY

### v0.1.0 (2026-04-23)
- Extracts design, IA, rules-backend and templates-backend from `feat/chat-first-redesign` (Jantine)
- Explicitly scopes OUT: LiteLLM guardrails hook, new connector adapters, dev-CI, bun lockfile switch, website, submodule bumps
- Fixes internal contradictions in the design-system rule before adopting it
- Sets a v1 design-system spine that Jantine can swap later without per-file refactor
- Spawns follow-up SPECs: polish, enforcement, connectors (see bottom)

---

## Goal

Bring the chat-first redesign into `main` as a clean, reviewable integration — without absorbing the scope drift, tooling side-quests, and internal inconsistencies that accumulated on the source branch.

Core intent (unchanged from SPEC-001):

- **Chat** is the homepage — no tool grid
- **Kennis** is unified — collecties + notebooks + documenten in one view
- **Regels** is a first-class sidebar concept
- **Templates** is a first-class sidebar concept (added versus SPEC-001)
- Compliance/privacy is the enabler, not the headline

New intent in v002:

- Rules and Templates are stored and editable in v1 (backend included), but NOT enforced on LiteLLM chat calls (enforcement = separate SPEC)
- Button shape stabilizes at `rounded-full` in v1 to avoid a second shape-change when amber is reintroduced in polish
- All design inconsistencies between the source doc and source code are resolved before merge

---

## Success Criteria

1. User lands on chat directly after login — no speed bump, no tool grid
2. Sidebar: 4 end-user items (Chat, Kennis, Templates, Regels) + admin items for admins/group-admins
3. Rules page list/create/edit works against the rules backend (data-only, no chat enforcement)
4. Templates page list/create/edit works against the templates backend (data-only, no chat injection)
5. Unified kennis view shows collecties cards + notebooks/documenten list
6. All legacy routes redirect to new locations (no broken bookmarks)
7. New i18n keys merged per-key; existing keys are NOT replaced
8. Button identity: `rounded-full` pills, sentence-case, no uppercase anywhere in the app UI
9. Content bg white, sidebar bg cream, amber preserved only as focus-ring + logo reserve
10. `portal-design-system.md` is internally consistent (no rule-vs-example contradictions)
11. TypeScript strict passes; portal-frontend build green
12. Alembic `upgrade head` → `downgrade base` → `upgrade head` is idempotent on a clean DB
13. Playwright smoke tests pass for: chat-home, kennis unified view, rules list, templates list, a re-parented focus page, a redirected legacy route
14. No stray generated artefacts committed (`.tanstack/`, `bun.lock`, `.tmp-*` are gitignored or absent)

---

## Non-Goals (explicit — each becomes its own SPEC if needed)

- **LiteLLM guardrails hook** (`deploy/litellm/klai_knowledge.py`): rules/templates are data-only in v1 → `SPEC-RULES-ENFORCEMENT-001`
- **Active-template injection** at chat-call time → `SPEC-TEMPLATES-INJECTION-001`
- **New connector adapters** (airtable, confluence, gmail, slack, google_sheets, `_google_auth`) → per-connector SPEC
- **Dev-CI + `docker-compose.dev.yml` + `dev.md` + Caddy dev-routes** → `SPEC-DEV-INFRA-001`
- **Website changes** (`klai-website` submodule): left untouched
- **`klai-infra` submodule bump**: not required for this SPEC
- **Package-manager switch (`bun.lock`)**: rejected; keep current toolchain
- **Amber button polish + typography polish + layout rhythm resolution** → `SPEC-PORTAL-POLISH-001`
- **Replacing LibreChat with native chat** (SPEC-001 non-goal, still applies)
- **Admin area redesign**: admin pages receive visual refresh only (button/input style changes cascade) — no IA or behavior changes

---

## v1 Design-System Spine

Explicit, reversible choices. Jantine's polish SPEC can overwrite each row below in isolation without touching other rows.

### Color

| Concern | Mechanism | Why |
|---|---|---|
| Grayscale (text, borders, hover bg) | Tailwind literals — `gray-50/200/400/900` | Doc-stated rule; swap = codemod |
| Semantic palette (sidebar, destructive, success, warning, ring) | CSS tokens in `index.css` | Swap = one-file edit |
| Content background | `bg-white` | LibreChat continuity |
| Sidebar background | `bg-[var(--color-sidebar)]` (cream `#f5f4ef`) | Warm-neutral kept for navigation |
| Layering in dash | `bg-black/[0.06]` active, `bg-black/5` hover | Applied consistently, not just sidebar |
| Amber | Reserved as `--color-ring: #fcaa2d` + logo only | No amber on buttons or accents in v1 |

### Typography

- Default UI: system-ui inherited from `<main>` className
- Parabole: ONLY in page headings (`font-display-bold text-[26px]`) and collection names (`font-display text-[15px]`). No other contexts in v1.
- Case: **sentence-case everywhere**. No `uppercase` class anywhere in `klai-portal/frontend/src/`. Including tab headers (`text-xs uppercase tracking-wider` → `text-xs tracking-wide` or similar).

### Radius

| Element | Radius | Class |
|---|---|---|
| Buttons | 9999px | `rounded-full` |
| Badges | 9999px | `rounded-full` |
| Inputs / search / icon-containers | 8px | `rounded-lg` |
| Cards | 12px | `rounded-xl` |
| Sidebar items | 6px | `rounded-md` |

**Change from source branch**: buttons move from `rounded-lg` (branch-tip) to `rounded-full` in v1. Rationale: polish SPEC will restore amber buttons; stabilizing shape now avoids a second transition.

### Spacing, Icons, Containers

Adopt `portal-design-system.md` as-is (once internal contradictions are fixed):
- Container widths (`max-w-3xl` list, `max-w-lg` form, `mx-auto px-6 py-10`)
- Icon sizes (sidebar 18, inline 16, small 14, empty-state 40)
- Sidebar item classes (`ITEM_BASE`, `ITEM_ACTIVE`, `ICON_PROPS`)
- Form layout (`space-y-1.5` label-input, `space-y-4` fields)

### Fixes required in `portal-design-system.md` before adoption

1. Table example uses `border-[var(--color-border)]` — replace with `border-gray-200` to match the "grayscale via literals" rule
2. `--font-sans` in `index.css` has Parabole primary; doc says "never override fontFamily, system-ui is the default" — reconcile (recommended: `--font-sans` stays as a *brand fallback chain*, `<main>` className explicitly sets system-ui)
3. Remove contradiction between "use `gray-900` for primary text" and `--color-foreground: #191918` — document both: `gray-900` is the Tailwind literal for prose; `--color-foreground` is the token for component-level theming
4. Add explicit case-style rule: "No `uppercase` class, anywhere in the app"

### Deferred to POLISH-1 (explicit seams)

The following remain UNRESOLVED in v1. Implementing them now would pre-commit Jantine's direction.

1. Empty-state pattern — keep dashed-border (doc) and rules-page variant as-is
2. `space-y-6` vs `space-y-8` rhythm between sections — no normalization
3. `ghost` vs `outline` button — keep both variants; may collapse later
4. "Back/cancel" link styling — keep both patterns (`link` variant + inline muted)
5. Amber reintroduction on primary buttons — token preserved, not applied
6. Button typography: "amber pill, medium-bold black capitalized text" — polish concern
7. Parabole font-weight scale beyond 500/700 — polish concern

---

## Information Architecture

### Sidebar (end users)

| # | Label (NL) | Label (EN) | Icon (lucide) | Route | Product gate |
|---|---|---|---|---|---|
| 1 | Chat | Chat | `MessageSquare` | `/app` (end: true) | chat |
| 2 | Kennis | Knowledge | `BookOpen` | `/app/knowledge` | knowledge |
| 3 | Templates | Templates | `Sliders` | `/app/templates` | chat |
| 4 | Regels | Rules | `Scale` | `/app/rules` | chat |

### Sidebar (admin + group-admin append)

| Label (NL) | Icon | Route |
|---|---|---|
| Team | `Users` | `/admin/users` |
| MCPs | `Puzzle` | `/admin/mcps` |

Future (OUT of scope): DNA, Bots.

### Route Mapping (from SPEC-001, reconfirmed)

| Legacy | New | Mechanism |
|---|---|---|
| `/app/chat` | `/app/` | `redirect` in `beforeLoad` |
| `/app/focus` | `/app/knowledge` | `redirect` |
| `/app/focus/$id` | `/app/knowledge/focus/$id` | re-parented file |
| `/app/docs` | `/app/knowledge` | `redirect` |
| `/app/docs/$kbSlug` | `/app/knowledge/docs/$kbSlug` | re-parented file |
| `/app/gaps` | `/app/knowledge/gaps` | re-parented (admin-only) |
| `/app/transcribe/*` | `/app/transcribe/*` | unchanged, hidden from sidebar |

---

## Backend Scope

### Rules

Files to adopt from branch (verify each):
- `klai-portal/backend/app/models/rules.py`
- `klai-portal/backend/app/api/app_rules.py`
- `klai-portal/backend/app/services/default_rules.py`
- `klai-portal/backend/alembic/versions/49c788860eb3_add_portal_rules.py`
- `klai-portal/backend/alembic/versions/125e31c9e42b_add_rule_type.py`

Behavior in v1: data CRUD only. No LiteLLM enforcement.

### Templates

- `klai-portal/backend/app/models/templates.py`
- `klai-portal/backend/app/api/app_templates.py`
- `klai-portal/backend/app/services/default_templates.py`
- `klai-portal/backend/alembic/versions/f7a8b9c0d1e2_add_portal_templates.py`
- `klai-portal/backend/alembic/versions/a4b5c6d7e8f9_add_active_template_ids_to_portal_users.py`

Behavior in v1: user selects active templates; no chat-call injection.

### Portal user model

- `klai-portal/backend/app/models/portal.py` — add `active_template_ids` field (migration above)

### Main app wiring

- `klai-portal/backend/app/main.py` — register rules + templates routers (minimal diff only)
- `klai-portal/backend/app/core/config.py` — verify no feature-flag leakage from non-goal scope

### Rejected / deferred from branch

| File | Reason |
|---|---|
| `klai-portal/backend/app/services/pii_detector.py` | Tied to LiteLLM hook (non-goal) |
| `klai-portal/backend/app/services/file_parser.py` | Tied to new connectors (non-goal) |
| `klai-portal/backend/app/services/connector_credentials.py` changes | Tied to new connectors |
| `klai-portal/backend/app/services/provisioning/orchestrator.py` changes | Verify: if tied to rules/templates keep, if tied to connectors drop |
| `klai-portal/backend/app/services/knowledge_ingest_client.py` changes | Verify scope |
| `klai-portal/backend/app/api/internal.py` | Adopt only if required by rules/templates API |
| `klai-portal/backend/app/api/app_account.py` changes | Verify: active-template selection endpoint → adopt; other → defer |
| `klai-portal/backend/app/api/app_knowledge_bases.py` changes | Verify: visual/metadata only → adopt; new connector endpoints → defer |

Each "verify" item is resolved in Phase 4/5 by reading the file diff and categorizing per change block.

---

## Frontend Scope

### New routes
- `routes/app/index.tsx` (chat iframe + KBScopeBar wrapper)
- `routes/app/rules/{index,new,$slug.edit,-rule-form}.tsx`
- `routes/app/templates/{index,new,$slug.edit,-template-form}.tsx`
- `routes/app/knowledge/focus/*` (re-parented)
- `routes/app/knowledge/docs/*` (re-parented)

### Rewritten
- `routes/app/route.tsx` (sidebar 4 items + admin)
- `routes/app/chat.tsx` (redirect to `/app/`)
- `routes/app/knowledge/index.tsx` (unified kennis view)
- `routes/app/focus/index.tsx` (redirect)
- `routes/app/docs/index.tsx` (redirect)
- `routes/app/_components/KBScopeBar.tsx` (visual refresh)

### Visual refresh only (no behavior change)
- `components/ui/{button,badge,input,label,select,step-indicator}.tsx`
- `components/help/HelpButton.tsx`
- `routes/app/knowledge/$kbSlug/{overview,items,connectors,members,taxonomy,settings,advanced,route,-kb-helpers}.tsx`
- `routes/app/knowledge/$kbSlug_.add-source.tsx` — restyle (verified restyle-only, not new feature)
- `routes/app/knowledge/$kbSlug_.add-connector.tsx`, `$kbSlug_.edit-connector.$connectorId.tsx` — diff must be visual-only; if new connector logic sneaks in, extract to SPEC-CONNECTOR-*
- `routes/app/knowledge/new.tsx`, `new._components/MemberPicker.tsx`
- `routes/app/account.tsx`, `meetings/*`, `transcribe/*`, `scribe.tsx`, `gaps/index.tsx`
- `routes/admin/{api-keys,billing,domains,groups,index,join-requests,mcps,route,settings,users,widgets}/*`
- `routes/{index,logged-out,login,verify}.tsx`, `routes/$locale/password/forgot.tsx`, `routes/password/set.tsx`, `routes/setup/mfa.lazy.tsx`
- `index.css` (token updates per spine above)

### i18n

Per-key merge into `messages/{en,nl}.json`:
- `sidebar_*`, `knowledge_*`, `rules_*`, `templates_*` and any identified new keys

Mechanism: extract new keys from branch, merge into main's JSON by key (jq or similar), diff-review before commit. Existing keys that were textually rewritten on the branch are NOT replaced.

### Explicitly excluded
- `klai-portal/frontend/.tanstack/tmp/*` — add `.tanstack/` to `.gitignore`
- `klai-portal/frontend/bun.lock` — rejected
- `routeTree.gen.ts` — regenerated locally, not cherry-picked
- `klai-portal/backend/app/tmp/` (if present in working trees) — ensure gitignored

---

## Implementation Plan

### Phase 0 — Hygiene + foundations
- Run `codeindex update` for fresh graph
- Add `.tanstack/` and `klai-portal/backend/app/tmp/` to `.gitignore`
- Remove stray `.tmp-klai-home-en.json` from working tree if present
- Write `.claude/rules/klai/design/portal-design-system.md` with the 4 contradiction fixes (border rule, font, primary-text, no-uppercase)

### Phase 1 — Design tokens
- `klai-portal/frontend/src/index.css` token updates per spine
- Verify sidebar cream + content white via `bun run dev` (or current manager)

### Phase 2 — UI components (global visual refresh)
- `button.tsx` → `rounded-full`, sentence-case, no uppercase, no amber
- `badge.tsx`, `input.tsx`, `label.tsx`, `select.tsx`, `step-indicator.tsx`
- Codemod: remove `uppercase` class + `tracking-[0.04em]` / `tracking-wider` where applied to visible prose
- Smoke-test via Playwright over all admin + auth routes; verify no visual regression beyond intended refresh

### Phase 3 — Sidebar + chat-home + redirects
- `routes/app/route.tsx` (4 end-user items + admin append)
- `routes/app/index.tsx` (chat iframe + KBScopeBar + health-check logic)
- `routes/app/chat.tsx` (redirect)
- `routes/app/_components/KBScopeBar.tsx` visual refresh
- `routes/app/focus/index.tsx`, `docs/index.tsx` redirects
- Per-key i18n merge: sidebar + chat keys

### Phase 4 — Rules backend + page
- Models, migration, API (`app_rules`, `default_rules`)
- Portal user model: `active_template_ids` field + migration
- Pages: `rules/index.tsx`, `rules/new.tsx`, `rules/$slug.edit.tsx`, `rules/-rule-form.tsx`
- Per-key i18n merge: rules keys
- Integration test: create / list / edit / delete rule via API

### Phase 5 — Templates backend + pages
- Models, migration, API (`app_templates`, `default_templates`)
- Pages: `templates/index.tsx`, `templates/new.tsx`, `templates/$slug.edit.tsx`, `templates/-template-form.tsx`
- Per-key i18n merge: templates keys
- Integration test: create / list / edit / delete template; set/unset active

### Phase 6 — Kennis unified view + re-parented pages
- `routes/app/knowledge/index.tsx` — cards + unified notes list
- Re-parent focus/docs under `/knowledge/*` (file moves + route regen)
- Per-key i18n merge: knowledge keys

### Phase 7 — Visual polish per page-group
- KB detail tabs (label changes per SPEC-001 table + visual refresh)
- `knowledge/$kbSlug_.add-source.tsx` restyle verification
- `add-connector`/`edit-connector` diff verification (visual-only gate)
- Knowledge forms, account, meetings, transcribe, gaps, scribe, admin group, auth group — commit per group
- Apply `bg-black/[0.06]` + `bg-black/5` layering pattern consistently

### Phase 8 — QA
- Playwright E2E: chat-home, kennis, rules, templates, redirects, a re-parented focus page
- `tsc --noEmit` (portal-frontend) — zero errors
- `bun run build` (or npm/pnpm — whichever is canonical) — green
- `alembic upgrade head` → `downgrade base` → `upgrade head` on clean DB
- Manual: fresh signup → verify empty-state on rules + templates + kennis
- `codeindex update` + review impact graph for migrations

---

## Risks and Mitigations

1. **Global `rounded-full` button swap** — affects every form on every page. *Mitigation*: Phase 2 smoke-test covers all routes; commit-per-component isolates regression.
2. **Alembic ordering** — four new migrations must apply linearly. *Mitigation*: inspect `alembic history` on the branch, adopt same dependency chain in portal-api; integration test in Phase 8.
3. **`messages/*.json` per-key merge tool-error risk** — bulk rewrite on branch makes diff noisy. *Mitigation*: scripted jq-merge; diff-review before commit; acceptance test = existing strings unchanged.
4. **`add-source.tsx` 1416 lines** — visual diff only per user instruction. *Mitigation*: Phase 7 gated review before adopt; if new feature logic found, extract to separate SPEC.
5. **Scope slip via "visual-only" refresh** — admin/auth pages may get unintended behavior changes. *Mitigation*: commit-per-page-group + spot-check.
6. **Submodule drift** — branch bumps `klai-infra` and `klai-website`; this SPEC leaves them untouched. *Mitigation*: verify no portal-frontend file depends on the submodule bumps (likely zero; submodules are deploy/content).
7. **Rules/Templates UI works but does nothing** — users see pages that don't affect chats in v1. *Mitigation*: UI copy explicitly states "Rules apply to future conversations" style messaging; polish SPEC or enforcement SPEC closes the loop.

---

## Polish-1 seams (explicit list for SPEC-PORTAL-POLISH-001)

Items Jantine's polish SPEC should address, in isolation from v1:

1. Amber pill buttons (fill + text + hover)
2. Button text style: medium/bold weight, capitalized
3. Empty-state pattern normalization (one canonical or two documented variants)
4. Section rhythm: `space-y-6` vs `space-y-8` rule
5. `ghost` vs `outline` button — semantic difference or merge
6. Back/cancel link — one pattern
7. `text-gray-900` vs `--color-foreground` (#191918) alignment
8. Parabole weight scale beyond 500/700
9. Brand-moment audit: where Parabole appears beyond headings + collection names

---

## Separate SPECs spawned by this SPEC

- `SPEC-PORTAL-POLISH-001` — Jantine's design polish (amber, typography, rhythm)
- `SPEC-RULES-ENFORCEMENT-001` — LiteLLM guardrails hook
- `SPEC-TEMPLATES-INJECTION-001` — active-template injection at chat-call time
- `SPEC-CONNECTOR-AIRTABLE-001`
- `SPEC-CONNECTOR-CONFLUENCE-001`
- `SPEC-CONNECTOR-GMAIL-001`
- `SPEC-CONNECTOR-SLACK-001`
- `SPEC-CONNECTOR-GSHEETS-001`
- `SPEC-DEV-INFRA-001` — dev CI, `docker-compose.dev.yml`, Caddy dev-routes, `dev.md`

---

## What does NOT change

- Admin area IA — only visual refresh cascades through shared components
- Backend auth flow
- LibreChat iframe contents or integration protocol
- Design tokens beyond what's listed in the spine above
- `klai-infra` and `klai-website` submodules
- Package manager (no bun switch)
- Meetings/transcribe/scribe/gaps functionality — only visual refresh

---

## Verification checklist (copy into PR description)

- [ ] `codeindex update` run before merge
- [ ] `.gitignore` includes `.tanstack/` and `klai-portal/backend/app/tmp/`
- [ ] `portal-design-system.md` has no internal contradictions (border rule, font, primary-text, uppercase)
- [ ] No `uppercase` class remains in `klai-portal/frontend/src/`
- [ ] All buttons use `rounded-full` (or explicitly justified `rounded-lg` for non-button elements)
- [ ] Content bg white, sidebar bg cream, amber only as `--color-ring`
- [ ] Sidebar: 4 end-user items (Chat/Kennis/Templates/Regels) + admin append
- [ ] Redirects: `/app/chat`, `/app/focus`, `/app/docs` → correct targets
- [ ] i18n per-key merge (diff review confirms no unintended copy changes to existing keys)
- [ ] Alembic: `upgrade head` → `downgrade base` → `upgrade head` idempotent
- [ ] Playwright: chat-home, kennis, rules, templates, one redirect, one re-parented route
- [ ] `tsc --noEmit` green
- [ ] Portal-frontend build green
- [ ] No `.tanstack/tmp/`, `bun.lock`, or stray artefacts committed
- [ ] `klai-infra` and `klai-website` submodule refs unchanged versus `main`
