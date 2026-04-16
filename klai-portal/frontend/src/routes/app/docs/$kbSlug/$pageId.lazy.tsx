import { createLazyFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState, useEffect, useRef, useCallback } from 'react'
import * as m from '@/paraglide/messages'
import { Input } from '@/components/ui/input'
import { DOCS_BASE, stripMdExt, slugify } from '@/lib/kb-editor/tree-utils'
import { useKBEditor, resolveSlug, shortId, PageNotInIndexError } from '@/lib/kb-editor/KBEditorContext'
import { DEFAULT_ICON } from '@/lib/kb-editor/tree-utils'
import { apiFetch } from '@/lib/apiFetch'
import { editorLogger } from '@/lib/logger'
import { BlockPageEditor } from '@/components/kb-editor/BlockPageEditor'
import type { BlockPageEditorHandle } from '@/components/kb-editor/BlockPageEditor'
import { AccessControlPanel } from '@/components/kb-editor/AccessControlPanel'
import { EditorHeader } from '@/components/kb-editor/EditorHeader'

export const Route = createLazyFileRoute('/app/docs/$kbSlug/$pageId')({
  component: KBPageEditor,
})

interface PageData {
  frontmatter: { title?: string; description?: string; edit_access?: 'org' | string[]; icon?: string }
  content: string
}

function KBPageEditor() {
  const { kbSlug, pageId } = Route.useParams()
  const ctx = useKBEditor()
  const { orgSlug, token, pageIndex, refetchTree, refetchPageIndex, doSaveRef, setSaveStatus, setEditTitle, navigateToPage } = ctx

  // REQ-STA-04: Strict slug resolution — throws PageNotInIndexError when not found
  let selectedPath: string | null = null
  let pageNotFound = false

  if (pageIndex.length === 0) {
    // pageIndex not yet loaded — use pageId as temporary path to avoid false 404
    selectedPath = pageId
  } else {
    try {
      selectedPath = resolveSlug(pageId, pageIndex)
    } catch (err) {
      if (err instanceof PageNotInIndexError) {
        pageNotFound = true
      }
    }
  }

  const [currentPage, setCurrentPage] = useState<{
    title: string
    content: string
    icon: string
    editorKey: number
  }>({ title: '', content: '', icon: DEFAULT_ICON, editorKey: 0 })

  const [accessState, setAccessState] = useState<{
    show: boolean
    mode: 'org' | 'specific'
    users: string[]
    newUserId: string
    saveStatus: 'idle' | 'saving' | 'saved' | 'error'
  }>({ show: false, mode: 'org', users: [], newUserId: '', saveStatus: 'idle' })

  const [showWikilinkPicker, setShowWikilinkPicker] = useState(false)
  const [wikilinkSearch, setWikilinkSearch] = useState('')

  const editorRef = useRef<BlockPageEditorHandle>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Stable refs for async save
  const tokenRef = useRef(token)
  const selectedPathRef = useRef(selectedPath)
  const titleRef = useRef(currentPage.title)
  const iconRef = useRef(currentPage.icon)
  tokenRef.current = token
  selectedPathRef.current = selectedPath
  titleRef.current = currentPage.title
  iconRef.current = currentPage.icon

  // Keep layout's editTitle in sync (for sidebar save indicator)
  useEffect(() => { setEditTitle(currentPage.title) }, [currentPage.title, setEditTitle])

  // Load page data
  const { data: page } = useQuery<PageData>({
    queryKey: ['docs-page', orgSlug, kbSlug, selectedPath],
    queryFn: async () => apiFetch<PageData>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${selectedPath}`, token),
    enabled: !!token && !!selectedPath && !pageNotFound,
  })

  // Apply loaded page to editor
  useEffect(() => {
    if (!page || !selectedPathRef.current) return
    const ea = page.frontmatter.edit_access
    setCurrentPage((p) => ({
      ...p,
      title: page.frontmatter.title ?? '',
      content: page.content,
      icon: page.frontmatter.icon ?? DEFAULT_ICON,
      editorKey: p.editorKey + 1,
    }))
    setSaveStatus('idle')
    setAccessState({
      show: false,
      mode: !ea || ea === 'org' ? 'org' : 'specific',
      users: !ea || ea === 'org' ? [] : Array.isArray(ea) ? ea : [ea],
      newUserId: '',
      saveStatus: 'idle',
    })
  }, [page, setSaveStatus])

  const doSave = useCallback(async () => {
    const path = selectedPathRef.current
    const title = titleRef.current
    const tok = tokenRef.current
    const icon = iconRef.current
    if (!path || !tok) return
    const content = editorRef.current?.getContent() ?? ''

    const currentSlug = stripMdExt(path)
    const newSlug = slugify(title)

    setSaveStatus('saving')

    if (newSlug !== currentSlug) {
      try {
        await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-rename/${currentSlug}`, tok, {
          method: 'POST',
          body: JSON.stringify({ newSlug, title, content, icon }),
        })
        selectedPathRef.current = newSlug
        setSaveStatus('renamed')
        setTimeout(() => setSaveStatus('idle'), 2500)
        void refetchTree()
        // REQ-EVT-03: Refresh pageIndex after rename, then navigate by UUID
        const refreshed = await refetchPageIndex()
        const freshIndex = refreshed.data ?? pageIndex
        const entry = freshIndex.find((p) => p.slug === newSlug)
        const targetId = shortId(entry)
        navigateToPage(targetId || newSlug)
      } catch (err) {
        editorLogger.error('Page rename failed', { from: currentSlug, to: newSlug, err })
        setSaveStatus('error')
        setTimeout(() => setSaveStatus('idle'), 3000)
        throw err // propagate so callers know save failed
      }
      return
    }

    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${currentSlug}`, tok, {
        method: 'PUT',
        body: JSON.stringify({ title, content, icon }),
      })
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
      void refetchTree()
      void refetchPageIndex()
    } catch (err) {
      editorLogger.error('Page save failed', { slug: currentSlug, err })
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
      throw err // propagate so callers know save failed
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgSlug, kbSlug, refetchTree, refetchPageIndex, navigateToPage, pageIndex])

  // saveNow clears pending timer and saves immediately — used by layout before page switch
  const saveNow = useCallback(async () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    await doSave()
  }, [doSave])

  // Register saveNow with layout so sidebar onSelect can call it
  useEffect(() => {
    doSaveRef.current = saveNow
    return () => { doSaveRef.current = null }
  }, [doSaveRef, saveNow])

  // Flush pending save on full-page navigation (address bar, browser back/forward).
  // fetch with keepalive:true continues after the page unloads and supports auth headers.
  // Sidebar navigation is handled by doSaveRef (called in route.tsx onSelect).
  useEffect(() => {
    const handleBeforeUnload = () => {
      if (!saveTimerRef.current) return
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
      const path = selectedPathRef.current
      const tok = tokenRef.current
      const content = editorRef.current?.getContent() ?? ''
      if (!path || !tok) return
      const slug = stripMdExt(path)
      fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${slug}`, {
        method: 'PUT',
        headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: titleRef.current, content, icon: iconRef.current }),
        keepalive: true,
      }).catch(() => { /* fire-and-forget on unload */ })
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [orgSlug, kbSlug])

  const scheduleSave = useCallback(() => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(doSave, 1500)
  }, [doSave])

  const saveAccess = useCallback(async () => {
    if (!selectedPath) return
    const content = editorRef.current?.getContent() ?? ''
    const editAccess = accessState.mode === 'org' ? 'org' : accessState.users
    setAccessState((a) => ({ ...a, saveStatus: 'saving' }))
    try {
      await apiFetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${selectedPath}`, token, {
        method: 'PUT',
        body: JSON.stringify({ title: titleRef.current, content, icon: iconRef.current, edit_access: editAccess }),
      })
      setAccessState((a) => ({ ...a, saveStatus: 'saved' }))
      setTimeout(() => setAccessState((a) => ({ ...a, saveStatus: 'idle' })), 2000)
    } catch (err) {
      editorLogger.error('Access control save failed', { path: selectedPath, err })
      setAccessState((a) => ({ ...a, saveStatus: 'error' }))
      setTimeout(() => setAccessState((a) => ({ ...a, saveStatus: 'idle' })), 3000)
    }
  }, [selectedPath, orgSlug, kbSlug, token, accessState.mode, accessState.users])

  // Update URL to UUID once pageIndex has the UUID for this page (handles slug-based entry)
  useEffect(() => {
    if (!pageIndex.length || !selectedPath) return
    const entry = pageIndex.find((p) => p.slug === selectedPath)
    const pid = shortId(entry)
    // Only redirect if we currently have a non-UUID pageId in the URL
    if (pid && pid !== pageId) {
      navigateToPage(pid)
    }
    // Only run when pageIndex loads/updates
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageIndex])

  // REQ-STA-04: Page not found UI — shown when UUID/slug is not in pageIndex
  if (pageNotFound) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4 text-[var(--color-muted-foreground)]">
        <p className="text-lg font-medium">{m.docs_page_not_found()}</p>
        <p className="text-sm">{m.docs_page_not_found_desc()}</p>
        <button
          className="text-sm text-[var(--color-rl-accent-dark)] underline hover:opacity-80"
          onClick={() => navigateToPage(null)}
        >
          {m.docs_back_to_kb()}
        </button>
      </div>
    )
  }

  return (
    <>
      <EditorHeader
        pageIcon={currentPage.icon}
        editTitle={currentPage.title}
        saveStatus={ctx.saveStatus}
        onIconChange={(icon) => setCurrentPage((p) => ({ ...p, icon }))}
        onTitleChange={(title) => setCurrentPage((p) => ({ ...p, title }))}
        onScheduleSave={scheduleSave}
        onToggleAccessPanel={() => setAccessState((a) => ({ ...a, show: !a.show }))}
        onDeletePage={() => ctx.setDeletePagePath(selectedPath)}
      />

      {accessState.show && (
        <AccessControlPanel
          accessMode={accessState.mode}
          accessUsers={accessState.users}
          newUserId={accessState.newUserId}
          accessSaveStatus={accessState.saveStatus}
          onClose={() => setAccessState((a) => ({ ...a, show: false }))}
          onAccessModeChange={(mode) => setAccessState((a) => ({ ...a, mode }))}
          onNewUserIdChange={(v) => setAccessState((a) => ({ ...a, newUserId: v }))}
          onAddUser={(uid) => setAccessState((a) => ({ ...a, users: a.users.includes(uid) ? a.users : [...a.users, uid] }))}
          onRemoveUser={(uid) => setAccessState((a) => ({ ...a, users: a.users.filter((u) => u !== uid) }))}
          onSave={saveAccess}
        />
      )}

      <div className="flex-1 overflow-y-auto">
        <BlockPageEditor
          key={currentPage.editorKey}
          ref={editorRef}
          initialContent={currentPage.content}
          onChange={scheduleSave}
          pageIndex={pageIndex}
          kbSlug={kbSlug}
          currentPageSlug={selectedPath ?? ''}
          onNavigateToPage={(slug) => {
            if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
            // Resolve slug to UUID for navigation
            const entry = pageIndex.find((p) => p.slug === slug)
            const targetId = shortId(entry)
            navigateToPage(targetId || slug)
          }}
          onRequestWikilinkPicker={() => setShowWikilinkPicker(true)}
        />
      </div>

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
            <p className="mb-2 text-xs text-[var(--color-muted-foreground)]">{m.docs_wikilink_connect()}</p>
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
    </>
  )
}
