import { createFileRoute, Outlet, useNavigate, useParams } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { Input } from '@/components/ui/input'
import {
  DOCS_BASE,
  getOrgSlug,
  slugify,
  collectSlugs,
  navToSidebarEntries,
  addChildToNode,
  stripMdExt,
} from '@/lib/kb-editor/tree-utils'
import type { NavNode } from '@/lib/kb-editor/tree-utils'
import {
  KBEditorContext,
  resolveSlug,
  shortId,
  PageNotInIndexError,
  type SaveStatus,
  type PageIndexEntry,
} from '@/lib/kb-editor/KBEditorContext'
import { apiFetch } from '@/lib/apiFetch'
import { editorLogger, treeLogger } from '@/lib/logger'
import { SidebarPanel } from '@/components/kb-editor/SidebarPanel'
import { DeletePageModal } from '@/components/kb-editor/DeletePageModal'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { notifications } from '@mantine/notifications'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export const Route = createFileRoute('/app/docs/$kbSlug')({
  component: () => (
    <ProductGuard product="knowledge">
      <KBEditorLayout />
    </ProductGuard>
  ),
})

/** Response shape for new page creation (combined response from backend). REQ-EVT-02 */
interface CreatePageResponse {
  page: { id: string; slug: string; title: string; icon: string | null }
  pageIndex: PageIndexEntry[]
  idempotent?: boolean
}

function KBEditorLayout() {
  const { kbSlug } = Route.useParams()
  const allParams = useParams({ strict: false })
  const pageId = 'pageId' in allParams ? (allParams as { pageId: string }).pageId : undefined
  const navigate = useNavigate()
  const auth = useAuth()
  const token = auth.user?.access_token
  const orgSlug = getOrgSlug()

  // Shared display state — owned here, set by page component via context
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [editTitle, setEditTitle] = useState('')

  // Page's save+clear-timer function — registered by page via useEffect
  const doSaveRef = useRef<(() => Promise<void>) | null>(null)

  // REQ-EVT-01: Prevent duplicate page creation on double-click
  const creationLockRef = useRef(false)

  // Tree
  const { data: tree = [], refetch: refetchTree } = useQuery<NavNode[]>({
    queryKey: ['docs-tree', orgSlug, kbSlug],
    queryFn: async () => apiFetch<NavNode[]>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/tree`, token),
    enabled: !!token,
  })

  // PageIndex (id → slug mapping) — also settable synchronously for post-create update
  const { data: fetchedPageIndex = [], refetch: refetchPageIndex } = useQuery<PageIndexEntry[]>({
    queryKey: ['docs-page-index', orgSlug, kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<PageIndexEntry[]>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-index`, token)
      } catch {
        return []
      }
    },
    enabled: !!token,
  })

  // REQ-EVT-02: Synchronous pageIndex state that can be updated after page creation
  // without waiting for a refetch round-trip.
  const [localPageIndex, setLocalPageIndex] = useState<PageIndexEntry[] | null>(null)
  useEffect(() => { setLocalPageIndex(null) }, [fetchedPageIndex])
  const pageIndex = localPageIndex ?? fetchedPageIndex

  const setPageIndex = useCallback((entries: PageIndexEntry[]) => {
    setLocalPageIndex(entries)
  }, [])

  // Optimistic local tree for drag-and-drop
  const [localTree, setLocalTree] = useState<NavNode[] | null>(null)
  useEffect(() => { setLocalTree(null) }, [tree])
  const displayTree = localTree ?? tree

  // Derive selectedPath from URL — strict resolveSlug, never exposes a raw UUID as a slug
  const selectedPath = useMemo(() => {
    if (!pageId) return null
    if (pageIndex.length === 0) {
      // pageIndex not loaded yet — UUID cannot be resolved, non-UUID slugs are used directly
      return UUID_RE.test(pageId) ? null : pageId
    }
    try {
      return resolveSlug(pageId, pageIndex)
    } catch (err) {
      if (err instanceof PageNotInIndexError) return null
      return null
    }
  }, [pageId, pageIndex])

  // New-page UI state
  const [showNewPage, setShowNewPage] = useState(false)
  const [newPageTitle, setNewPageTitle] = useState('')
  const [newPageParent, setNewPageParent] = useState<string | null>(null)

  // Delete confirmation
  const [deletePagePath, setDeletePagePath] = useState<string | null>(null)

  // REQ-UBI-02: Navigate to a page by its UUID
  const navigateToPage = useCallback((targetPageId: string | null) => {
    if (!targetPageId) {
      void navigate({ to: '/app/docs/$kbSlug', params: { kbSlug } })
      return
    }
    void navigate({ to: '/app/docs/$kbSlug/$pageId', params: { kbSlug, pageId: targetPageId } })
  }, [kbSlug, navigate])

  // Drag-and-drop sidebar reorder
  const handleSidebarUpdate = useCallback(async (newTree: NavNode[]) => {
    const previousTree = localTree ?? tree
    setLocalTree(newTree)
    const pages = navToSidebarEntries(newTree)
    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/sidebar`, token, {
        method: 'PUT',
        body: JSON.stringify({ pages }),
      })
      await refetchTree()
    } catch (err) {
      treeLogger.error('Sidebar reorder failed, reverting', err)
      setLocalTree(previousTree)
    }
  }, [orgSlug, kbSlug, token, localTree, tree, refetchTree])

  const handleAddSubpage = useCallback((parentPath: string) => {
    setNewPageParent(parentPath)
    setNewPageTitle('')
    setShowNewPage(true)
  }, [])

  const handleNewPage = useCallback(async (parentPath: string | null = null) => {
    if (!newPageTitle.trim()) return

    // REQ-EVT-01: Prevent duplicate creation on double-click
    if (creationLockRef.current) return
    creationLockRef.current = true

    const baseSlug = slugify(newPageTitle)
    const existingSlugs = collectSlugs(displayTree)
    let slug = baseSlug
    let counter = 2
    while (existingSlugs.has(slug)) { slug = `${baseSlug}-${counter++}` }

    // REQ-UNW-01: Pre-creation save — explicit null check, not silent optional chaining
    if (doSaveRef.current !== null) {
      try {
        await doSaveRef.current()
      } catch (err) {
        editorLogger.error('Pre-creation save failed', { err })
        notifications.show({
          title: 'Save failed',
          message: 'Could not save current page before creating a new one. Please retry.',
          color: 'red',
        })
        creationLockRef.current = false
        return
      }
    }
    // If doSaveRef is null, editor is not mounted — proceed anyway (no content to save)

    // REQ-UBI-01: Generate unique Idempotency-Key per user action
    const idempotencyKey = crypto.randomUUID()

    try {
      const response = await apiFetch<CreatePageResponse>(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${slug}`,
        token,
        {
          method: 'PUT',
          headers: { 'Idempotency-Key': idempotencyKey },
          body: JSON.stringify({ title: newPageTitle, content: '' }),
        }
      )

      if (parentPath !== null) {
        const currentTree = localTree ?? tree
        const updatedTree = addChildToNode(currentTree, parentPath, slug)
        await handleSidebarUpdate(updatedTree)
      } else {
        await refetchTree()
      }

      // REQ-EVT-02 / REQ-UBI-03: Update pageIndex synchronously before navigation
      if (!response.pageIndex) {
        // REQ-STA-02: pageIndex refresh failed / missing in response
        editorLogger.error('Page created but pageIndex missing from response', { slug })
        notifications.show({
          title: 'Could not refresh page index',
          message: 'Page was created but the page index could not be refreshed. Please reload.',
          color: 'orange',
        })
        creationLockRef.current = false
        return
      }

      setPageIndex(response.pageIndex)

      setNewPageTitle('')
      setShowNewPage(false)
      setNewPageParent(null)

      // Navigate using UUID from the combined response
      navigateToPage(response.page.id)
    } catch (err) {
      editorLogger.error('Page creation failed', { slug, err })
      notifications.show({
        title: 'Page creation failed',
        message: 'Could not create the new page. Please try again.',
        color: 'red',
      })
    } finally {
      creationLockRef.current = false
    }
  }, [newPageTitle, orgSlug, kbSlug, token, displayTree, localTree, tree, handleSidebarUpdate, refetchTree, setPageIndex, navigateToPage])

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/upload`, token, {
        method: 'POST',
        body: form,
      })
    },
    onSuccess: () => void refetchTree(),
  })

  const deleteMutation = useMutation({
    mutationFn: async (path: string) => {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${path}`, token, {
        method: 'DELETE',
      })
    },
    onSuccess: (_data, path) => {
      if (selectedPath === path) navigateToPage(null)
      setDeletePagePath(null)
      void refetchTree()
    },
    onError: (err: Error) => {
      editorLogger.error('Page delete failed', { error: err.message })
    },
  })

  const ctx = useMemo(() => ({
    orgSlug,
    kbSlug,
    token,
    displayTree,
    pageIndex,
    setPageIndex,
    refetchTree,
    refetchPageIndex,
    doSaveRef,
    saveStatus,
    setSaveStatus,
    editTitle,
    setEditTitle,
    navigateToPage,
    setDeletePagePath,
  }), [orgSlug, kbSlug, token, displayTree, pageIndex, setPageIndex, refetchTree, refetchPageIndex, saveStatus, editTitle, navigateToPage])

  return (
    <KBEditorContext.Provider value={ctx}>
      <div className="flex h-full">
        <SidebarPanel
          displayTree={displayTree}
          selectedPath={selectedPath}
          editTitle={editTitle}
          showNewPage={showNewPage}
          newPageParent={newPageParent}
          newPageTitle={newPageTitle}
          saveStatus={saveStatus}
          onSelect={async (node) => {
            const newSlug = stripMdExt(node.path)
            if (newSlug === selectedPath) return

            // REQ-EVT-04: Await pending save before navigating to another page
            // REQ-UNW-01: Explicit null check — never silent skip
            if (doSaveRef.current !== null) {
              try {
                await doSaveRef.current()
              } catch (err) {
                editorLogger.error('Pre-navigation save failed', { from: selectedPath, to: newSlug, err })
                // REQ-STA-03: Show error, stay on current page
                notifications.show({
                  title: 'Save failed',
                  message: 'Could not save before navigating. Changes may be lost. Retry navigation to continue.',
                  color: 'red',
                  autoClose: 6000,
                })
                return // REQ-STA-03: do NOT navigate with unsaved changes
              }
            }

            // Look up target by slug, navigate by UUID
            const entry = pageIndex.find((p) => p.slug === newSlug)
            const targetId = shortId(entry)
            if (targetId) {
              navigateToPage(targetId)
            } else {
              // No UUID yet (draft page) — navigate by slug until UUID is assigned on first save
              void navigate({ to: '/app/docs/$kbSlug/$pageId', params: { kbSlug, pageId: newSlug } })
            }
          }}
          onSidebarUpdate={handleSidebarUpdate}
          onAddSubpage={handleAddSubpage}
          onDeletePage={setDeletePagePath}
          onShowNewPage={() => { setNewPageParent(null); setShowNewPage(true) }}
          onNewPageTitleChange={setNewPageTitle}
          onNewPageConfirm={handleNewPage}
          onNewPageCancel={() => { setShowNewPage(false); setNewPageTitle(''); setNewPageParent(null) }}
          onUpload={(file) => uploadMutation.mutate(file)}
        />

        {/* Editor pane — child route renders here */}
        <main className="flex-1 flex flex-col overflow-hidden">
          <Outlet />
        </main>

        <DeletePageModal
          open={!!deletePagePath}
          onOpenChange={(open) => { if (!open) setDeletePagePath(null) }}
          pageTitle={pageIndex.find((p) => p.slug === deletePagePath)?.title ?? deletePagePath ?? ''}
          onConfirm={() => { if (deletePagePath) deleteMutation.mutate(deletePagePath) }}
          isPending={deleteMutation.isPending}
        />
      </div>

      {/* Wikilink new-page input — kept here because it needs sidebar state */}
      {showNewPage && !newPageParent && (
        <div className="hidden">
          <Input
            autoFocus
            value={newPageTitle}
            onChange={(e) => setNewPageTitle(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void handleNewPage(null)
              if (e.key === 'Escape') { setShowNewPage(false); setNewPageTitle('') }
            }}
          />
        </div>
      )}
    </KBEditorContext.Provider>
  )
}
