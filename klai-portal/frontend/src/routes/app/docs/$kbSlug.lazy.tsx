import { createLazyFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useState, useEffect, useRef, useCallback } from 'react'
import * as m from '@/paraglide/messages'
import { Input } from '@/components/ui/input'
import {
  DOCS_BASE,
  DEFAULT_ICON,
  getOrgSlug,
  slugify,
  collectSlugs,
  navToSidebarEntries,
  addChildToNode,
  stripMdExt,
} from '@/lib/kb-editor/tree-utils'
import type { NavNode } from '@/lib/kb-editor/tree-utils'

import { apiFetch } from '@/lib/apiFetch'
import { editorLogger, treeLogger } from '@/lib/logger'
import { BlockPageEditor } from '@/components/kb-editor/BlockPageEditor'
import type { BlockPageEditorHandle } from '@/components/kb-editor/BlockPageEditor'
import { AccessControlPanel } from '@/components/kb-editor/AccessControlPanel'
import { EditorHeader } from '@/components/kb-editor/EditorHeader'
import { DeletePageModal } from '@/components/kb-editor/DeletePageModal'
import { SidebarPanel } from '@/components/kb-editor/SidebarPanel'

import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createLazyFileRoute('/app/docs/$kbSlug')({
  component: () => (
    <ProductGuard product="knowledge">
      <KBEditorPage />
    </ProductGuard>
  ),
})

interface PageData {
  frontmatter: { title?: string; description?: string; edit_access?: 'org' | string[]; icon?: string }
  content: string
}

function KBEditorPage() {
  const { kbSlug } = Route.useParams()
  const { page: initialPage } = Route.useSearch()
  const navigate = useNavigate()
  const auth = useAuth()
  const token = auth.user?.access_token
  const orgSlug = getOrgSlug()

  const [currentPage, setCurrentPage] = useState<{
    path: string | null
    title: string
    content: string
    icon: string
    editorKey: number
  }>({ path: initialPage ?? null, title: '', content: '', icon: DEFAULT_ICON, editorKey: 0 })

  const [uiState, setUiState] = useState<{
    saveStatus: 'idle' | 'saving' | 'saved' | 'renamed' | 'error'
    showWikilinkPicker: boolean
    wikilinkSearch: string
    showNewPage: boolean
    newPageTitle: string
    newPageParent: string | null
  }>({
    saveStatus: 'idle',
    showWikilinkPicker: false,
    wikilinkSearch: '',
    showNewPage: false,
    newPageTitle: '',
    newPageParent: null,
  })

  const [accessState, setAccessState] = useState<{
    show: boolean
    mode: 'org' | 'specific'
    users: string[]
    newUserId: string
    saveStatus: 'idle' | 'saving' | 'saved' | 'error'
  }>({ show: false, mode: 'org', users: [], newUserId: '', saveStatus: 'idle' })

  // Optimistic local tree (null = use server tree)
  const [localTree, setLocalTree] = useState<NavNode[] | null>(null)

  // Page deletion state — path of page pending delete confirmation (null = modal closed)
  const [deletePagePath, setDeletePagePath] = useState<string | null>(null)

  // Convenience aliases
  const selectedPath = currentPage.path
  const editTitle = currentPage.title
  const editContent = currentPage.content
  const pageIcon = currentPage.icon
  const editorKey = currentPage.editorKey
  const saveStatus = uiState.saveStatus
  const showWikilinkPicker = uiState.showWikilinkPicker
  const wikilinkSearch = uiState.wikilinkSearch
  const showNewPage = uiState.showNewPage
  const newPageTitle = uiState.newPageTitle
  const newPageParent = uiState.newPageParent
  const showAccessPanel = accessState.show
  const accessMode = accessState.mode
  const accessUsers = accessState.users
  const newUserId = accessState.newUserId
  const accessSaveStatus = accessState.saveStatus

  const setSelectedPath = (path: string | null) => {
    setCurrentPage((p) => ({ ...p, path }))
    void navigate({ from: '/app/docs/$kbSlug', search: { page: path ?? undefined }, replace: true })
  }
  const setEditTitle = (title: string) =>
    setCurrentPage((p) => ({ ...p, title }))
  const setEditContent = (content: string) =>
    setCurrentPage((p) => ({ ...p, content }))
  const setPageIcon = (icon: string) =>
    setCurrentPage((p) => ({ ...p, icon }))
  const setEditorKey = (fn: ((k: number) => number) | number) =>
    setCurrentPage((p) => ({ ...p, editorKey: typeof fn === 'function' ? fn(p.editorKey) : fn }))
  const setSaveStatus = (s: typeof uiState.saveStatus) =>
    setUiState((u) => ({ ...u, saveStatus: s }))
  const setShowWikilinkPicker = (v: boolean) =>
    setUiState((u) => ({ ...u, showWikilinkPicker: v }))
  const setWikilinkSearch = (v: string) =>
    setUiState((u) => ({ ...u, wikilinkSearch: v }))
  const setShowNewPage = (v: boolean) =>
    setUiState((u) => ({ ...u, showNewPage: v }))
  const setNewPageTitle = (v: string) =>
    setUiState((u) => ({ ...u, newPageTitle: v }))
  const setNewPageParent = (v: string | null) =>
    setUiState((u) => ({ ...u, newPageParent: v }))
  const setShowAccessPanel = (fn: ((v: boolean) => boolean) | boolean) =>
    setAccessState((a) => ({ ...a, show: typeof fn === 'function' ? fn(a.show) : fn }))
  const setAccessMode = (mode: 'org' | 'specific') =>
    setAccessState((a) => ({ ...a, mode }))
  const setAccessUsers = (fn: ((users: string[]) => string[]) | string[]) =>
    setAccessState((a) => ({ ...a, users: typeof fn === 'function' ? fn(a.users) : fn }))
  const setNewUserId = (v: string) =>
    setAccessState((a) => ({ ...a, newUserId: v }))
  const setAccessSaveStatus = (s: typeof accessState.saveStatus) =>
    setAccessState((a) => ({ ...a, saveStatus: s }))

  const editorRef = useRef<BlockPageEditorHandle>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Stable refs for save function
  const tokenRef = useRef(token)
  const selectedPathRef = useRef(selectedPath)
  const editTitleRef = useRef(editTitle)
  const pageIconRef = useRef(pageIcon)
  tokenRef.current = token
  selectedPathRef.current = selectedPath
  editTitleRef.current = editTitle
  pageIconRef.current = pageIcon


  const { data: tree = [], refetch: refetchTree } = useQuery<NavNode[]>({
    queryKey: ['docs-tree', orgSlug, kbSlug],
    queryFn: async () => apiFetch<NavNode[]>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/tree`, token),
    enabled: !!token,
    select: (data) => data,
  })

  // Clear local tree when the server tree refreshes
  useEffect(() => {
    setLocalTree(null)
  }, [tree])

  const displayTree = localTree ?? tree

  const { data: page } = useQuery<PageData>({
    queryKey: ['docs-page', orgSlug, kbSlug, selectedPath],
    queryFn: async () => apiFetch<PageData>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${selectedPath}`, token),
    enabled: !!token && !!selectedPath,
  })

  const { data: pageIndex = [] } = useQuery<Array<{ id: string | null; slug: string; title: string; icon?: string }>>({
    queryKey: ['docs-page-index', orgSlug, kbSlug],
    queryFn: async () => {
      try {
        return await apiFetch<Array<{ id: string | null; slug: string; title: string; icon?: string }>>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-index`, token)
      } catch {
        return []
      }
    },
    enabled: !!token,
  })

  useEffect(() => {
    // Guard: only apply if we still have a page selected (prevents stale data
    // from overwriting state after the user has navigated away).
    if (!page || !selectedPathRef.current) return

    const ea = page.frontmatter.edit_access
    setCurrentPage((p) => ({
      ...p,
      title: page.frontmatter.title ?? '',
      content: page.content,
      icon: page.frontmatter.icon ?? DEFAULT_ICON,
      editorKey: p.editorKey + 1,
    }))
    setUiState((u) => ({ ...u, saveStatus: 'idle' }))
    setAccessState({
      show: false,
      mode: !ea || ea === 'org' ? 'org' : 'specific',
      users: !ea || ea === 'org' ? [] : Array.isArray(ea) ? ea : [ea],
      newUserId: '',
      saveStatus: 'idle',
    })
  }, [page])

  // parentPath: null = root level, string = path of the parent node (without .md)
  const handleNewPage = async (parentPath: string | null = null) => {
    if (!newPageTitle.trim()) return

    // Generate a unique slug that doesn't collide with existing pages
    const baseSlug = slugify(newPageTitle)
    const existingSlugs = collectSlugs(displayTree)
    let slug = baseSlug
    let counter = 2
    while (existingSlugs.has(slug)) {
      slug = `${baseSlug}-${counter++}`
    }

    const path = slug

    // Save the currently open page before switching, to prevent losing unsaved changes
    if (selectedPathRef.current && saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
      await doSave()
    } else if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }

    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${path}`, token, {
        method: 'PUT',
        body: JSON.stringify({ title: newPageTitle, content: '' }),
      })

      if (parentPath !== null) {
        // Insert the new slug as a child of parentPath in the sidebar
        const currentTree = localTree ?? tree
        const updatedTree = addChildToNode(currentTree, parentPath, slug)
        await handleSidebarUpdate(updatedTree)
      } else {
        await refetchTree()
      }

      setSelectedPath(path)
      setEditTitle(newPageTitle)
      setEditContent('')
      setEditorKey((k) => k + 1)
      setNewPageTitle('')
      setShowNewPage(false)
      setNewPageParent(null)
    } catch (err) {
      editorLogger.error('Page creation failed', { slug: path, err })
    }
  }

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/upload`, token, {
        method: 'POST',
        body: form,
      })
    },
    onSuccess: () => refetchTree(),
  })

  const deleteMutation = useMutation({
    mutationFn: async (path: string) => {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${path}`, token, {
        method: 'DELETE',
      })
    },
    onSuccess: (_data, path) => {
      // If the deleted page was the currently selected page, clear editor
      if (selectedPath === path) {
        setSelectedPath(null)
        setEditTitle('')
        setEditContent('')
        setEditorKey((k) => k + 1)
      }
      setDeletePagePath(null)
      void refetchTree()
    },
    onError: (err: Error) => {
      editorLogger.error('Page delete failed', { error: err.message })
    },
  })

  const doSave = useCallback(async () => {
    const path = selectedPathRef.current
    const title = editTitleRef.current
    const tok = tokenRef.current
    if (!path || !tok) return
    const content = editorRef.current?.getMarkdown() ?? ''

    const currentSlug = stripMdExt(path)
    const newSlug = slugify(title)

    setSaveStatus('saving')

    if (newSlug !== currentSlug) {
      // Title changed enough to alter the slug — rename instead of a plain save
      try {
        await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-rename/${currentSlug}`, tok, {
          method: 'POST',
          body: JSON.stringify({ newSlug, title, content, icon: pageIconRef.current }),
        })
        // Update local selection to new slug
        selectedPathRef.current = newSlug
        setSelectedPath(newSlug)
        setSaveStatus('renamed')
        setTimeout(() => setSaveStatus('idle'), 2500)
        void refetchTree()
      } catch (err) {
        editorLogger.error('Page rename failed', { from: currentSlug, to: newSlug, err })
        setSaveStatus('error')
        setTimeout(() => setSaveStatus('idle'), 3000)
      }
      return
    }

    // Normal save — slug unchanged
    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${currentSlug}`, tok, {
        method: 'PUT',
        body: JSON.stringify({ title, content, icon: pageIconRef.current }),
      })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
      void refetchTree()
    } catch (err) {
      editorLogger.error('Page save failed', { slug: currentSlug, err })
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setSaveStatus is a wrapper around setUiState (stable setter); adding it recreates this callback every render
  }, [orgSlug, kbSlug, refetchTree])

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(doSave, 1500)
  }, [doSave])

  const saveAccess = useCallback(async () => {
    if (!selectedPath) return
    const content = editorRef.current?.getMarkdown() ?? ''
    const editAccess = accessMode === 'org' ? 'org' : accessUsers
    setAccessSaveStatus('saving')
    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${selectedPath}`, token, {
        method: 'PUT',
        body: JSON.stringify({ title: editTitleRef.current, content, icon: pageIconRef.current, edit_access: editAccess }),
      })
      setAccessSaveStatus('saved')
      setTimeout(() => setAccessSaveStatus('idle'), 2000)
    } catch (err) {
      editorLogger.error('Access control save failed', { path: selectedPath, err })
      setAccessSaveStatus('error')
      setTimeout(() => setAccessSaveStatus('idle'), 3000)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setAccessSaveStatus is a wrapper around setAccessState (stable setter); adding it recreates this callback every render
  }, [selectedPath, orgSlug, kbSlug, token, accessMode, accessUsers])

  // Called by NavTree when the user reorders items via drag-and-drop.
  // newTree is the full updated tree after the operation.
  const handleSidebarUpdate = useCallback(async (newTree: NavNode[]) => {
    const previousTree = localTree ?? tree
    // Optimistic update
    setLocalTree(newTree)

    const pages = navToSidebarEntries(newTree)
    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/sidebar`, token, {
        method: 'PUT',
        body: JSON.stringify({ pages }),
      })
      // Clear optimistic state — next refetch gives fresh data
      await refetchTree()
    } catch (err) {
      treeLogger.error('Sidebar reorder failed, reverting', err)
      setLocalTree(previousTree)
    }
  }, [orgSlug, kbSlug, token, localTree, tree, refetchTree])

  // Opens the new-page input anchored under a specific parent
  const handleAddSubpage = useCallback((parentPath: string) => {
    setNewPageParent(parentPath)
    setNewPageTitle('')
    setShowNewPage(true)
  }, [])

  return (
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
          const newPath = stripMdExt(node.path)
          if (newPath === selectedPath) return
          if (saveTimerRef.current) {
            clearTimeout(saveTimerRef.current)
            saveTimerRef.current = null
            await doSave()
          }
          setSelectedPath(newPath)
          setEditContent('')
          setEditorKey((k) => k + 1)
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

      {/* Editor pane */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {selectedPath ? (
          <>
            <EditorHeader
              pageIcon={pageIcon}
              editTitle={editTitle}
              saveStatus={saveStatus}
              onIconChange={(icon) => { setPageIcon(icon) }}
              onTitleChange={setEditTitle}
              onScheduleSave={scheduleSave}
              onToggleAccessPanel={() => setShowAccessPanel((v) => !v)}
              onDeletePage={() => { if (selectedPath) setDeletePagePath(selectedPath) }}
            />
            {showAccessPanel && (
              <AccessControlPanel
                accessMode={accessMode}
                accessUsers={accessUsers}
                newUserId={newUserId}
                accessSaveStatus={accessSaveStatus}
                onClose={() => setShowAccessPanel(false)}
                onAccessModeChange={setAccessMode}
                onNewUserIdChange={setNewUserId}
                onAddUser={(uid) => setAccessUsers((prev) => prev.includes(uid) ? prev : [...prev, uid])}
                onRemoveUser={(uid) => setAccessUsers((prev) => prev.filter((u) => u !== uid))}
                onSave={saveAccess}
              />
            )}
            <div className="flex-1 overflow-y-auto">
              <BlockPageEditor
                key={editorKey}
                ref={editorRef}
                initialContent={editContent}
                onChange={scheduleSave}
                pageIndex={pageIndex}
                kbSlug={kbSlug}
                currentPageSlug={selectedPath ?? ''}
                onNavigateToPage={(slug) => {
                  if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
                  setSelectedPath(slug)
                  setEditContent('')
                  setEditorKey((k) => k + 1)
                }}
                onRequestWikilinkPicker={() => setShowWikilinkPicker(true)}
              />
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-sm text-[var(--color-muted-foreground)]">
            {m.docs_editor_select_page()}
          </div>
        )}
      </main>
      {/* Wikilink picker modal — opened from slash menu "Koppelen aan pagina" */}
      {showWikilinkPicker && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/35"
          onClick={() => { setShowWikilinkPicker(false); setWikilinkSearch('') }}
          onKeyDown={(e) => { if (e.key === 'Escape') { setShowWikilinkPicker(false); setWikilinkSearch('') } }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label={m.docs_wikilink_connect()}
            className="w-[360px] max-w-[90vw] rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <p className="mb-2 text-xs text-[var(--color-muted-foreground)]">
              {m.docs_wikilink_connect()}
            </p>
            <Input
              autoFocus
              placeholder={m.docs_wikilink_search_placeholder()}
              value={wikilinkSearch}
              onChange={(e) => setWikilinkSearch(e.target.value)}
              className="mb-2"
            />
            <div className="max-h-[280px] overflow-y-auto">
              {pageIndex
                .filter((p) => p.slug !== selectedPath)
                .filter((p) =>
                  !wikilinkSearch ||
                  p.title.toLowerCase().includes(wikilinkSearch.toLowerCase()) ||
                  p.slug.includes(wikilinkSearch.toLowerCase())
                )
                .slice(0, 15)
                .map((p) => (
                  <button
                    key={p.slug}
                    className="w-full text-left px-3 py-2 text-sm rounded flex items-center gap-2 hover:bg-[var(--color-secondary)] text-[var(--color-foreground)]"
                    onClick={() => {
                      editorRef.current?.insertWikilink(p.id ?? p.slug, p.title, p.icon)
                      setShowWikilinkPicker(false)
                      setWikilinkSearch('')
                    }}
                  >
                    <span>{p.icon ?? '📄'}</span>
                    {p.title}
                  </button>
                ))}
              {pageIndex.filter((p) => p.slug !== selectedPath).length === 0 && (
                <p className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">{m.docs_wikilink_no_results()}</p>
              )}
            </div>
          </div>
        </div>
      )}
      <DeletePageModal
        open={!!deletePagePath}
        onOpenChange={(open) => { if (!open) setDeletePagePath(null) }}
        pageTitle={pageIndex.find((p) => p.slug === deletePagePath)?.title ?? deletePagePath ?? ''}
        onConfirm={() => { if (deletePagePath) deleteMutation.mutate(deletePagePath) }}
        isPending={deleteMutation.isPending}
      />
    </div>
  )
}
