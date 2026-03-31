# SPEC-KB-016: URL-driven state for $kbSlug detail page

**Status:** Draft  
**Author:** Mark Vletter  
**Date:** 2026-03-31

---

## 1. Context & motivation

`routes/app/knowledge/$kbSlug.tsx` is a tabbed page with five to six tabs depending on the caller's role (Overview, Items, Connectors, Members, Taxonomy, Settings). All UI-state — active tab, which connector is in edit mode, and whether the add-connector form is open — lives in local `useState`. This means:

- Refreshing the page always lands on "Overview", losing your position.
- You cannot link a colleague directly to the Connectors tab of a specific KB.
- You cannot link to a connector that needs their attention in edit mode.
- Browser Back/Forward does not navigate between tabs.

This SPEC adds URL-driven state for the three pieces of state that are worth sharing via a URL. All form-filling and transient interaction state remains local.

---

## 2. URL schema

Base route remains: `/app/knowledge/:kbSlug`

### 2.1 Search parameters

| Param | Type | Values | Default | When present |
|---|---|---|---|---|
| `tab` | `KBTab` | `overview` \| `connectors` \| `members` \| `items` \| `taxonomy` \| `settings` | `overview` | Always optional |
| `edit` | `string` | connector UUID | — | Only when `tab=connectors` |
| `add` | `true` | literal `true` | — | Only when `tab=connectors` |

`edit` and `add` are mutually exclusive: opening one clears the other.

### 2.2 Examples

```
# Default landing
/app/knowledge/voys

# Connectors tab
/app/knowledge/voys?tab=connectors

# Connector in edit mode
/app/knowledge/voys?tab=connectors&edit=550e8400-e29b-41d4-a716-446655440000

# Add-connector form open
/app/knowledge/voys?tab=connectors&add=true

# Members tab
/app/knowledge/voys?tab=members
```

### 2.3 Invariants

- Unknown `tab` values are silently normalised to `overview` (not a hard error).
- `edit` present with `tab != connectors`: the edit param is ignored (tab wins).
- `add=true` present with `tab != connectors`: the add param is ignored.
- Both `edit` and `add` present simultaneously: `edit` wins, `add` is dropped.

---

## 3. State migration

### 3.1 State moving to URL (search params)

| Current state | New home | Notes |
|---|---|---|
| `activeTab: KBTab` in `KnowledgeDetailPage` | `?tab=` search param | Replace `useState` entirely |
| `editingId: string \| null` in `ConnectorsSection` | `?edit=<uuid>` search param | Passed down as prop or read via `Route.useSearch()` |
| `showAdd: boolean` in `ConnectorsSection` | `?add=true` search param | Passed down as prop |

### 3.2 State staying local (unchanged)

All of the following remain as `useState` — they are ephemeral form-filling or transient interaction state that would pollute the URL and break paste-ability:

**ConnectorsSection:**
- `selectedType` — which type the user picked in the add wizard
- `name`, `schedule`, `githubConfig`, `webcrawlerConfig`, `allowedAssertionModes` — add-form field values
- `editName`, `editSchedule`, `editWebcrawlerConfig`, `editGithubConfig`, `editAllowedAssertionModes` — edit-form field values
- `wcStep` — webcrawler wizard step (`details` | `preview` | `settings`)
- `wcPreviewUrl`, `showAdvancedSelector`, `createPreviewResult`, `editPreviewResult` — wizard UX
- `confirmingDeleteId` — inline delete confirmation
- `syncingIds` — set of connector IDs currently being synced

**MembersSection (line ~999):**
- `showInviteUser`, `showInviteGroup`, `inviteEmail`, `inviteGroupId`, `inviteRole` — invite form
- `confirmingRemoveUser`, `confirmingRemoveGroup` — inline remove confirmation

**TaxonomySection (line ~1344):**
- `expandedIds`, `editingId`, `editName`, `confirmDeleteId` — tree interaction
- `showAddRoot`, `addParentId`, `newNodeName` — add-node form
- `rejectingProposalId`, `rejectReason` — proposal rejection

**KnowledgeDetailPage:**
- `deleteModalOpen` — KB delete modal

---

## 4. TanStack Router implementation pattern

The project uses **TanStack Router v1 (`^1.168.7`)** with the manual `validateSearch` pattern (no Zod, consistent with `login.tsx`, `verify.tsx`, etc.).

### 4.1 Route definition

```typescript
// routes/app/knowledge/$kbSlug.tsx

type KBTab = 'overview' | 'connectors' | 'members' | 'items' | 'taxonomy' | 'settings'

type KBSearch = {
  tab?: KBTab
  edit?: string   // connector UUID; only relevant when tab=connectors
  add?: true      // only relevant when tab=connectors
}

const VALID_TABS = new Set<KBTab>(['overview', 'connectors', 'members', 'items', 'taxonomy', 'settings'])

export const Route = createFileRoute('/app/knowledge/$kbSlug')({
  validateSearch: (search: Record<string, unknown>): KBSearch => ({
    tab: VALID_TABS.has(search.tab as string) ? (search.tab as KBTab) : undefined,
    edit: typeof search.edit === 'string' ? search.edit : undefined,
    add: search.add === true || search.add === 'true' ? true : undefined,
  }),
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgeDetailPage />
    </ProductGuard>
  ),
})
```

### 4.2 Reading state in KnowledgeDetailPage

```typescript
function KnowledgeDetailPage() {
  const { kbSlug } = Route.useParams()
  const { tab = 'overview' } = Route.useSearch()
  const navigate = useNavigate({ from: '/app/knowledge/$kbSlug' })

  // activeTab is now derived, not useState
  const activeTab: KBTab = tab ?? 'overview'

  function setTab(t: KBTab) {
    // Switching tab clears connector-specific params
    void navigate({ search: { tab: t } })
  }
  // ...
}
```

### 4.3 Tab bar navigation

Replace `onClick={() => setActiveTab(id)}` with:

```typescript
onClick={() => setTab(id)}
```

### 4.4 Passing connector state to ConnectorsSection

`ConnectorsSection` currently lives in the same file and receives `kbSlug`, `token`, `isOwner` as props. Extend the props interface:

```typescript
interface ConnectorsSectionProps {
  kbSlug: string
  token: string | undefined
  isOwner: boolean
  editingConnectorId: string | undefined   // from ?edit=
  isAddOpen: boolean                        // from ?add=true
  onOpenAdd: () => void
  onCloseAdd: () => void
  onOpenEdit: (id: string) => void
  onCloseEdit: () => void
}
```

Caller (KnowledgeDetailPage) provides these by reading `Route.useSearch()` and wiring `navigate` callbacks:

```typescript
const { tab = 'overview', edit, add } = Route.useSearch()

function openAdd() {
  void navigate({ search: (prev) => ({ ...prev, tab: 'connectors', add: true, edit: undefined }) })
}
function closeAdd() {
  void navigate({ search: (prev) => ({ ...prev, add: undefined }) })
}
function openEdit(id: string) {
  void navigate({ search: (prev) => ({ ...prev, tab: 'connectors', edit: id, add: undefined }) })
}
function closeEdit() {
  void navigate({ search: (prev) => ({ ...prev, edit: undefined }) })
}
```

### 4.5 Why `navigate({ search: (prev) => ... })`

The `$kbSlug` path param is preserved automatically by TanStack Router when navigating with `from` set. The functional form `search: (prev) => ({ ...prev, ... })` is used for partial updates (e.g. clearing just `edit` without touching `tab`). A plain object `search: { tab: t }` resets all params, which is correct only when switching tabs (where edit/add must be cleared anyway).

---

## 5. Acceptance criteria

**AC-1 — Tab is reflected in URL**  
When a user clicks a tab, `?tab=<tab-id>` appears in the address bar. Browser Back returns to the previous tab.

**AC-2 — Tab is read from URL on load**  
Navigating directly to `/app/knowledge/voys?tab=connectors` opens the Connectors tab without visiting Overview first.

**AC-3 — Unknown tab gracefully falls back**  
Navigating to `?tab=banana` renders the Overview tab. No error is thrown.

**AC-4 — Connector edit mode is reflected in URL**  
Clicking "Edit" on a connector causes `?tab=connectors&edit=<uuid>` to appear. Refreshing reopens that connector in edit mode.

**AC-5 — Add form is reflected in URL**  
Clicking "Add connector" causes `?tab=connectors&add=true` to appear. Refreshing reopens the add form.

**AC-6 — edit and add are mutually exclusive**  
Clicking "Edit" while the add form is open (or vice versa) drops the other param. Both params never coexist.

**AC-7 — Tab switch clears connector params**  
Navigating to any tab other than Connectors drops `edit` and `add` from the URL.

**AC-8 — edit/add ignored on wrong tab**  
`/app/knowledge/voys?tab=overview&edit=uuid` renders Overview without opening any edit form.

**AC-9 — $kbSlug is never lost**  
After any tab or connector-state navigation, the URL still contains the correct `$kbSlug` path segment.

**AC-10 — Form-filling state is not persisted in URL**  
Field values (name, schedule, URL, config) are not present in the URL at any point during form filling.

**AC-11 — TypeScript compiles cleanly**  
`npm run build` (which includes `tsc -b`) passes with no new errors.

**AC-12 — ESLint passes**  
`npm run lint` passes with no new errors.

---

## 6. Out of scope

- Members tab state (invite form open/closed): single-step, low value for deep-linking.
- Taxonomy tab state: tree expansion, editing, proposals — local interaction only.
- Webcrawler wizard step (`wcStep`): part of the form-filling lifecycle, not linkable.
- Settings tab state: no sub-state worth exposing.
- Scroll position restoration.
- Animated tab transitions.

---

## 7. Files affected

| File | Change |
|---|---|
| `routes/app/knowledge/$kbSlug.tsx` | Add `validateSearch`, replace `useState` for tab, wire props for connector state |

No new files. No backend changes. No i18n changes (no new user-visible strings).
