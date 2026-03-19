// @ts-ignore
import Picker from '@emoji-mart/react'
// @ts-ignore
import data from '@emoji-mart/data'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from 'react'
import { ChevronRight, FolderOpen, ArrowLeft, Upload, Plus, Check, X, MoreHorizontal, Loader2, Users, Lock, GripVertical } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCreateBlockNote } from '@blocknote/react'
import { BlockNoteView } from '@blocknote/mantine'
import '@blocknote/mantine/style.css'
import * as m from '@/paraglide/messages'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
} from '@dnd-kit/core'
import type { DragEndEvent, DragStartEvent, DragOverEvent } from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

export const Route = createFileRoute('/app/docs/$kbSlug')({
  component: KBEditorPage,
})

const DOCS_BASE = '/docs/api'

function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

interface NavNode {
  slug: string
  title: string
  icon?: string
  path: string
  type: 'file' | 'dir'
  children?: NavNode[]
}

interface SidebarEntry {
  slug: string
  children?: SidebarEntry[]
}

interface PageData {
  frontmatter: { title?: string; description?: string; edit_access?: 'org' | string[]; icon?: string }
  content: string
}

const DEFAULT_ICON = '📄'


// Convert NavNode[] to SidebarEntry[] (strip everything except slug + children)
function navToSidebarEntries(nodes: NavNode[]): SidebarEntry[] {
  return nodes.map((n) => ({
    slug: n.path.replace(/\.md$/, ''),
    ...(n.children?.length ? { children: navToSidebarEntries(n.children) } : {}),
  }))
}

// Apply a reorder (same level) within the full tree, returning a new tree
function applyReorder(
  nodes: NavNode[],
  parentPath: string | null,
  oldIndex: number,
  newIndex: number,
): NavNode[] {
  if (parentPath === null) {
    // Root level
    return arrayMove(nodes, oldIndex, newIndex)
  }
  return nodes.map((n) => {
    if (n.path === parentPath) {
      return { ...n, children: arrayMove(n.children ?? [], oldIndex, newIndex) }
    }
    if (n.children?.length) {
      return { ...n, children: applyReorder(n.children, parentPath, oldIndex, newIndex) }
    }
    return n
  })
}

// Add a new child slug under the node matching parentPath
function addChildToNode(nodes: NavNode[], parentPath: string, newSlug: string): NavNode[] {
  return nodes.map((node) => {
    if (node.path.replace(/\.md$/, '') === parentPath) {
      return {
        ...node,
        children: [
          ...(node.children ?? []),
          { slug: newSlug, title: newSlug, path: `${newSlug}.md`, type: 'file' as const, icon: DEFAULT_ICON },
        ],
      }
    }
    if (node.children) {
      return { ...node, children: addChildToNode(node.children, parentPath, newSlug) }
    }
    return node
  })
}

function KBEditorPage() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const orgSlug = getOrgSlug()

  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const [pageIcon, setPageIcon] = useState(DEFAULT_ICON)
  const [showIconPicker, setShowIconPicker] = useState(false)
  const [editorKey, setEditorKey] = useState(0)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  const [newPageTitle, setNewPageTitle] = useState('')
  const [showNewPage, setShowNewPage] = useState(false)
  // parentPath for the pending new-page input (null = root level)
  const [newPageParent, setNewPageParent] = useState<string | null>(null)
  const [showMenu, setShowMenu] = useState(false)
  const [showAccessPanel, setShowAccessPanel] = useState(false)
  const [accessMode, setAccessMode] = useState<'org' | 'specific'>('org')
  const [accessUsers, setAccessUsers] = useState<string[]>([])
  const [newUserId, setNewUserId] = useState('')
  const [accessSaveStatus, setAccessSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  // Optimistic local tree (null = use server tree)
  const [localTree, setLocalTree] = useState<NavNode[] | null>(null)

  const editorRef = useRef<{ getMarkdown: () => string }>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const iconPickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!showMenu) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showMenu])

  useEffect(() => {
    if (!showIconPicker) return
    const handler = (e: MouseEvent) => {
      if (iconPickerRef.current && !iconPickerRef.current.contains(e.target as Node)) {
        setShowIconPicker(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showIconPicker])

  // Stable refs for save function
  const tokenRef = useRef(token)
  const selectedPathRef = useRef(selectedPath)
  const editTitleRef = useRef(editTitle)
  const pageIconRef = useRef(pageIcon)
  tokenRef.current = token
  selectedPathRef.current = selectedPath
  editTitleRef.current = editTitle
  pageIconRef.current = pageIcon

  const authHeader = `Bearer ${token}`

  const { data: tree = [], refetch: refetchTree } = useQuery<NavNode[]>({
    queryKey: ['docs-tree', orgSlug, kbSlug],
    queryFn: async () => {
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/tree`, {
        headers: { Authorization: authHeader },
      })
      if (!res.ok) throw new Error('Failed')
      return res.json()
    },
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
    queryFn: async () => {
      const res = await fetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${selectedPath}`,
        { headers: { Authorization: authHeader } }
      )
      if (!res.ok) throw new Error('Failed')
      return res.json()
    },
    enabled: !!token && !!selectedPath,
  })

  useEffect(() => {
    if (page) {
      setEditTitle(page.frontmatter.title ?? '')
      setEditContent(page.content)
      setPageIcon(page.frontmatter.icon ?? DEFAULT_ICON)
      setSaveStatus('idle')
      setEditorKey((k) => k + 1)
      const ea = page.frontmatter.edit_access
      if (!ea || ea === 'org') {
        setAccessMode('org')
        setAccessUsers([])
      } else {
        setAccessMode('specific')
        setAccessUsers(Array.isArray(ea) ? ea : [ea])
      }
      setShowAccessPanel(false)
      setAccessSaveStatus('idle')
    }
  }, [page])

  // parentPath: null = root level, string = path of the parent node (without .md)
  const handleNewPage = async (parentPath: string | null = null) => {
    if (!newPageTitle.trim()) return
    const slug = newPageTitle
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .trim()
      .replace(/\s+/g, '-') || 'untitled'
    const path = slug
    try {
      const res = await fetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${path}`,
        {
          method: 'PUT',
          headers: { Authorization: authHeader, 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: newPageTitle, content: '' }),
        }
      )
      if (!res.ok) throw new Error('Aanmaken mislukt')
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)

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
    } catch {
      // silently fail; tree refetch will show state
    }
  }

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/upload`, {
        method: 'POST',
        headers: { Authorization: authHeader },
        body: form,
      })
      if (!res.ok) throw new Error('Upload failed')
      return res.json()
    },
    onSuccess: () => refetchTree(),
  })

  const doSave = useCallback(async () => {
    const path = selectedPathRef.current
    const title = editTitleRef.current
    const tok = tokenRef.current
    if (!path || !tok) return
    const content = editorRef.current?.getMarkdown() ?? ''
    setSaveStatus('saving')
    try {
      const res = await fetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${path}`,
        {
          method: 'PUT',
          headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ title, content, icon: pageIconRef.current }),
        }
      )
      if (!res.ok) throw new Error('Save failed')
      setSaveStatus('saved')
      setTimeout(() => setSaveStatus('idle'), 2000)
      refetchTree()
    } catch {
      setSaveStatus('error')
      setTimeout(() => setSaveStatus('idle'), 3000)
    }
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
      const res = await fetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${selectedPath}`,
        {
          method: 'PUT',
          headers: { Authorization: authHeader, 'Content-Type': 'application/json' },
          body: JSON.stringify({ title: editTitleRef.current, content, icon: pageIconRef.current, edit_access: editAccess }),
        }
      )
      if (!res.ok) throw new Error('Save failed')
      setAccessSaveStatus('saved')
      setTimeout(() => setAccessSaveStatus('idle'), 2000)
    } catch {
      setAccessSaveStatus('error')
      setTimeout(() => setAccessSaveStatus('idle'), 3000)
    }
  }, [selectedPath, orgSlug, kbSlug, authHeader, accessMode, accessUsers])

  // Called by NavTree when the user reorders items via drag-and-drop.
  // newTree is the full updated tree after the operation.
  const handleSidebarUpdate = useCallback(async (newTree: NavNode[]) => {
    const previousTree = localTree ?? tree
    // Optimistic update
    setLocalTree(newTree)

    const pages = navToSidebarEntries(newTree)
    try {
      const res = await fetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/sidebar`,
        {
          method: 'PUT',
          headers: { Authorization: authHeader, 'Content-Type': 'application/json' },
          body: JSON.stringify({ pages }),
        }
      )
      if (!res.ok) throw new Error('Sidebar update failed')
      // Clear optimistic state — next refetch gives fresh data
      await refetchTree()
    } catch {
      // Revert on failure
      setLocalTree(previousTree)
    }
  }, [orgSlug, kbSlug, authHeader, localTree, tree, refetchTree])

  // Opens the new-page input anchored under a specific parent
  const handleAddSubpage = useCallback((parentPath: string) => {
    setNewPageParent(parentPath)
    setNewPageTitle('')
    setShowNewPage(true)
  }, [])

  return (
    <div className="flex h-full">
      {/* Sidebar: nav tree */}
      <aside className="w-56 shrink-0 border-r border-[var(--color-border)] bg-[var(--color-muted)] flex flex-col">
        <div className="px-3 py-3 border-b border-[var(--color-border)]">
          <Link
            to="/app/docs"
            className="flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
          >
            <ArrowLeft size={12} />
            {m.docs_editor_back()}
          </Link>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {displayTree.length === 0 ? (
            <p className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">
              {m.docs_pages_empty()}
            </p>
          ) : (
            <NavTree
              nodes={displayTree}
              selectedPath={selectedPath}
              onSelect={(node) => {
                if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
                setSelectedPath(node.path.replace(/\.md$/, ''))
                setEditContent('')
                setEditorKey((k) => k + 1)
              }}
              activeTitle={editTitle}
              activePath={selectedPath}
              fullTree={displayTree}
              onSidebarUpdate={handleSidebarUpdate}
              onAddSubpage={handleAddSubpage}
              addingSubpageUnder={showNewPage ? newPageParent : null}
              newPageTitle={newPageTitle}
              onNewPageTitleChange={setNewPageTitle}
              onNewPageConfirm={handleNewPage}
              onNewPageCancel={() => { setShowNewPage(false); setNewPageTitle(''); setNewPageParent(null) }}
            />
          )}
        </div>
        <div className="px-3 py-3 border-t border-[var(--color-border)] space-y-2">
          {showNewPage && newPageParent === null ? (
            <div className="space-y-1.5">
              <Input
                value={newPageTitle}
                onChange={(e) => setNewPageTitle(e.target.value)}
                placeholder={m.docs_editor_title_placeholder()}
                className="h-7 text-xs"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleNewPage(null)
                  if (e.key === 'Escape') { setShowNewPage(false); setNewPageTitle('') }
                }}
              />
              <div className="flex gap-1">
                <Button
                  size="sm"
                  className="flex-1 h-6 text-xs"
                  onClick={() => handleNewPage(null)}
                  disabled={!newPageTitle.trim() || saveStatus === 'saving'}
                >
                  <Check size={11} className="mr-1" />
                  {m.docs_kb_create()}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2"
                  onClick={() => { setShowNewPage(false); setNewPageTitle('') }}
                >
                  <X size={11} />
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={() => { setNewPageParent(null); setShowNewPage(true) }}
            >
              <Plus size={12} className="mr-1.5" />
              {m.docs_pages_new()}
            </Button>
          )}
          <label className="w-full block">
            <input
              type="file"
              accept=".md"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) uploadMutation.mutate(file)
                e.target.value = ''
              }}
            />
            <Button variant="outline" size="sm" className="w-full cursor-pointer">
              <Upload size={12} className="mr-1.5" />
              {m.docs_pages_upload()}
            </Button>
          </label>
        </div>
      </aside>

      {/* Editor pane */}
      <main className="flex-1 flex flex-col overflow-hidden">
        {selectedPath ? (
          <>
            <div className="flex items-center gap-3 px-5 py-3 border-b border-[var(--color-border)]">
              {/* Emoji icon zone */}
              <div className="relative shrink-0" ref={iconPickerRef}>
                <button
                  className="flex items-center justify-center w-8 h-8 rounded hover:bg-[var(--color-muted-foreground)]/10 text-xl leading-none transition-colors"
                  onClick={() => setShowIconPicker((v) => !v)}
                  title="Pictogram kiezen"
                  type="button"
                >
                  {pageIcon}
                </button>
                {showIconPicker && (
                  <div
                    style={{
                      position: 'absolute',
                      top: '100%',
                      left: 0,
                      zIndex: 50,
                    }}
                  >
                    <Picker
                      data={data}
                      onEmojiSelect={(emoji: { native: string }) => {
                        setPageIcon(emoji.native)
                        scheduleSave()
                        setShowIconPicker(false)
                      }}
                      theme="light"
                      locale="nl"
                      previewPosition="none"
                      skinTonePosition="none"
                    />
                  </div>
                )}
              </div>
              <Input
                value={editTitle}
                onChange={(e) => {
                  setEditTitle(e.target.value)
                  scheduleSave()
                }}
                placeholder={m.docs_editor_title_placeholder()}
                className="flex-1 text-base font-medium border-none shadow-none focus-visible:ring-0 p-0 h-auto"
              />
              <div className="flex items-center gap-2 shrink-0">
                {saveStatus === 'saving' && (
                  <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                    <Loader2 size={12} className="animate-spin" />
                    {m.docs_editor_saving()}
                  </span>
                )}
                {saveStatus === 'saved' && (
                  <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                    <Check size={12} />
                    {m.docs_editor_save()}
                  </span>
                )}
                {saveStatus === 'error' && (
                  <span className="text-xs text-[var(--color-destructive)]">Opslaan mislukt</span>
                )}
                <div className="relative" ref={menuRef}>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 w-7 p-0"
                    onClick={() => setShowMenu((v) => !v)}
                  >
                    <MoreHorizontal size={15} />
                  </Button>
                  {showMenu && (
                    <div className="absolute right-0 top-8 z-10 w-48 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md py-1">
                      <p className="px-3 py-1.5 text-xs text-[var(--color-muted-foreground)]">
                        Paginainstellingen
                      </p>
                      <button
                        className="w-full text-left px-3 py-1.5 text-sm text-[var(--color-purple-deep)] hover:bg-[var(--color-secondary)]"
                        onClick={() => { setShowMenu(false); setShowAccessPanel((v) => !v) }}
                      >
                        {m.docs_access_panel_title()}…
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>
            {showAccessPanel && (
              <div className="border-b border-[var(--color-border)] bg-[var(--color-muted)] px-5 py-4">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <Lock size={14} className="text-[var(--color-purple-deep)]" />
                    <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                      {m.docs_access_panel_title()}
                    </span>
                  </div>
                  <button
                    className="text-xs text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                    onClick={() => setShowAccessPanel(false)}
                  >
                    {m.docs_access_close()}
                  </button>
                </div>
                <div className="space-y-3 max-w-sm">
                  <div className="space-y-1.5">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="access-mode"
                        value="org"
                        checked={accessMode === 'org'}
                        onChange={() => setAccessMode('org')}
                        className="accent-[var(--color-accent)]"
                      />
                      <span className="text-sm text-[var(--color-purple-deep)]">
                        <Users size={13} className="inline mr-1" />
                        {m.docs_access_everyone()}
                      </span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="access-mode"
                        value="specific"
                        checked={accessMode === 'specific'}
                        onChange={() => setAccessMode('specific')}
                        className="accent-[var(--color-accent)]"
                      />
                      <span className="text-sm text-[var(--color-purple-deep)]">
                        <Lock size={13} className="inline mr-1" />
                        {m.docs_access_specific()}
                      </span>
                    </label>
                  </div>
                  {accessMode === 'specific' && (
                    <div className="space-y-2">
                      <div className="flex gap-2">
                        <Input
                          id="docs-access-add-user"
                          value={newUserId}
                          onChange={(e) => setNewUserId(e.target.value)}
                          placeholder={m.docs_access_add_placeholder()}
                          className="h-7 text-xs"
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && newUserId.trim()) {
                              setAccessUsers((prev) => prev.includes(newUserId.trim()) ? prev : [...prev, newUserId.trim()])
                              setNewUserId('')
                            }
                          }}
                        />
                        <Button
                          size="sm"
                          className="h-7 px-2 text-xs shrink-0"
                          disabled={!newUserId.trim()}
                          onClick={() => {
                            if (!newUserId.trim()) return
                            setAccessUsers((prev) => prev.includes(newUserId.trim()) ? prev : [...prev, newUserId.trim()])
                            setNewUserId('')
                          }}
                        >
                          {m.docs_access_add_button()}
                        </Button>
                      </div>
                      {accessUsers.length > 0 && (
                        <ul className="space-y-1">
                          {accessUsers.map((uid) => (
                            <li key={uid} className="flex items-center justify-between rounded bg-[var(--color-card)] border border-[var(--color-border)] px-2 py-1">
                              <span className="text-xs text-[var(--color-purple-deep)] truncate">{uid}</span>
                              <button
                                className="ml-2 text-xs text-[var(--color-destructive)] hover:opacity-70 shrink-0"
                                onClick={() => setAccessUsers((prev) => prev.filter((u) => u !== uid))}
                                aria-label={m.docs_access_remove()}
                              >
                                <X size={12} />
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                  <div className="flex items-center gap-3 pt-1">
                    <Button
                      size="sm"
                      className="h-7 text-xs"
                      disabled={accessSaveStatus === 'saving'}
                      onClick={saveAccess}
                    >
                      {accessSaveStatus === 'saving' ? (
                        <><Loader2 size={11} className="mr-1 animate-spin" />{m.docs_access_saving()}</>
                      ) : (
                        m.docs_access_save()
                      )}
                    </Button>
                    {accessSaveStatus === 'saved' && (
                      <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                        <Check size={11} />{m.docs_access_saved()}
                      </span>
                    )}
                    {accessSaveStatus === 'error' && (
                      <span className="text-xs text-[var(--color-destructive)]">{m.docs_access_error_save()}</span>
                    )}
                  </div>
                </div>
              </div>
            )}
            <div className="flex-1 overflow-y-auto">
              <BlockPageEditor
                key={editorKey}
                ref={editorRef}
                initialContent={editContent}
                onChange={scheduleSave}
              />
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-sm text-[var(--color-muted-foreground)]">
            {m.docs_editor_select_page()}
          </div>
        )}
      </main>
    </div>
  )
}

// ─── BlockNote editor ────────────────────────────────────────────────────────

type BlockPageEditorHandle = { getMarkdown: () => string }

const BlockPageEditor = forwardRef<
  BlockPageEditorHandle,
  { initialContent: string; onChange: () => void }
>(({ initialContent, onChange }, ref) => {
  const editor = useCreateBlockNote()

  useEffect(() => {
    if (!initialContent) return
    const blocks = editor.tryParseMarkdownToBlocks(initialContent)
    editor.replaceBlocks(editor.document, blocks)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useImperativeHandle(ref, () => ({
    getMarkdown: () => editor.blocksToMarkdownLossy(editor.document),
  }))

  return (
    <BlockNoteView
      editor={editor}
      theme="light"
      className="min-h-full"
      onChange={onChange}
    />
  )
})
BlockPageEditor.displayName = 'BlockPageEditor'

// ─── Nav tree ─────────────────────────────────────────────────────────────────

interface NavTreeProps {
  nodes: NavNode[]
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  depth?: number
  activeTitle?: string
  activePath?: string | null
  // The full root tree (needed to compute full-tree mutations for sidebar API)
  fullTree: NavNode[]
  onSidebarUpdate: (newTree: NavNode[]) => void
  // Internal: path of the parent node (null = root level)
  parentPath?: string | null
  // Subpage creation
  onAddSubpage: (parentPath: string) => void
  addingSubpageUnder: string | null
  newPageTitle: string
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: (parentPath: string) => void
  onNewPageCancel: () => void
}

function NavTree({
  nodes,
  selectedPath,
  onSelect,
  depth = 0,
  activeTitle,
  activePath,
  fullTree,
  onSidebarUpdate,
  parentPath = null,
  onAddSubpage,
  addingSubpageUnder,
  newPageTitle,
  onNewPageTitleChange,
  onNewPageConfirm,
  onNewPageCancel,
}: NavTreeProps) {
  const [activeNode, setActiveNode] = useState<NavNode | null>(null)
  const [nestTarget, setNestTarget] = useState<string | null>(null)
  const nestTargetRef = useRef<string | null>(null)
  const pointerYRef = useRef(0)

  useEffect(() => {
    const onMove = (e: MouseEvent) => { pointerYRef.current = e.clientY }
    document.addEventListener('mousemove', onMove)
    return () => document.removeEventListener('mousemove', onMove)
  }, [])

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    })
  )

  function handleDragStart(event: DragStartEvent) {
    const node = nodes.find((n) => n.path === event.active.id)
    setActiveNode(node ?? null)
    setNestTarget(null)
    nestTargetRef.current = null
  }

  function handleDragOver(event: DragOverEvent) {
    const { over } = event
    if (!over) {
      nestTargetRef.current = null
      setNestTarget(null)
      return
    }
    const overId = String(over.id)
    // Skip nesting into self
    if (overId === String(event.active.id)) {
      nestTargetRef.current = null
      setNestTarget(null)
      return
    }
    const overEl = document.querySelector(`[data-sortable-path="${overId}"]`)
    if (!overEl) {
      nestTargetRef.current = null
      setNestTarget(null)
      return
    }
    const rect = overEl.getBoundingClientRect()
    const relY = (pointerYRef.current - rect.top) / rect.height
    const isNestZone = relY > 0.3 && relY < 0.7
    const newNestTarget = isNestZone ? overId : null
    nestTargetRef.current = newNestTarget
    setNestTarget(newNestTarget)
  }

  function handleDragEnd(event: DragEndEvent) {
    const currentNestTarget = nestTargetRef.current
    setActiveNode(null)
    setNestTarget(null)
    nestTargetRef.current = null

    const { active, over } = event
    if (!over || active.id === over.id) return

    const activeId = active.id as string
    const overId = over.id as string

    // Nest operation: drop onto the center of a target item
    if (currentNestTarget && currentNestTarget === overId) {
      const activeNodeFull = nodes.find((n) => n.path === activeId)
      if (!activeNodeFull) return
      // Remove from current level
      const withoutActive = nodes.filter((n) => n.path !== activeId)
      // Rebuild full tree without active at current level
      let updatedTree: NavNode[]
      if (parentPath === null) {
        updatedTree = withoutActive
      } else {
        updatedTree = fullTree.map(function patchParent(n: NavNode): NavNode {
          if (n.path === parentPath) {
            return { ...n, children: withoutActive }
          }
          if (n.children?.length) {
            return { ...n, children: n.children.map(patchParent) }
          }
          return n
        })
      }
      // Add as child of overId (strip .md for addChildToNode matching)
      const overPathClean = overId.replace(/\.md$/, '')
      const finalTree = addChildToNode(updatedTree, overPathClean, activeNodeFull.slug)
      onSidebarUpdate(finalTree)
      return
    }

    const oldIndex = nodes.findIndex((n) => n.path === activeId)
    const newIndex = nodes.findIndex((n) => n.path === overId)
    if (oldIndex === -1 || newIndex === -1) return

    // Reorder within the same level
    const newTree = applyReorder(fullTree, parentPath, oldIndex, newIndex)
    onSidebarUpdate(newTree)
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={nodes.map((n) => n.path)}
        strategy={verticalListSortingStrategy}
      >
        {nodes.map((node) => (
          <SortableNavItem
            key={node.path}
            node={node}
            selectedPath={selectedPath}
            onSelect={onSelect}
            depth={depth}
            activeTitle={activeTitle}
            activePath={activePath}
            fullTree={fullTree}
            onSidebarUpdate={onSidebarUpdate}
            onAddSubpage={onAddSubpage}
            addingSubpageUnder={addingSubpageUnder}
            newPageTitle={newPageTitle}
            onNewPageTitleChange={onNewPageTitleChange}
            onNewPageConfirm={onNewPageConfirm}
            onNewPageCancel={onNewPageCancel}
            nestTarget={nestTarget}
          />
        ))}
      </SortableContext>
      <DragOverlay>
        {activeNode && (
          <NavItemOverlay node={activeNode} depth={depth} activeTitle={activeTitle} activePath={activePath} />
        )}
      </DragOverlay>
    </DndContext>
  )
}

interface NavItemProps {
  node: NavNode
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  depth: number
  activeTitle?: string
  activePath?: string | null
  fullTree: NavNode[]
  onSidebarUpdate: (newTree: NavNode[]) => void
  onAddSubpage: (parentPath: string) => void
  addingSubpageUnder: string | null
  newPageTitle: string
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: (parentPath: string) => void
  onNewPageCancel: () => void
  nestTarget?: string | null
}

function SortableNavItem(props: NavItemProps) {
  const {
    node,
    selectedPath,
    onSelect,
    depth,
    activeTitle,
    activePath,
    fullTree,
    onSidebarUpdate,
    onAddSubpage,
    addingSubpageUnder,
    newPageTitle,
    onNewPageTitleChange,
    onNewPageConfirm,
    onNewPageCancel,
    nestTarget,
  } = props

  const isExpandable = !!(node.children && node.children.length > 0)
  const [open, setOpen] = useState(true)
  const [hovered, setHovered] = useState(false)
  const isSelected = selectedPath === node.path.replace(/\.md$/, '')
  const nodePath = node.path.replace(/\.md$/, '')
  const isAddingSubpageHere = addingSubpageUnder === nodePath
  const isNestTarget = nestTarget === node.path

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: node.path })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : 1,
  }

  // Inline sub-page input shown directly below this item when active
  const subpageInput = isAddingSubpageHere && (
    <div className="py-1 pr-2" style={{ paddingLeft: `${20 + depth * 12}px` }}>
      <div className="flex gap-1 items-center">
        <Input
          value={newPageTitle}
          onChange={(e) => onNewPageTitleChange(e.target.value)}
          placeholder={m.docs_editor_title_placeholder()}
          className="h-6 text-xs flex-1"
          autoFocus
          onKeyDown={(e) => {
            if (e.key === 'Enter') onNewPageConfirm(nodePath)
            if (e.key === 'Escape') onNewPageCancel()
          }}
        />
        <button
          type="button"
          className="shrink-0 text-[var(--color-purple-deep)] hover:opacity-70 disabled:opacity-30"
          disabled={!newPageTitle.trim()}
          onClick={() => onNewPageConfirm(nodePath)}
          aria-label={m.docs_kb_create()}
        >
          <Check size={12} />
        </button>
        <button
          type="button"
          className="shrink-0 text-[var(--color-muted-foreground)] hover:opacity-70"
          onClick={onNewPageCancel}
          aria-label="Annuleren"
        >
          <X size={12} />
        </button>
      </div>
    </div>
  )

  if (node.type === 'dir' && !isExpandable) {
    // Non-expandable folder label (empty dir, no children)
    return (
      <div ref={setNodeRef} style={style} data-sortable-path={node.path}>
        <div
          className={`flex w-full items-center${isNestTarget ? ' border-l-2 border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5' : ''}`}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <button
            className="flex flex-1 items-center gap-1.5 py-1 text-xs font-medium text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)] min-w-0"
            style={{ paddingLeft: `${12 + depth * 12}px` }}
            disabled
          >
            <FolderOpen size={13} className="shrink-0" />
            <span className="truncate">{node.title}</span>
          </button>
          <div className="flex items-center gap-0.5 mr-1.5 shrink-0">
            {hovered && (
              <button
                type="button"
                className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                onClick={() => onAddSubpage(nodePath)}
                title={m.docs_pages_add_subpage()}
                aria-label={m.docs_pages_add_subpage()}
              >
                <Plus size={10} />
              </button>
            )}
            <span className="cursor-grab touch-none" {...attributes} {...listeners}>
              <GripVertical size={12} className="text-[var(--color-muted-foreground)] opacity-40 flex-shrink-0" />
            </span>
          </div>
        </div>
        {subpageInput}
      </div>
    )
  }

  if (isExpandable) {
    // Expandable node: could be dir or file with children
    const isDir = node.type === 'dir'

    return (
      <div ref={setNodeRef} style={style} data-sortable-path={node.path}>
        <div
          className={`flex w-full items-center group${isNestTarget ? ' border-l-2 border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5' : ''}`}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
        >
          <button
            className={`flex flex-1 items-center gap-1.5 py-1 text-xs min-w-0 ${
              isDir
                ? 'font-medium text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
                : isSelected
                  ? 'text-[var(--color-purple-deep)] font-medium'
                  : 'text-[var(--color-foreground)] hover:text-[var(--color-foreground)]'
            }`}
            style={{ paddingLeft: `${12 + depth * 12}px` }}
            onClick={() => {
              if (!isDir) onSelect(node)
            }}
          >
            {/* Emoji + chevron overlay — same physical space */}
            <div className="relative w-5 h-5 shrink-0 flex items-center justify-center">
              {/* Emoji (or folder icon for dirs): always visible, hidden on hover */}
              {isDir
                ? <FolderOpen size={13} className="shrink-0 transition-opacity group-hover:opacity-0" />
                : <span className="text-sm leading-none transition-opacity group-hover:opacity-0 select-none">{node.icon ?? DEFAULT_ICON}</span>
              }
              {/* Chevron: only visible on hover, overlaid on top */}
              <button
                className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={(e) => { e.stopPropagation(); setOpen((v) => !v) }}
                tabIndex={-1}
                aria-label={open ? 'Inklappen' : 'Uitklappen'}
                type="button"
              >
                <ChevronRight
                  size={12}
                  className={`text-[var(--color-muted-foreground)] transition-transform ${open ? 'rotate-90' : ''}`}
                />
              </button>
            </div>
            <span className="truncate">
              {!isDir && activePath && activePath === node.path.replace(/\.md$/, '')
                ? (activeTitle ?? node.title)
                : node.title}
            </span>
          </button>
          <div className="flex items-center gap-0.5 mr-1.5 shrink-0">
            {hovered && (
              <button
                type="button"
                className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                onClick={() => { setOpen(true); onAddSubpage(nodePath) }}
                title={m.docs_pages_add_subpage()}
                aria-label={m.docs_pages_add_subpage()}
              >
                <Plus size={10} />
              </button>
            )}
            <span className="cursor-grab touch-none" {...attributes} {...listeners}>
              <GripVertical size={12} className="text-[var(--color-muted-foreground)] opacity-40 flex-shrink-0" />
            </span>
          </div>
        </div>
        {subpageInput}
        {open && node.children && (
          <NavTree
            nodes={node.children}
            selectedPath={selectedPath}
            onSelect={onSelect}
            depth={depth + 1}
            activeTitle={activeTitle}
            activePath={activePath}
            fullTree={fullTree}
            onSidebarUpdate={onSidebarUpdate}
            parentPath={node.path}
            onAddSubpage={onAddSubpage}
            addingSubpageUnder={addingSubpageUnder}
            newPageTitle={newPageTitle}
            onNewPageTitleChange={onNewPageTitleChange}
            onNewPageConfirm={onNewPageConfirm}
            onNewPageCancel={onNewPageCancel}
          />
        )}
      </div>
    )
  }

  // Plain file node (no children)
  const displayTitle =
    activePath && activePath === node.path.replace(/\.md$/, '')
      ? (activeTitle ?? node.title)
      : node.title

  return (
    <div ref={setNodeRef} style={style} data-sortable-path={node.path}>
      <div
        className={`flex w-full items-center py-1 text-xs transition-colors ${
          isSelected
            ? 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-deep)] font-medium'
            : 'text-[var(--color-foreground)] hover:bg-[var(--color-muted-foreground)]/5'
        }${isNestTarget ? ' border-l-2 border-[var(--color-purple-accent)] bg-[var(--color-purple-accent)]/5' : ''}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        <button
          className="flex flex-1 items-center gap-1.5 min-w-0"
          style={{ paddingLeft: `${16 + depth * 12}px` }}
          onClick={() => onSelect(node)}
        >
          <span className="shrink-0 text-sm leading-none">{node.icon ?? DEFAULT_ICON}</span>
          <span className="truncate">{displayTitle}</span>
        </button>
        <div className="flex items-center gap-0.5 mr-1.5 shrink-0">
          {hovered && (
            <button
              type="button"
              className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
              onClick={() => onAddSubpage(nodePath)}
              title={m.docs_pages_add_subpage()}
              aria-label={m.docs_pages_add_subpage()}
            >
              <Plus size={10} />
            </button>
          )}
          <span className="cursor-grab touch-none" {...attributes} {...listeners}>
            <GripVertical size={12} className="text-[var(--color-muted-foreground)] opacity-40 flex-shrink-0" />
          </span>
        </div>
      </div>
      {subpageInput}
    </div>
  )
}

// Overlay shown while dragging
function NavItemOverlay({
  node,
  depth,
  activeTitle,
  activePath,
}: {
  node: NavNode
  depth: number
  activeTitle?: string
  activePath?: string | null
}) {
  const displayTitle =
    activePath && activePath === node.path.replace(/\.md$/, '')
      ? (activeTitle ?? node.title)
      : node.title

  const isDir = node.type === 'dir'

  return (
    <div
      className="flex items-center gap-1.5 rounded text-xs font-medium bg-[var(--color-card)] border border-[var(--color-border)] shadow-md py-1 pr-2 text-[var(--color-purple-deep)]"
      style={{ paddingLeft: `${(isDir ? 12 : 16) + depth * 12}px` }}
    >
      {isDir
        ? <FolderOpen size={13} className="shrink-0" />
        : <span className="shrink-0 text-sm leading-none">{node.icon ?? DEFAULT_ICON}</span>
      }
      <span className="truncate">{displayTitle}</span>
    </div>
  )
}
