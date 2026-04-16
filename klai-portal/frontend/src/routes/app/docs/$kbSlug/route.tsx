import { createFileRoute, Outlet, useNavigate, useParams } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
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
  type SaveStatus,
  type PageIndexEntry,
} from '@/lib/kb-editor/KBEditorContext'
import { apiFetch } from '@/lib/apiFetch'
import { editorLogger, treeLogger } from '@/lib/logger'
import { SidebarPanel } from '@/components/kb-editor/SidebarPanel'
import { DeletePageModal } from '@/components/kb-editor/DeletePageModal'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/docs/$kbSlug')({
  component: () => (
    <ProductGuard product="knowledge">
      <KBEditorLayout />
    </ProductGuard>
  ),
})

function KBEditorLayout() {
  const { kbSlug } = Route.useParams()
  // Read child route param (strict: false = read params from any matched route)
  const { pageId } = useParams({ strict: false }) as { pageId?: string }
  const navigate = useNavigate()
  const auth = useAuth()
  const token = auth.user?.access_token
  const orgSlug = getOrgSlug()

  // Shared display state — owned here, set by page component via context
  const [saveStatus, setSaveStatus] = useState<SaveStatus>('idle')
  const [editTitle, setEditTitle] = useState('')

  // Page's save+clear-timer function — registered by page via useEffect
  const doSaveRef = useRef<(() => Promise<void>) | null>(null)

  // Tree
  const { data: tree = [], refetch: refetchTree } = useQuery<NavNode[]>({
    queryKey: ['docs-tree', orgSlug, kbSlug],
    queryFn: async () => apiFetch<NavNode[]>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/tree`, token),
    enabled: !!token,
  })

  // PageIndex (id → slug mapping)
  const { data: pageIndex = [], refetch: refetchPageIndex } = useQuery<PageIndexEntry[]>({
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

  // Optimistic local tree for drag-and-drop
  const [localTree, setLocalTree] = useState<NavNode[] | null>(null)
  useEffect(() => { setLocalTree(null) }, [tree])
  const displayTree = localTree ?? tree

  // Derive selectedPath from URL (resolves 8-char ID or slug fallback)
  const selectedPath = pageId ? resolveSlug(pageId, pageIndex) : null

  // New-page UI state
  const [showNewPage, setShowNewPage] = useState(false)
  const [newPageTitle, setNewPageTitle] = useState('')
  const [newPageParent, setNewPageParent] = useState<string | null>(null)

  // Delete confirmation
  const [deletePagePath, setDeletePagePath] = useState<string | null>(null)

  // Navigate to a page by its slug — resolves to 8-char ID when available
  const navigateToPage = useCallback((slug: string | null) => {
    if (!slug) {
      void navigate({ to: '/app/docs/$kbSlug', params: { kbSlug } })
      return
    }
    const entry = pageIndex.find((p) => p.slug === slug)
    const pid = shortId(entry) || slug
    void navigate({ to: '/app/docs/$kbSlug/$pageId', params: { kbSlug, pageId: pid } })
  }, [kbSlug, navigate, pageIndex])

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

    const baseSlug = slugify(newPageTitle)
    const existingSlugs = collectSlugs(displayTree)
    let slug = baseSlug
    let counter = 2
    while (existingSlugs.has(slug)) { slug = `${baseSlug}-${counter++}` }

    // Save current page before switching
    await doSaveRef.current?.()

    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${slug}`, token, {
        method: 'PUT',
        body: JSON.stringify({ title: newPageTitle, content: '' }),
      })

      if (parentPath !== null) {
        const currentTree = localTree ?? tree
        const updatedTree = addChildToNode(currentTree, parentPath, slug)
        await handleSidebarUpdate(updatedTree)
      } else {
        await refetchTree()
      }

      setNewPageTitle('')
      setShowNewPage(false)
      setNewPageParent(null)
      navigateToPage(slug)
    } catch (err) {
      editorLogger.error('Page creation failed', { slug, err })
    }
  }, [newPageTitle, orgSlug, kbSlug, token, displayTree, localTree, tree, handleSidebarUpdate, refetchTree, navigateToPage])

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
    refetchTree,
    refetchPageIndex,
    doSaveRef,
    saveStatus,
    setSaveStatus,
    editTitle,
    setEditTitle,
    navigateToPage,
    setDeletePagePath,
  }), [orgSlug, kbSlug, token, displayTree, pageIndex, refetchTree, refetchPageIndex, saveStatus, editTitle, navigateToPage])

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
            await doSaveRef.current?.()
            navigateToPage(newSlug)
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
