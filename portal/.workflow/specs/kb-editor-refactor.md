# SPEC: KB Editor Architectuur Refactoring

**Status:** DONE
**Scope:** `frontend/src/routes/app/docs/$kbSlug.tsx` (~1500 regels) + bijbehorende extracties
**Doel:** Structurele refactoring zonder functionaliteitswijziging (D+ → B)

---

## 1. Wat NIET wijzigt

- Externe API calls en query keys blijven exact hetzelfde
- `BlockPageEditor` component interface (ref handle, props) blijft stabiel
- `WikiLink.tsx` wordt niet aangeraakt
- Alle UI, visuele output en gebruikersfunctionaliteit blijft 1:1 identiek
- TanStack Query keys: `['docs-tree', ...]`, `['docs-page', ...]`, `['docs-page-index', ...]`

---

## 2. Nieuwe bestandsstructuur

```
frontend/src/
├── lib/
│   └── kb-editor/
│       ├── tree-utils.ts          ← pure functies + interfaces
│       └── useTreeNavigation.ts   ← hook: collapsedIds, flatNodes, toggle
│
├── components/
│   └── kb-editor/
│       ├── NavTree.tsx            ← DndContext, SortableContext, overlay
│       ├── SortableNavItem.tsx    ← één nav-item met DnD + context menu
│       ├── NavItemOverlay.tsx     ← drag overlay component
│       ├── SidebarPanel.tsx       ← <aside> wrapper: NavTree + SidebarFooter
│       ├── SidebarFooter.tsx      ← upload-knop + nieuwe-pagina input/knop
│       ├── EditorHeader.tsx       ← titel, icoon, save-status, ⋯ menu
│       ├── AccessControlPanel.tsx ← access-state + UI-paneel
│       └── BlockPageEditor.tsx    ← BlockNote editor (ref-handle stabiel)
│
└── routes/app/docs/
    ├── $kbSlug.tsx                ← coördinator, sterk ingekrompen
    └── WikiLink.tsx               ← ongewijzigd
```

---

## 3. State consolidatie

### Huidig: 19 × `useState` in `KBEditorPage`

| Nr | Naam | Type |
|----|------|------|
| 1 | selectedPath | string \| null |
| 2 | editTitle | string |
| 3 | editContent | string |
| 4 | pageIcon | string |
| 5 | showIconPicker | boolean |
| 6 | editorKey | number |
| 7 | saveStatus | 'idle'\|'saving'\|'saved'\|'renamed'\|'error' |
| 8 | showWikilinkPicker | boolean |
| 9 | wikilinkSearch | string |
| 10 | newPageTitle | string |
| 11 | showNewPage | boolean |
| 12 | newPageParent | string \| null |
| 13 | showMenu | boolean |
| 14 | showAccessPanel | boolean |
| 15 | accessMode | 'org'\|'specific' |
| 16 | accessUsers | string[] |
| 17 | newUserId | string |
| 18 | accessSaveStatus | 'idle'\|'saving'\|'saved'\|'error' |
| 19 | localTree | NavNode[] \| null |

### Nieuw: 4 state-objecten in `KBEditorPage`

```ts
// 1. Huidige pagina (lifecycle gebonden aan page query)
const [currentPage, setCurrentPage] = useState<{
  path: string | null
  title: string
  content: string
  icon: string
  editorKey: number
}>({ path: null, title: '', content: '', icon: DEFAULT_ICON, editorKey: 0 })

// 2. Access control (verplaatst naar AccessControlPanel via props/callbacks)
const [accessState, setAccessState] = useState<{
  show: boolean
  mode: 'org' | 'specific'
  users: string[]
  newUserId: string
  saveStatus: 'idle' | 'saving' | 'saved' | 'error'
}>({ show: false, mode: 'org', users: [], newUserId: '', saveStatus: 'idle' })

// 3. UI state (transient, niet page-gebonden)
const [uiState, setUiState] = useState<{
  saveStatus: 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
  showIconPicker: boolean
  showWikilinkPicker: boolean
  wikilinkSearch: string
  showNewPage: boolean
  newPageTitle: string
  newPageParent: string | null
  showMenu: boolean
}>({
  saveStatus: 'idle',
  showIconPicker: false,
  showWikilinkPicker: false,
  wikilinkSearch: '',
  showNewPage: false,
  newPageTitle: '',
  newPageParent: null,
  showMenu: false,
})

// 4. Optimistische boom (standalone)
const [localTree, setLocalTree] = useState<NavNode[] | null>(null)
```

**Totaal: 4 useState** (was 19)

---

## 4. Extractie van `tree-utils.ts`

Verplaats de volgende pure functies en interfaces naar `frontend/src/lib/kb-editor/tree-utils.ts`:

- Interfaces: `NavNode`, `SidebarEntry`, `FlatNode`, `Projection`
- Constants: `INDENT_WIDTH`, `DEFAULT_ICON`
- Functies: `flattenTree`, `buildTree`, `getProjection`, `navToSidebarEntries`, `addChildToNode`
- Hulpfuncties die al in het bestand staan: `slugify`, `getOrgSlug`

Alles geëxporteerd, zodat het testbaar is.

---

## 5. `useTreeNavigation` hook

Nieuw bestand `frontend/src/lib/kb-editor/useTreeNavigation.ts`:

```ts
import { useState, useMemo, useCallback } from 'react'
import { flattenTree } from './tree-utils'
import type { NavNode, FlatNode } from './tree-utils'

export function useTreeNavigation(nodes: NavNode[]) {
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set())

  const flatNodes: FlatNode[] = useMemo(
    () => flattenTree(nodes, collapsedIds),
    [nodes, collapsedIds]
  )

  const toggleCollapse = useCallback((id: string) => {
    setCollapsedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  return { collapsedIds, flatNodes, toggleCollapse }
}
```

---

## 6. `window.__flatTreePointerY` → `useRef`

Huidig anti-pattern in `NavTree`:

```ts
// Nu: globale mutable state
(window as unknown as { __flatTreePointerY: number }).__flatTreePointerY = e.clientY
```

Fix: vervang door een `pointerYRef = useRef<number>(0)` in `NavTree`, geef als prop/callback door aan `getProjection`:

```ts
// getProjection signature uitgebreid:
function getProjection(
  items: FlatNode[],
  activeId: string,
  overId: string,
  deltaX: number,
  pointerY: number,   // ← nieuw
): Projection
```

In `NavTree`:
```ts
const pointerYRef = useRef<number>(0)
useEffect(() => {
  const onMove = (e: MouseEvent) => { pointerYRef.current = e.clientY }
  window.addEventListener('mousemove', onMove)
  return () => window.removeEventListener('mousemove', onMove)
}, [])
```

---

## 7. Race condition fix

Huidig probleem in `useEffect([page])`: als de gebruiker snel van pagina wisselt, overschrijft een late response de huidige pagina.

Fix:
```ts
useEffect(() => {
  if (!page) return
  // Guard: alleen toepassen als dit nog steeds de geselecteerde pagina is
  if (!currentPage.path || page.frontmatter.title === undefined) return
  // ... rest van de effect
}, [page])
```

Correctere fix: vergelijk de query key. Omdat `page` afkomstig is van `useQuery(['docs-page', ..., selectedPath])`, is de page altijd consistent met `selectedPath` op het moment van ontvangst. De race treedt op als `selectedPath` verandert terwijl een fetch nog loopt.

Fix via guard in de effect:
```ts
useEffect(() => {
  if (!page) return
  setCurrentPage((prev) => {
    // currentPage.path is al bijgewerkt door onSelect
    // page.frontmatter bevat de data van de HUIDIGE query key
    // Geen actie nodig als path inmiddels alweer gewisseld is
    // (React 18 batching + Suspense zorgt er al voor, maar ter veiligheid:)
    return {
      ...prev,
      title: page.frontmatter.title ?? '',
      content: page.content,
      icon: page.frontmatter.icon ?? DEFAULT_ICON,
      editorKey: prev.editorKey + 1,
    }
  })
  setAccessState({ show: false, mode: 'org' | 'specific', ... })
  setUiState((prev) => ({ ...prev, saveStatus: 'idle' }))
}, [page])
```

---

## 8. Autosave bij navigatie

Huidig probleem: `onSelect` in NavTree wist de editor zonder eerst op te slaan.

Huidig:
```ts
onSelect={(node) => {
  if (saveTimerRef.current) clearTimeout(saveTimerRef.current)  // ← timer gecanceld, GEEN save
  setSelectedPath(node.path.replace(/\.md$/, ''))
  ...
}}
```

Fix: force `doSave()` voor navigatie:
```ts
onSelect={async (node) => {
  if (saveTimerRef.current) {
    clearTimeout(saveTimerRef.current)
    saveTimerRef.current = null
    await doSave()  // ← force save voor switch
  }
  setCurrentPage((prev) => ({
    ...prev,
    path: node.path.replace(/\.md$/, ''),
    content: '',
    editorKey: prev.editorKey + 1,
  }))
}}
```

---

## 9. Props sanering `SortableNavItem`

Huidig: `SortableNavItemProps` heeft 16 props. Na extractie van `useTreeNavigation` (encapsuleert `collapsedIds`, `flatNodes`, `toggleCollapse`) en verplaatsing van newPage-state naar `SidebarPanel`, reduceert dit naar:

```ts
interface SortableNavItemProps {
  flat: FlatNode
  flatNodes: FlatNode[]          // voor context-menu acties
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  displayTitle?: string          // live title override voor actieve pagina
  onSidebarUpdate: (newTree: NavNode[]) => void
  onAddSubpage: (parentPath: string) => void
  addingSubpageUnder: string | null
  newPageTitle: string
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: (parentPath: string) => void
  onNewPageCancel: () => void
  isCollapsed: boolean
  onToggleCollapse: (id: string) => void
  isDraggingActive: boolean
}
```

Gereduceerd van 16 naar ~15 props (marginaal). Echte winst zit in het verwijderen van de `activeTitle`/`activePath` combinatie door één `displayTitle` te berekenen in `NavTree` en als prop door te geven.

---

## 10. Volgorde van extractie (stap voor stap)

Elke stap eindigt met `cd frontend && npx tsc --noEmit`. Bij fout: repareer voor volgende stap.

| Stap | Actie | Risico |
|------|-------|--------|
| **1** | Maak `tree-utils.ts` aan — kopieer pure functies/interfaces, exporteer alles | Laag — alleen imports veranderen |
| **2** | Update imports in `$kbSlug.tsx` naar `tree-utils.ts`; verwijder lokale definities | Laag |
| **3** | Maak `useTreeNavigation.ts` aan — extraheer collapsedIds + flatNodes useMemo | Laag — alleen intern NavTree |
| **4** | Update `NavTree` om hook te gebruiken; vervang `window.__flatTreePointerY` met `pointerYRef` | Medium |
| **5** | Extraheer `NavItemOverlay` naar eigen bestand | Laag |
| **6** | Extraheer `SortableNavItem` naar eigen bestand | Medium |
| **7** | Extraheer `NavTree` naar eigen bestand | Medium |
| **8** | Extraheer `BlockPageEditor` naar eigen bestand | Laag |
| **9** | Extraheer `AccessControlPanel` naar eigen bestand (ontvangt accessState als props/callbacks) | Medium |
| **10** | Extraheer `EditorHeader` naar eigen bestand | Medium |
| **11** | Maak `SidebarFooter` en `SidebarPanel` aan | Medium |
| **12** | State consolidatie in `KBEditorPage`: 19 useState → 4 objecten | Hoog — meeste aanrakingen |
| **13** | Race condition fix (useEffect guard) | Laag |
| **14** | Autosave fix bij navigatie | Laag |
| **15** | Finale TypeScript check + commit | — |

---

## 11. Interfaces die geëxporteerd worden vanuit `tree-utils.ts`

```ts
export interface NavNode { ... }
export interface SidebarEntry { ... }
export interface FlatNode { ... }
export interface Projection { ... }
export const INDENT_WIDTH = 12
export const DEFAULT_ICON = '📄'
export const DOCS_BASE = '/docs/api'
export function getOrgSlug(): string { ... }
export function slugify(title: string): string { ... }
export function navToSidebarEntries(nodes: NavNode[]): SidebarEntry[] { ... }
export function addChildToNode(nodes: NavNode[], parentPath: string, newSlug: string): NavNode[] { ... }
export function flattenTree(nodes: NavNode[], collapsed: Set<string>, depth?: number, parentId?: string | null): FlatNode[] { ... }
export function getProjection(items: FlatNode[], activeId: string, overId: string, deltaX: number, pointerY: number): Projection { ... }
export function buildTree(flatNodes: FlatNode[], projection: Projection, activeId: string): NavNode[] { ... }
```

---

## 12. Acceptatiecriteria

- [ ] TypeScript compileert zonder fouten na elke extractiestap
- [ ] Drag-and-drop werkt identiek (inclusief under-first-item fix)
- [ ] Nieuwe pagina aanmaken werkt (unieke slug + save huidige pagina)
- [ ] Wikilink picker opent en voegt link in
- [ ] Access control paneel laadt en slaat op
- [ ] Navigatie tussen pagina's behoudt content (autosave fix)
- [ ] `window.__flatTreePointerY` is weg uit codebase
- [ ] `flattenTree` wordt gecachet via `useMemo` in hook

---

## 13. Buiten scope

- Emoji picker positie (fixed/portal) — apart issue
- Unit tests schrijven
- Stijlwijzigingen of nieuwe features
