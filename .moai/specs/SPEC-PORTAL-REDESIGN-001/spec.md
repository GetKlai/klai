---
id: SPEC-PORTAL-REDESIGN-001
version: 1.0.0
status: approved
created: 2026-04-13
author: Jantine Doornbos
priority: high
---

# SPEC-PORTAL-REDESIGN-001: Chat-first Portal Redesign

## HISTORY

### v1.0.0 (2026-04-13)
- Final architecture: 3 sidebar items (Chat, Kennis, Regels)
- Homepage = LibreChat iframe + Klai wrapper
- "Mijn kennis" = unified view (collecties + notebooks + documenten)
- "Regels" = new page for prompt rules/guardrails (empty state v1)
- Inspired by Superdock (sources, rules) + Obsidian (knowledge-centric)

---

## Goal

Redesign the Klai portal end-user experience around one core interaction: **"Stel een vraag, krijg een antwoord uit je eigen kennis."**

The current portal presents 5 separate tools (Chat, Transcribe, Focus, Docs, Knowledge) that feel disconnected and technical. The redesign unifies them into three clear concepts:

1. **Chat** — talk to your knowledge
2. **Kennis** — manage your knowledge
3. **Regels** — control how AI behaves

Compliance/privacy is the enabler (why the CIO buys it), not the headline (why the user opens it).

## Success Criteria

- End user lands directly in the chat after login — no tool launcher, no speed bump
- Sidebar has exactly 3 items: Chat, Kennis, Regels
- Knowledge sources are visible in the chat via KBScopeBar pills
- "Mijn kennis" shows collecties, notebooks, and documenten in one unified view
- "Regels" page exists with empty state and clear value proposition
- All existing functionality remains accessible (no features removed, only reorganized)
- Old routes redirect to new locations (no broken bookmarks)
- NL and EN translations for all new/changed strings
- Passes TypeScript strict mode, builds without errors

## Non-Goals

- Replacing LibreChat with native chat (separate future SPEC)
- Building rules backend (this SPEC creates the frontend page only)
- Redesigning the admin area (/admin)
- Changing backend APIs
- Changing design system tokens (colors, fonts, radius)

---

## Information Architecture

### Current (5 tools, tool-first)

```
/app/              → Tool grid (5 cards)
/app/chat          → LibreChat iframe + KBScopeBar
/app/transcribe    → Transcription list + upload
/app/focus         → Notebook list + detail + editor
/app/docs          → Doc KB list + editor
/app/knowledge     → KB table + 7-tab detail pages
/app/account       → User settings
```

Sidebar: Chat | Scribe | Focus | Knowledge | Docs

### New (3 concepts, chat-first)

```
/app/                    → Chat (LibreChat iframe + Klai wrapper + KBScopeBar)
/app/knowledge           → "Mijn kennis" (collecties + notebooks + documenten)
/app/knowledge/$kbSlug/* → Collectie detail (existing KB tabs, simplified)
/app/knowledge/focus/*   → Notebooks (existing Focus pages, re-parented)
/app/knowledge/docs/*    → Documenten (existing Docs pages, re-parented)
/app/rules               → "Regels" (guardrails for AI — empty state v1)
/app/transcribe/*        → KEPT but hidden (accessible via URL only)
/app/account             → Unchanged
```

Sidebar: **Chat** | **Kennis** | **Regels**

Three items. Three concepts. That's the whole app.

- **Chat** = what you DO (talk to your knowledge)
- **Kennis** = what you KNOW (sources, notebooks, documents)
- **Regels** = how it BEHAVES (guardrails, instructions, tone)

### Route Mapping (old → new)

| Old route | New route | Change |
|---|---|---|
| `/app/` | `/app/` | Rewritten: tool grid → LibreChat iframe + wrapper |
| `/app/chat` | `/app/` (redirect) | Merged into homepage — chat IS the app |
| `/app/transcribe` | `/app/transcribe` | Hidden from sidebar, accessible via URL |
| `/app/focus` | `/app/knowledge` (redirect) | Merged into "Mijn kennis" |
| `/app/focus/$id` | `/app/knowledge/focus/$id` | Re-parented under /knowledge |
| `/app/docs` | `/app/knowledge` (redirect) | Merged into "Mijn kennis" |
| `/app/docs/$kbSlug` | `/app/knowledge/docs/$kbSlug` | Re-parented under /knowledge |
| `/app/knowledge` | `/app/knowledge` | Redesigned: table → unified knowledge view |
| `/app/knowledge/$kbSlug/*` | `/app/knowledge/$kbSlug/*` | Stays (simplified tabs) |
| `/app/knowledge/new` | `/app/knowledge/new` | Stays |
| `/app/gaps` | `/app/knowledge/gaps` | Nested under knowledge (admin only) |
| `/app/rules` | `/app/rules` | NEW: rules/guardrails page |
| `/app/account` | `/app/account` | Unchanged |

**Backward compatibility:** `/app/chat`, `/app/focus`, `/app/docs` redirect to new locations.

---

## Page-by-Page Specification

### Page 1: App Layout + Sidebar (`routes/app/route.tsx`)

**Current:** Sidebar with 5 nav items (Chat, Scribe, Focus, Knowledge, Docs). Product-gated filtering.

**New sidebar items:**

| # | Label (NL) | Label (EN) | Icon | Route | Product gate |
|---|---|---|---|---|---|
| 1 | Chat | Chat | `MessageSquare` | `/app` (end: true) | chat |
| 2 | Kennis | Knowledge | `BookOpen` | `/app/knowledge` | knowledge |
| 3 | Regels | Rules | `Shield` | `/app/rules` | chat |

**3 items. That's the whole app for the end user.**

- Chat = the homepage, where you land, the core product
- Kennis = everything you know: collecties, notebooks, documenten
- Regels = how AI behaves: guardrails, instructions, templates

**The rest of the sidebar remains unchanged:** admin/app switcher, locale switcher, user info, logout.

---

### Page 2: Homepage = Chat (`routes/app/index.tsx`)

**Current:** Greeting + "Tools" heading + 3-column grid of 5 tool cards.

**New:** The homepage IS the chat. LibreChat iframe with Klai wrapper. No greeting page, no speed bump. You open Klai, you talk to your knowledge.

**Layout:**

```
┌──────────────────────────────────────────────────────┐
│ [KBScopeBar]                                         │
│ ● Kennis aan  [Persoonlijk] [Organisatie] [API] [+]  │
│──────────────────────────────────────────────────────│
│                                                      │
│                 LibreChat iframe                      │
│                 (full height, full width)             │
│                                                      │
│                 LibreChat handles:                    │
│                 - Welcome/empty state                 │
│                 - Conversation sidebar                │
│                 - Input field                         │
│                 - AI responses with citations         │
│                 - Conversation history                │
│                                                      │
└──────────────────────────────────────────────────────┘
```

**The Klai wrapper adds only what LibreChat cannot provide:**

1. **KBScopeBar** — top bar showing active knowledge sources as pills. Toggle retrieval on/off, select personal/org KBs, filter specific KBs. Same functionality as current KBScopeBar but visually refined to match new design.

2. **Health check + loading state** — existing logic: health check → loading_iframe → ready/stuck/error. Same SSO expiry detection via postMessage. Same retry flow.

3. **That's it.** LibreChat handles everything else. We don't duplicate.

**Implementation:** Move all iframe logic from current `chat.tsx` into `index.tsx`. The `chat.tsx` file becomes a redirect to `/app/`.

**Data requirements:**
- `useAuth()` for token + SSO
- `useCurrentUser()` for product access check
- `useQuery(['kb-preference'])` for KB scope state
- `useQuery(['org-kbs-for-bar'])` for org KB list

**KBScopeBar visual refinement:**
- Same functionality, same API (PATCH `/api/app/account/kb-preference`)
- Softer border below: `border-[var(--color-border)]`
- Pills: `rounded-full`, amber tint when active, green dot for retrieval status
- Matches design system

---

### Page 3: "Mijn kennis" (`routes/app/knowledge/index.tsx`) — REDESIGNED

**Current:** Admin-style table with KB rows, visibility icons, search bar, stats per row.

**New:** Unified knowledge view. Everything the user knows, in one place. Two sections on one page.

**Layout:**

```
Mijn kennis                                      [+ Nieuw ▾]

Collecties
┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐
│ ● Persoonlijk   │  │ ● Organisatie   │  │  API Docs    │
│   12 items      │  │   48 items      │  │  6 items     │
│   3 connectors  │  │   5 connectors  │  │  1 connector │
│        Beheer → │  │        Beheer → │  │     Beheer → │
└─────────────────┘  └─────────────────┘  └──────────────┘

Notities & documenten
─────────────────────────────────────────────────────────
📓 Q1 Planning Notebook                      gisteren
📄 HR Beleid                                  3 apr
📓 Concurrentieanalyse                       1 apr
📄 API Documentatie                           28 mrt
```

**Section 1: Collecties (Knowledge Sources)**
- Cards in responsive grid (2-3 columns)
- Default KBs (Personal + Org) at top with green dot
- Other KBs follow
- Each card shows: name, item count, connector count, "Beheer →" link
- Card click or "Beheer →" → `/app/knowledge/$kbSlug/overview`

**Section 2: Notities & documenten**
- Unified list combining notebooks (Focus API) and doc KBs (Docs API)
- Sorted by last updated, mixed together
- Each row: icon (📓 notebook / 📄 document), title, relative date
- Notebook click → `/app/knowledge/focus/$id`
- Document click → `/app/knowledge/docs/$kbSlug`

**"+ Nieuw" dropdown:** "Bron toevoegen" | "Notebook" | "Document"
- Bron toevoegen → `/app/knowledge/new` (existing KB create form)
- Notebook → `/app/knowledge/focus/new` (existing Focus new page)
- Document → `/app/knowledge/docs/new` (existing Docs new page)

**Data requirements:**
- `['app-knowledge-bases']` + `['app-knowledge-bases-stats-summary']` for collecties
- Notebooks API (`/research/v1/notebooks`) for notebook list
- Docs-enabled KBs filter (`docs_enabled=true`) for doc list
- No new backend endpoints

---

### Page 4: Collectie Detail (`routes/app/knowledge/$kbSlug/`)

**Current:** 7 tabs: Overview, Items, Connectors, Members, Taxonomy, Settings, Advanced.

**Change:** Simplify visible tabs for non-admin users. Content stays the same, labels change.

| Tab | Visibility | New label (NL) | New label (EN) |
|---|---|---|---|
| Overview | Always | Overzicht | Overview |
| Items | Personal KB only | Items | Items |
| Connectors | Always | Bronnen | Sources |
| Members | Always | Toegang | Access |
| Taxonomy | Admin only | Taxonomie | Taxonomy |
| Settings | Owner only | Instellingen | Settings |
| Advanced | Owner only | Geavanceerd | Advanced |

**No changes to tab page content.** Same components, same data fetching, same functionality. Only navigation labels and conditional visibility change.

---

### Page 5: "Regels" (`routes/app/rules/index.tsx`) — NEW

**Current:** Does not exist.

**New:** Rules/guardrails page. Controls what topics, content, and data are allowed in AI conversations. Inspired by Superdock's Rules page.

**Layout (empty state — v1):**

```
Regels                                          [+ Nieuwe regel]

┌──────────────────────────────────────────────────────┐
│                                                      │
│                    [shield icon]                      │
│                                                      │
│                  Nog geen regels                      │
│                                                      │
│     Voeg regels toe om te bepalen welke              │
│     onderwerpen, content en data zijn                │
│     toegestaan in AI-gesprekken.                     │
│                                                      │
│            [+ Maak je eerste regel]                   │
│                                                      │
└──────────────────────────────────────────────────────┘

Hoe regels worden toegepast

┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  💬 Chat     │  │  📚 Kennis   │  │  🔒 Privacy  │
│              │  │              │  │              │
│  Regels      │  │  Regels      │  │  EU AI Act   │
│  gelden voor │  │  bepalen hoe │  │  compliance  │
│  elk         │  │  bronnen     │  │  wordt auto- │
│  gesprek     │  │  worden      │  │  matisch     │
│              │  │  gebruikt    │  │  toegepast   │
└──────────────┘  └──────────────┘  └──────────────┘
```

**Components:**

1. **Header** — "Regels" + "+ Nieuwe regel" button (disabled in v1 or opens coming-soon toast)
2. **Empty state card** — centered icon, title, description, CTA button
3. **"How rules are enforced" section** — 3 explanation cards showing where rules apply

**This is a frontend-only page.** No backend API needed for v1. The page communicates the concept and establishes the architecture for when the rules backend is built (separate SPEC).

**Future v2 (separate SPEC):**
- List of rules with enable/disable toggles
- Rule editor: name, description, instruction text
- Scope: per-collection or global
- Enforcement: applied as system prompts to LiteLLM calls

---

### Page 6: Transcribe

**Current:** Standalone sidebar item → transcription list + upload page.

**Change:**
- Remove from sidebar
- Keep routes at `/app/transcribe/*` (functional, just not in nav)
- Accessible via direct URL
- No changes to transcribe page components themselves

---

### Page 7: Redirects + Backward Compatibility

```typescript
'/app/chat'               → redirect to '/app/'
'/app/focus'              → redirect to '/app/knowledge'
'/app/focus/$id'          → redirect to '/app/knowledge/focus/$id'
'/app/docs'               → redirect to '/app/knowledge'
'/app/docs/$kbSlug'       → redirect to '/app/knowledge/docs/$kbSlug'
```

Implementation: TanStack Router `redirect` in `beforeLoad` of old route files.
Routes that stay: `/app/knowledge`, `/app/knowledge/$kbSlug/*`, `/app/transcribe/*`, `/app/account`.

---

## i18n Messages (new/changed)

| Key | EN | NL |
|---|---|---|
| `sidebar_chat` | Chat | Chat |
| `sidebar_knowledge` | Knowledge | Kennis |
| `sidebar_rules` | Rules | Regels |
| `knowledge_page_title` | My knowledge | Mijn kennis |
| `knowledge_section_sources` | Collections | Collecties |
| `knowledge_section_notes` | Notes & documents | Notities & documenten |
| `knowledge_new_button` | New | Nieuw |
| `knowledge_new_source` | Add source | Bron toevoegen |
| `knowledge_new_notebook` | Notebook | Notebook |
| `knowledge_new_document` | Document | Document |
| `knowledge_card_items` | {count} items | {count} items |
| `knowledge_card_connectors` | {count} connectors | {count} connectors |
| `knowledge_card_manage` | Manage | Beheer |
| `knowledge_tab_overview` | Overview | Overzicht |
| `knowledge_tab_connectors` | Sources | Bronnen |
| `knowledge_tab_members` | Access | Toegang |
| `rules_page_title` | Rules | Regels |
| `rules_page_subtitle` | Guardrails for AI conversations | Richtlijnen voor AI-gesprekken |
| `rules_empty_title` | No rules yet | Nog geen regels |
| `rules_empty_description` | Add rules to control what topics, content, and data are allowed in AI conversations. | Voeg regels toe om te bepalen welke onderwerpen, content en data zijn toegestaan in AI-gesprekken. |
| `rules_empty_cta` | Add your first rule | Maak je eerste regel |
| `rules_new_button` | New rule | Nieuwe regel |
| `rules_enforced_title` | How rules are enforced | Hoe regels worden toegepast |
| `rules_enforced_chat` | Applied to every conversation | Gelden voor elk gesprek |
| `rules_enforced_knowledge` | Control how sources are used | Bepalen hoe bronnen worden gebruikt |
| `rules_enforced_privacy` | EU AI Act compliance applied automatically | EU AI Act compliance wordt automatisch toegepast |

---

## Implementation Plan

### Phase 1: Chat = Home (sidebar + homepage = chat)
**Files:** `route.tsx`, `index.tsx`, `chat.tsx`, `_components/KBScopeBar.tsx`, `messages/*.json`
- Sidebar: 3 items (Chat, Kennis, Regels)
- Move chat iframe + KBScopeBar from `chat.tsx` to `index.tsx`
- `chat.tsx` becomes redirect to `/app/`
- Visual refinement of KBScopeBar
- i18n messages compiled

### Phase 2: "Mijn kennis" page
**Files:** `knowledge/index.tsx`
- Redesign knowledge list: card-based collecties + unified notes list
- Add notebook list from Focus API
- Add docs list from Docs API
- New "Nieuw" dropdown (bron/notebook/document)

### Phase 3: "Regels" page
**Files:** New `routes/app/rules/index.tsx`
- Empty state with icon, description, CTA
- "How rules are enforced" explanation cards
- Frontend-only, no backend needed

### Phase 4: Re-parent Focus + Docs under /knowledge
**Files:** New routes under `knowledge/focus/` and `knowledge/docs/`
- Focus detail pages accessible at `/app/knowledge/focus/$id`
- Docs editor accessible at `/app/knowledge/docs/$kbSlug`

### Phase 5: Redirects + backward compatibility
**Files:** `chat.tsx`, `focus/index.tsx`, `docs/index.tsx`
- `/app/chat` → `/app/`
- `/app/focus` → `/app/knowledge`
- `/app/docs` → `/app/knowledge`

### Phase 6: Polish + verification
- Verify all redirects work
- Test all user flows (chat, knowledge, rules)
- Verify product gating still works
- TypeScript + build verification
- Browser testing

---

## Files Changed

| File | Change type | Phase |
|---|---|---|
| `messages/en.json` | Modified (new keys) | 1 |
| `messages/nl.json` | Modified (new keys) | 1 |
| `routes/app/route.tsx` | Modified (sidebar: 3 items) | 1 |
| `routes/app/index.tsx` | Rewritten (chat iframe + wrapper) | 1 |
| `routes/app/chat.tsx` | Rewritten (redirect to /app/) | 1 |
| `routes/app/_components/KBScopeBar.tsx` | Modified (visual refinement) | 1 |
| `routes/app/knowledge/index.tsx` | Rewritten (cards + notes list) | 2 |
| `routes/app/rules/index.tsx` | New (rules empty state) | 3 |
| `routes/app/knowledge/focus/*.tsx` | New (re-parent Focus pages) | 4 |
| `routes/app/knowledge/docs/*.tsx` | New (re-parent Docs pages) | 4 |
| `routes/app/focus/index.tsx` | Modified (redirect) | 5 |
| `routes/app/docs/index.tsx` | Modified (redirect) | 5 |

---

## What Does NOT Change

- Admin area (`/admin/*`) — untouched
- Backend APIs — no changes
- Auth flow — untouched
- Design system tokens — same colors, fonts, radius
- LibreChat itself — iframe stays
- Transcribe pages — content unchanged, just hidden from nav
- Account page — unchanged
- KB detail tab pages — same content, same paths under `/app/knowledge/$kbSlug/*`
- Focus detail/editor pages — same content, mounted at new parent path
- Docs editor page — same content, mounted at new parent path
