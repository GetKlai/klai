import { createContext, useContext } from 'react'
import type { NavNode } from './tree-utils'

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
export type PageIndexEntry = { id: string | null; slug: string; title: string; icon?: string }

/** Thrown by resolveSlug when a pageId is not found in the current pageIndex. REQ-STA-04 */
export class PageNotInIndexError extends Error {
  constructor(pageId: string) {
    super(`Page not found in index: ${pageId}`)
    this.name = 'PageNotInIndexError'
  }
}

export interface KBEditorCtxValue {
  orgSlug: string
  kbSlug: string
  token: string | undefined
  displayTree: NavNode[]
  pageIndex: PageIndexEntry[]
  /** Synchronously update pageIndex with server-truth data. REQ-EVT-02 */
  setPageIndex: (entries: PageIndexEntry[]) => void
  refetchTree: () => void
  refetchPageIndex: () => Promise<{ data?: PageIndexEntry[] }>
  doSaveRef: React.MutableRefObject<(() => Promise<void>) | null>
  // Shared display state — owned by layout, set by page
  saveStatus: SaveStatus
  setSaveStatus: (s: SaveStatus) => void
  editTitle: string
  setEditTitle: (t: string) => void
  // Navigate to page by UUID (layout owns navigation)
  navigateToPage: (pageId: string | null) => void
  // Trigger delete confirmation modal (owned by layout)
  setDeletePagePath: (path: string | null) => void
}

export const KBEditorContext = createContext<KBEditorCtxValue | null>(null)

export function useKBEditor(): KBEditorCtxValue {
  const ctx = useContext(KBEditorContext)
  if (!ctx) throw new Error('useKBEditor must be used inside KBEditorLayout')
  return ctx
}

/**
 * Resolve a pageId (UUID or slug) to its slug using the pageIndex.
 *
 * REQ-STA-04: Strict mode — throws PageNotInIndexError if not found.
 * REQ-UNW-02: Never treats a UUID as a slug fallback.
 */
export function resolveSlug(pageId: string, pageIndex: PageIndexEntry[]): string {
  // First try UUID match (the canonical case for UUID-based URLs)
  const byId = pageIndex.find((p) => p.id === pageId)
  if (byId) return byId.slug

  // Then try slug match (for slug-based navigation / rename flows)
  const bySlug = pageIndex.find((p) => p.slug === pageId)
  if (bySlug) return bySlug.slug

  // Not found — throw instead of falling back to treating pageId as a slug
  throw new PageNotInIndexError(pageId)
}

/**
 * Get the stable UUID for URL routing.
 * REQ-UBI-02: Returns only the UUID id — no slug fallback.
 * Returns empty string when id is null (page not yet saved with UUID).
 */
export function shortId(entry: PageIndexEntry | undefined): string {
  return entry?.id ?? ''
}
