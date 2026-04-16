import { createContext, useContext } from 'react'
import type { NavNode } from './tree-utils'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
export type PageIndexEntry = { id: string | null; slug: string; title: string; icon?: string }

export interface KBEditorCtxValue {
  orgSlug: string
  kbSlug: string
  token: string | undefined
  displayTree: NavNode[]
  pageIndex: PageIndexEntry[]
  refetchTree: () => void
  refetchPageIndex: () => void
  doSaveRef: React.MutableRefObject<(() => Promise<void>) | null>
  // Shared display state — owned by layout, set by page
  saveStatus: SaveStatus
  setSaveStatus: (s: SaveStatus) => void
  editTitle: string
  setEditTitle: (t: string) => void
  // Navigate to page by slug (layout resolves slug → short ID)
  navigateToPage: (slug: string | null) => void
  // Trigger delete confirmation modal (owned by layout)
  setDeletePagePath: (path: string | null) => void
}

export const KBEditorContext = createContext<KBEditorCtxValue | null>(null)

export function useKBEditor(): KBEditorCtxValue {
  const ctx = useContext(KBEditorContext)
  if (!ctx) throw new Error('useKBEditor must be used inside KBEditorLayout')
  return ctx
}

/** Resolve a short 8-char page ID to its slug. Falls back to treating id as slug. */
export function resolveSlug(pageId: string, pageIndex: PageIndexEntry[]): string {
  const match = pageIndex.find((p) => p.id && p.id.startsWith(pageId))
  return match?.slug ?? pageId
}

/** Get the short ID for a slug. Falls back to slug itself when id is null. */
export function shortId(entry: PageIndexEntry | undefined): string {
  return entry?.id ? entry.id.slice(0, 8) : (entry?.slug ?? '')
}
