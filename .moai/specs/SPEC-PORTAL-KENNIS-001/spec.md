---
id: SPEC-PORTAL-KENNIS-001
version: 0.1.0
status: draft
created: 2026-05-08
author: Jantine Doornbos
priority: high
parent: SPEC-PORTAL-REDESIGN-002 (carved out — kennis-only)
source_branch: feat/chat-first-redesign
---

# SPEC-PORTAL-KENNIS-001: Kennis pages — chat-first redesign cherry-pick

## Goal

Bring the **kennis (Knowledge Base)** UI from `feat/chat-first-redesign` to `main` as an isolated change. Sidebar, chat-home, rules, templates and all non-knowledge pages are explicitly out of scope (covered by SPEC-PORTAL-REDESIGN-002 if/when adopted later).

## Core principle: super simpel voor een normaal mens

The kennis UI must work for non-technical users. Two screens, no jargon, no modes, no toggles.

### Screen 1 — KB list (`/app/knowledge`)

A flat list of KBs the user has access to. Per KB row:

- Icon (folder / user / building based on owner_type)
- KB name
- Subtitle: **"N bronnen · M chunks"**
- Status badge: **Klaar** (green) / **Bezig** (spinner) / **Probleem** (red — clickable)
- Click anywhere on row → KB detail screen

No expand chevron, no inline source list, no per-row sync/delete actions. Just: list, click, done.

Top of page: search bar (filters by KB name) + "+ Nieuwe collectie" button.

### Screen 2 — KB detail (`/app/knowledge/$kbSlug`)

Three tabs. **Bronnen** is default.

| Tab | Inhoud |
|---|---|
| **Bronnen** | Unified list of bronnen in this KB (connectors + direct uploads). Click on a bron expands it inline to show its content (chunks/items). Top: "+ Bron toevoegen" button. |
| **Instellingen** | KB name, description, access/members management, delete. Owner-only delete; member-management visible to owners. |
| **Geavanceerd** | Power-user view: taxonomie, connector-management detail (re-auth, edit credentials), platte items-view across all bronnen. |

No "Geavanceerd toggle" — it's a full tab. No separate Members / Settings / Items / Connectors / Taxonomy / Advanced tabs.

### Concept "alles is een bron"

A bron is anything that brings content into a KB:
- A connector (Notion DB, GitHub repo, Drive folder, MCP server) — managed bron, contains many items
- A direct file/url/text upload — atomic bron, contains itself

Both render with the same row shape on the Bronnen tab. Click a bron → see its chunks/items underneath. The user never has to distinguish "is this a connector or a file" in the default flow.

### What disappears versus today's main

- 7-tab KB shell (overview / connectors / members / items / taxonomy / settings / advanced) → 3 tabs
- "Connectors" as a first-class concept in the UI → folded into "Bronnen"
- Sync-status jargon ("indexed" / "syncing" / "auth_error") → translated to **Klaar / Bezig / Probleem** in user-visible status
- Per-KB inline expansion in the list → moved to KB detail page where it has room to breathe

## Scope

### In

Frontend files (port from `feat/chat-first-redesign`, adapted for main):

- `routes/app/knowledge/index.tsx` — flat "Bronnen"-style list with expandable KB rows showing connectors inline, search, no personal/team/org tabs.
- `routes/app/knowledge/$kbSlug/route.tsx` — KB shell: default tab-bar shows **Overzicht / Toegang / Instellingen** (3 tabs). A **"Geavanceerd"** toggle (button or expandable section in the tab-bar area) reveals the power-user tabs: **Items / Connectoren / Taxonomie / Advanced**. Toggle state is local to the KB-detail view (per-session, not persisted in v1). All routes remain addressable via direct URL — landing on a hidden tab via URL auto-expands the Geavanceerd section so the active tab is visible.
- `routes/app/knowledge/$kbSlug/overview.tsx` — unified view: connectors + items + sync controls on one page.
- `routes/app/knowledge/$kbSlug/members.tsx` — visual refresh ("Toegang" tab).
- `routes/app/knowledge/$kbSlug/settings.tsx` — visual refresh ("Instellingen" tab, owner-only).
- `routes/app/knowledge/$kbSlug/-kb-helpers.tsx` and `-kb-types.ts` — supporting types/helpers.
- `routes/app/knowledge/new.tsx` and `new._components/MemberPicker.tsx` — KB creation flow restyle.
- `routes/app/knowledge/$kbSlug_.add-source.tsx` + `$kbSlug_.add-source._components/*` — unified "Add source" wizard restricted to source types **already supported on main**.
- `routes/app/knowledge/$kbSlug_.add-connector.tsx` and `$kbSlug_.edit-connector.$connectorId.tsx` — adopt visual-only changes; reject any new-connector adapter logic.

i18n: per-key merge of `knowledge_*`, `add_source_*` keys from feat branch into main `messages/{nl,en}.json`. Existing keys are NOT overwritten.

### Out (explicit)

- Sidebar restructure (`routes/app/route.tsx`) — keep main's nav.
- Chat-as-homepage (`routes/app/index.tsx`) — keep main's dashboard.
- Rules pages, Templates pages — separate SPEC.
- LiteLLM guardrails hook (`deploy/litellm/klai_knowledge.py`) — separate SPEC.
- New connector adapters not yet on main: **gmail, slack, google_sheets, youtube, rss**. The unified "Add source" wizard in v1 only exposes source types whose backend exists on main today: file, url, text, image (direct upload), web_crawler, github, notion, google_drive, ms_docs, airtable, confluence.
- Sub-modules (`klai-infra`, `klai-website`) — untouched.
- `routes/app/_components/ChatConfigBar.tsx` and `KBScopeBar.tsx` — main deleted these in commit `0896d46b`. Do NOT re-introduce.
- Re-parenting of `/app/focus` and `/app/docs` under `/app/knowledge/*` — keep main's IA.
- Database migrations and model changes — strictly out. If a backend change requires alembic, the SPEC stops and is renegotiated.
- New connector adapters — out (in-scope source types only).
- Any test file changes from feat branch unless adapted: porting tests is allowed but must pass against main's behavior.

### Allowed backend additions (limited)

Only the small additions enumerated in Phase B-bis: a chunks-count field on stats-summary, a unified `/sources` endpoint, and optionally a per-bron content endpoint. No models, no migrations, no new auth flow.

## Source-type catalog (in scope)

The unified `add-source.tsx` wizard exposes these only:

| Group | Source types |
|---|---|
| Direct upload | file, url, text, image |
| Website & media | web_crawler |
| Productivity | google_drive, ms_docs, notion |
| Development | github |
| Integrations | airtable, confluence |

Out (will appear with `comingSoon: true` badge OR be omitted entirely — implementation decides per type whether stub-with-disabled-state is cleaner than removal): gmail, slack, google_sheets, youtube, rss.

## Success Criteria

1. `/app/knowledge` shows a flat list of KBs. Each row: name, "N bronnen · M chunks", status (Klaar/Bezig/Probleem). Click navigates to the KB detail. Top bar: search + "Nieuwe collectie".
2. `/app/knowledge/$kbSlug` shows exactly three tabs: **Bronnen** (default), **Instellingen**, **Geavanceerd**. No fourth tab, no toggle, no hidden tab.
3. **Bronnen tab** lists connectors + direct uploads in one list, consistent row shape. Clicking a bron expands it inline to show its chunks/items. "+ Bron toevoegen" button at top. Empty state has one CTA: "Eerste bron toevoegen".
4. **Instellingen tab** consolidates KB name/description editing, access (members) management, and delete. Owner gating preserved.
5. **Geavanceerd tab** consolidates taxonomy, connector-detail management (re-auth, edit credentials), and a flat per-item view across all bronnen.
6. "Bron toevoegen" wizard exposes only the in-scope source types (see catalog below) and adapts to the KB type (Persoonlijk shows file/url/text/image + connectors; team/org shows connectors only).
7. User-visible status uses **Klaar / Bezig / Probleem**. Internal terms (`indexed`, `syncing`, `auth_error`) only appear in tooltips or on the Geavanceerd tab.
8. Direct URLs to legacy sub-routes (e.g. `/app/knowledge/foo/items`) redirect to the appropriate new tab (items → Geavanceerd, connectors → Geavanceerd, taxonomy → Geavanceerd, members → Instellingen, advanced → Geavanceerd).
9. `tsc --noEmit` on portal-frontend → zero errors. `bun run build` green.
10. Playwright smoke: list page renders, KB detail renders 3 tabs, clicking a bron expands content, add-source page works for at least one source type per category.
11. No broken links to removed `KBScopeBar` / `ChatConfigBar`.
12. Visual changes match `.claude/rules/klai/design/portal-patterns.md`: `rounded-full` buttons, sentence-case, no `uppercase` class, white content bg.

## Implementation Plan

### Phase A — Branch + scaffolding
- New branch `feat/SPEC-PORTAL-KENNIS-001` from `main`.
- Commit per phase below.

### Phase B — Supporting files
- Port `-kb-types.ts`, `-kb-helpers.tsx` from feat branch (no UI change yet).
- Verify imports resolve against main.

### Phase B-bis — Minimal backend additions (verified design)

The data layer already supports everything. Three small endpoints are added in portal-api (proxying to knowledge-ingest where appropriate). No migration. No model changes.

1. **Stats-summary chunks count.** Extend `/api/app/knowledge-bases/stats-summary` response: per-KB `chunks` field. Implementation: a single `COUNT(*)` join on `knowledge.parent_chunks` per KB, or a sum of `parent_chunks` rows joined via `artifacts`. ~10 lines.

2. **Unified bronnen list — `GET /api/app/knowledge-bases/{slug}/sources`.** Returns:
   ```
   [
     # one row per connector that has items in this KB
     {kind: "connector", id, connector_type, name, items_count, chunks_count, last_sync_at, last_sync_status},
     # one row per direct-upload artifact (artifacts where extra->>'source_connector_id' IS NULL)
     {kind: "upload", id, name, content_type, chunks_count, created_at}
   ]
   ```
   Aggregate query joins `artifacts` (filtered by `org_id`, `kb_slug`) with `parent_chunks` for counts. Group by connector_id (from `extra` jsonb) where present; ungrouped rows = direct uploads. ~80 lines + 1-2 tests.

3. **Drill-down — `GET /api/app/knowledge-bases/{slug}/sources/{source_id}/content`.** For connector-source: list artifacts (path + chunks_count + status) under that connector. For upload-source: list parent_chunks (text preview + position). Pagination via `limit`/`offset`. ~60 lines + 1-2 tests.

If any of these proves to require alembic or model changes, STOP and surface to user.

### Phase C — KB list page (`knowledge/index.tsx`) — flat list, no expand
- New simple page. Do NOT port feat's expandable-row pattern.
- Per KB row:
  - Icon (per `owner_type`: User / FolderOpen / Building2)
  - Name (Parabole `font-display text-[15px]`)
  - Subtitle: `{N} bronnen · {M} chunks`
  - Status badge: **Klaar** / **Bezig** / **Probleem** (mapped from underlying sync states; if any connector has error → Probleem; if any syncing → Bezig; else Klaar)
  - Whole row is a `<Link>` to `/app/knowledge/$kbSlug` (default tab = Bronnen)
- No per-row actions on the list. Re-index, delete, add-source live on the detail page.
- Top bar: search input (filters by KB name only) + "Nieuwe collectie" button.
- Required data:
  - KB list from `/api/app/knowledge-bases`
  - Counts: bronnen-count + chunks-count per KB. **Verification needed:** does `/api/app/knowledge-bases/stats-summary` already return chunks per KB? If not, see Phase B-bis.
- Strip any references to deleted main components (`KBScopeBar`, `ChatConfigBar`).

### Phase D — KB detail shell (`$kbSlug/route.tsx`) — 3 tabs
- Tab bar: **Bronnen** (default) / **Instellingen** / **Geavanceerd**.
- New child routes:
  - `$kbSlug/bronnen.tsx` — default landing
  - `$kbSlug/instellingen.tsx` — merges members + settings + delete
  - `$kbSlug/geavanceerd.tsx` — merges items + connectors + taxonomy + advanced into one tab with internal sections
- Legacy route mapping (`beforeLoad` redirects):
  - `/overview` → `/bronnen`
  - `/items` → `/bronnen` (items are visible via per-bron drill-down)
  - `/connectors` → `/bronnen` (connectors are bronnen now)
  - `/taxonomy` → `/geavanceerd#taxonomie`
  - `/advanced` → `/geavanceerd#advanced`
  - `/members` → `/instellingen#toegang`
  - `/settings` → `/instellingen`
- Owner gating: Instellingen edit + delete owner-only; Geavanceerd visible to all members but destructive actions owner-only.
- Verify `ProductGuard`, `useCurrentUser` hooks already exist on main.
- Header above tab bar: KB name (Parabole `font-display-bold text-[26px]`), "Terug" link → `/app/knowledge`.

### Phase E — Bronnen tab (`$kbSlug/bronnen.tsx`) — "alles is een bron"
- Single list of bronnen for this KB (one row per connector + one row per direct upload).
- Data source: `GET /api/app/knowledge-bases/${slug}/sources` (Phase B-bis).
- Per bron row:
  - Type icon (per `connector_type` for connectors, per `content_type` for uploads)
  - Name (`connector.name` or `artifact.path`)
  - Meta line: `{type-label} · {chunks_count} chunks · {items_count} items` (items_count only for connectors). Type-label is human: "Notion", "GitHub-repo", "PDF", "URL", "Tekst", "Afbeelding".
  - Status: Klaar / Bezig / Probleem (mapped from `last_sync_status` for connectors; uploads default Klaar unless `extra` jsonb says otherwise)
  - Chevron right — clicking expands the row inline.
- **Expanded bron content** (data from `GET /sources/${id}/content`):
  - **For connector:** list of items (artifact path + chunk count). Each item clickable to expand further → chunk preview. Top action bar: **Synchroniseren** (sync connector), **Beheer koppeling** (re-auth / edit credentials — opens existing `edit-connector` page or modal), **Verwijderen** (delete connector + all its artifacts).
  - **For upload:** list of chunks with text preview (truncated, ~120 chars) + position. Top action bar: **Origineel openen** (download/view), **Verwijderen**.
- Connector-management actions live HERE, not in a separate Geavanceerd section. One bron, one place.
- Top of tab: search input (filters bronnen by name) + "+ Bron toevoegen" button → add-source wizard.
- Empty state: dashed-border card, single CTA "Eerste bron toevoegen".
- Documenten link (block editor) stays separate as a visually distinct section below bronnen, only if `kb.docs_enabled && kb.gitea_repo_slug`.

### Phase F — Instellingen tab (`$kbSlug/instellingen.tsx`)
- Sections (one page, scrollable, no sub-tabs):
  - **Algemeen:** name, description (editable; owner-only)
  - **Toegang:** member list with role per member, invite, remove (owner-only edit; visible to all members read-only)
  - **Verwijderen:** danger-zone delete button at bottom (owner-only, with confirm)
- Port from feat's `members.tsx` + `settings.tsx` + delete logic from `overview.tsx`.

### Phase F-bis — Geavanceerd tab (`$kbSlug/geavanceerd.tsx`)
- Sections (one page, scrollable, no sub-tabs):
  - **Taxonomie:** taxonomy view + suggest (port from feat's `taxonomy.tsx`)
  - **Geavanceerde instellingen:** port from feat's `advanced.tsx` (rebuild controls, debug info, etc.)
- **Removed from earlier draft:**
  - **Items section is dropped.** The Bronnen tab already shows items via per-bron drill-down. A separate flat items list duplicates that without adding value.
  - **Connectors section is dropped.** Connector-management (sync, re-auth, edit credentials, delete) lives in the Bronnen tab where each connector appears as a bron. One bron, one place.
- Each section has an `id` so direct URLs (e.g. `/geavanceerd#taxonomie`) can scroll to it.
- Legacy URL redirects updated:
  - `/items` → `/bronnen` (items are visible there now via drill-down)
  - `/connectors` → `/bronnen` (connectors are bronnen now)
  - `/taxonomy` → `/geavanceerd#taxonomie`
  - `/advanced` → `/geavanceerd#advanced`

### Phase F-ter — Nieuwe collectie (`knowledge/new.tsx`)
- Port: visual refresh only — keep behavior identical.
- Verify `MemberPicker` component from feat works with main's user-search endpoint.

### Phase G — Add source wizard
- Port `$kbSlug_.add-source.tsx` + `_components/*`.
- **Filter source-type catalog to in-scope list above.** Out-of-scope types either omitted or rendered with `comingSoon: true` and disabled handler.
- Verify each in-scope source type submits successfully against main's API (manual or test).

### Phase H — Add/edit connector visual refresh
- Diff-review feat versions of `add-connector.tsx` and `edit-connector.tsx`. Adopt visual changes only. Reject any new connector logic.

### Phase I — i18n merge
- Extract `knowledge_*`, `add_source_*`, `kb_*`, `sources_*` keys from feat `messages/{nl,en}.json`.
- Merge per-key into main. Diff review: existing keys not changed.

### Phase J — QA
- `routeTree.gen.ts` regenerate locally — do not cherry-pick from branch.
- `tsc --noEmit`.
- `bun run build`.
- Playwright smoke (or manual click-through if Playwright not set up locally): list page, KB detail with each tab, add-source with each in-scope source type initiating.
- No console errors on these pages.
- Push branch, open PR.

## Risks

| Risk | Mitigation |
|---|---|
| `add-source.tsx` references components/types that don't exist on main | Phase B ports types/helpers first; Phase G fails fast on missing imports → list and decide to stub or skip |
| Hidden tabs (items/connectors/taxonomy/advanced) link from somewhere unexpected | All routes stay addressable; auto-expand on direct URL ensures user sees the active tab |
| KB list expects fields on `/api/app/knowledge-bases` response that main doesn't return | Phase C verifies field-by-field; if main lacks a field, add `?? null` or omit UI element rather than change backend |
| Per-source `chunks` count not returned by main's connectors API | If absent, omit the "· N chunks" suffix in the meta line; show only the type label |
| Files in expanded view only visible for own Persoonlijk-KB | Documented limitation. For other KBs the expanded view shows connectors only. A separate SPEC adds a per-KB items endpoint to lift this — out of scope here |
| File download action requires endpoint that may not exist on main | Phase C: grep main for a file-download route. If absent, omit the download icon — show name + delete only |
| feat's `add-connector.tsx` includes new-connector adapter coupling | Phase H gate: visual diff only, abort port if logic-coupling found |
| Out-of-scope `gmail/slack/sheets/youtube/rss` tiles ship as live but fail | Phase G filter must be enforced; tests must verify only in-scope types are submittable |
| Deleted `KBScopeBar` / `ChatConfigBar` re-imported transitively | Grep `import.*KBScopeBar\|ChatConfigBar` after each phase |

## Verification checklist (PR description)

- [ ] No alembic migration. No model change. No new connector adapter.
- [ ] Backend additions limited to Phase B-bis (stats-summary chunks, `/sources` endpoint, optional per-bron content)
- [ ] No reference to `KBScopeBar` or `ChatConfigBar` re-introduced
- [ ] Sidebar (`routes/app/route.tsx`) untouched — same as main
- [ ] `routes/app/index.tsx` untouched — same as main
- [ ] No `routes/app/rules/*` or `routes/app/templates/*` added
- [ ] KB list shows: name, "N bronnen · M chunks", status (Klaar/Bezig/Probleem). No expand chevron, no per-row sync/delete.
- [ ] KB detail has exactly 3 tabs: Bronnen / Instellingen / Geavanceerd
- [ ] All legacy KB sub-routes (overview, items, connectors, taxonomy, advanced, members, settings) redirect to one of the 3 new tabs
- [ ] Bronnen tab: uniform list, click expands inline showing chunks/items
- [ ] `add-source.tsx` only exposes in-scope source types and adapts to KB type (Persoonlijk = all; team/org = connectors only)
- [ ] User-visible status uses Klaar / Bezig / Probleem (jargon only on Geavanceerd tab or in tooltips)
- [ ] `tsc --noEmit` green
- [ ] portal-frontend build green
- [ ] Manual smoke: list page → click KB → 3 tabs → click bron → see chunks → "Bron toevoegen" works for at least one type per category
- [ ] i18n: existing keys unchanged, only knowledge_* / add_source_* / kb_* / sources_* / bronnen_* added
