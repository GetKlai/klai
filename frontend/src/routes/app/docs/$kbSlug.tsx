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
import { useCreateBlockNote, SuggestionMenuController, getDefaultReactSlashMenuItems } from '@blocknote/react'
import { BlockNoteView } from '@blocknote/mantine'
import { BlockNoteSchema, defaultInlineContentSpecs } from '@blocknote/core'
import '@blocknote/mantine/style.css'
import { WikiLink } from './WikiLink'
import * as m from '@/paraglide/messages'
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  closestCenter,
} from '@dnd-kit/core'
import type { DragEndEvent, DragStartEvent, DragMoveEvent } from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'

export const Route = createFileRoute('/app/docs/$kbSlug')({
  component: KBEditorPage,
})

const DOCS_BASE = '/docs/api'

function getOrgSlug(): string {
  return window.location.hostname.split('.')[0]
}

function slugify(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, '')
      .trim()
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-') || 'untitled'
  )
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

// ─── Flat-list DnD utilities ──────────────────────────────────────────────────

interface FlatNode {
  id: string          // node.path
  depth: number
  parentId: string | null
  node: NavNode
}

function flattenTree(
  nodes: NavNode[],
  collapsed: Set<string>,
  depth = 0,
  parentId: string | null = null,
): FlatNode[] {
  const result: FlatNode[] = []
  for (const node of nodes) {
    result.push({ id: node.path, depth, parentId, node })
    if (node.children?.length && !collapsed.has(node.path)) {
      result.push(...flattenTree(node.children, collapsed, depth + 1, node.path))
    }
  }
  return result
}

interface Projection {
  depth: number
  parentId: string | null
  newIndex: number
}

const INDENT_WIDTH = 12

function getProjection(
  items: FlatNode[],
  activeId: string,
  overId: string,
  deltaX: number,
): Projection {
  const overIndex = items.findIndex((f) => f.id === overId)
  const activeIndex = items.findIndex((f) => f.id === activeId)

  if (overIndex === -1 || activeIndex === -1) {
    const fallback = items.findIndex((f) => f.id === activeId)
    return { depth: 0, parentId: null, newIndex: fallback === -1 ? 0 : fallback }
  }

  const overItem = items[overIndex]
  const baseDepth = overItem.depth

  // Determine the maximum allowable depth (prev item depth + 1)
  // Look backwards from overIndex (skipping the active item itself)
  const itemsWithoutActive = items.filter((f) => f.id !== activeId)
  const overIndexWithoutActive = itemsWithoutActive.findIndex((f) => f.id === overId)
  const prevItem = overIndexWithoutActive > 0 ? itemsWithoutActive[overIndexWithoutActive - 1] : null
  const maxDepth = prevItem ? prevItem.depth + 1 : 0

  const depthOffset = Math.round(deltaX / INDENT_WIDTH)
  const projectedDepth = Math.min(Math.max(0, baseDepth + depthOffset), maxDepth)

  // Find the parentId: look backwards for the first item at projectedDepth - 1
  let parentId: string | null = null
  if (projectedDepth > 0) {
    for (let i = overIndexWithoutActive - 1; i >= 0; i--) {
      if (itemsWithoutActive[i].depth === projectedDepth - 1) {
        parentId = itemsWithoutActive[i].id
        break
      }
      if (itemsWithoutActive[i].depth < projectedDepth - 1) {
        break
      }
    }
  }

  // newIndex: position in the items-without-active list where we're dropping
  const newIndex = overIndexWithoutActive

  return { depth: projectedDepth, parentId, newIndex }
}

function buildTree(flatNodes: FlatNode[], projection: Projection, activeId: string): NavNode[] {
  // Remove active node from flat list
  const withoutActive = flatNodes.filter((f) => f.id !== activeId)
  const activeFlat = flatNodes.find((f) => f.id === activeId)!

  // Insert active at newIndex with updated depth/parentId
  const inserted: FlatNode[] = [
    ...withoutActive.slice(0, projection.newIndex),
    { ...activeFlat, depth: projection.depth, parentId: projection.parentId },
    ...withoutActive.slice(projection.newIndex),
  ]

  // Rebuild hierarchical NavNode[] from the flat list
  // We do this by traversing the flat list and assigning children based on parentId
  const nodeMap = new Map<string, NavNode>()
  const roots: NavNode[] = []

  for (const flat of inserted) {
    // Clone the NavNode without children
    const node: NavNode = { ...flat.node, children: [] }
    nodeMap.set(flat.id, node)

    if (flat.parentId === null) {
      roots.push(node)
    } else {
      const parent = nodeMap.get(flat.parentId)
      if (parent) {
        parent.children = parent.children ?? []
        parent.children.push(node)
      } else {
        // Parent not found (shouldn't happen in a valid tree) — fall to root
        roots.push(node)
      }
    }
  }

  // Strip empty children arrays to keep the data clean
  function stripEmpty(nodes: NavNode[]): NavNode[] {
    return nodes.map((n) => ({
      ...n,
      children: n.children?.length ? stripEmpty(n.children) : undefined,
    }))
  }

  return stripEmpty(roots)
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
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'renamed' | 'error'>('idle')

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

  const { data: pageIndex = [] } = useQuery<Array<{ id: string | null; slug: string; title: string }>>({
    queryKey: ['docs-page-index', orgSlug, kbSlug],
    queryFn: async () => {
      const res = await fetch(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-index`, {
        headers: { Authorization: authHeader },
      })
      if (!res.ok) return []
      return res.json()
    },
    enabled: !!token,
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

    const currentSlug = path.replace(/\.md$/, '')
    const newSlug = slugify(title)

    setSaveStatus('saving')

    if (newSlug !== currentSlug) {
      // Title changed enough to alter the slug — rename instead of a plain save
      try {
        const res = await fetch(
          `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-rename/${currentSlug}`,
          {
            method: 'POST',
            headers: { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ newSlug, title, content, icon: pageIconRef.current }),
          }
        )
        if (!res.ok) throw new Error('Rename failed')
        // Update local selection to new slug
        selectedPathRef.current = newSlug
        setSelectedPath(newSlug)
        setSaveStatus('renamed')
        setTimeout(() => setSaveStatus('idle'), 2500)
        refetchTree()
      } catch {
        setSaveStatus('error')
        setTimeout(() => setSaveStatus('idle'), 3000)
      }
      return
    }

    // Normal save — slug unchanged
    try {
      const res = await fetch(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/pages/${currentSlug}`,
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
                {saveStatus === 'renamed' && (
                  <span className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
                    <Check size={12} />
                    {m.docs_editor_url_updated()}
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
                pageIndex={pageIndex}
                kbSlug={kbSlug}
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

type PageIndexEntry = { id: string | null; slug: string; title: string }

const wikilinkSchema = BlockNoteSchema.create({
  inlineContentSpecs: {
    ...defaultInlineContentSpecs,
    wikilink: WikiLink,
  },
})

const BlockPageEditor = forwardRef<
  BlockPageEditorHandle,
  {
    initialContent: string
    onChange: () => void
    pageIndex?: PageIndexEntry[]
    kbSlug?: string
  }
>(({ initialContent, onChange, pageIndex = [], kbSlug = '' }, ref) => {
  const editor = useCreateBlockNote({ schema: wikilinkSchema })

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
      slashMenu={false}
    >
      <SuggestionMenuController
        triggerCharacter="/"
        getItems={async (query) => {
          const defaultItems = await getDefaultReactSlashMenuItems(editor)
          const wikilinkItem = {
            title: "Link to pagina",
            subtext: "Koppel aan een andere pagina in deze kennisbank",
            icon: <span style={{ fontSize: '1.1em' }}>📄</span>,
            group: "Basisblokken",
            onItemClick: () => {
              editor.insertInlineContent("[[")
            },
          }
          const allItems = [...defaultItems, wikilinkItem]
          return allItems.filter((item) =>
            query === "" || item.title.toLowerCase().includes(query.toLowerCase())
          )
        }}
      />
      <SuggestionMenuController
        triggerCharacter="["
        getItems={async (query) => {
          const search = query.startsWith("[") ? query.slice(1).toLowerCase() : query.toLowerCase()
          const filtered = pageIndex.filter((p) =>
            search === "" ||
            p.title.toLowerCase().includes(search) ||
            p.slug.includes(search)
          )
          return filtered.slice(0, 10).map((p) => ({
            title: p.title,
            onItemClick: () => {
              editor.insertInlineContent([
                {
                  type: "wikilink",
                  props: { pageId: p.id ?? p.slug, title: p.title, kbSlug },
                } as any,
                " ",
              ])
            },
          }))
        }}
      />
    </BlockNoteView>
  )
})
BlockPageEditor.displayName = 'BlockPageEditor'

// ─── Nav tree (flat-list, single DndContext) ───────────────────────────────────

interface NavTreeProps {
  nodes: NavNode[]
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  activeTitle?: string
  activePath?: string | null
  onSidebarUpdate: (newTree: NavNode[]) => void
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
  activeTitle,
  activePath,
  onSidebarUpdate,
  onAddSubpage,
  addingSubpageUnder,
  newPageTitle,
  onNewPageTitleChange,
  onNewPageConfirm,
  onNewPageCancel,
}: NavTreeProps) {
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set())
  const [activeId, setActiveId] = useState<string | null>(null)
  const [projection, setProjection] = useState<Projection | null>(null)
  const deltaXRef = useRef(0)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } })
  )

  const flatNodes = flattenTree(nodes, collapsedIds)

  function handleDragStart(event: DragStartEvent) {
    setActiveId(event.active.id as string)
    setProjection(null)
    deltaXRef.current = 0
  }

  function handleDragMove(event: DragMoveEvent) {
    deltaXRef.current = event.delta.x
    const { active, over } = event
    if (!over || active.id === over.id) {
      setProjection(null)
      return
    }
    setProjection(
      getProjection(flatNodes, active.id as string, over.id as string, event.delta.x)
    )
  }

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    setActiveId(null)
    setProjection(null)
    deltaXRef.current = 0

    if (!over || active.id === over.id) return

    const proj = getProjection(
      flatNodes,
      active.id as string,
      over.id as string,
      deltaXRef.current,
    )

    const newTree = buildTree(flatNodes, proj, active.id as string)
    onSidebarUpdate(newTree)
  }

  const activeFlat = activeId ? flatNodes.find((f) => f.id === activeId) : null

  // Projected depth for the overlay — live update during drag
  const projectedDepth = projection?.depth ?? activeFlat?.depth ?? 0

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragMove={handleDragMove}
      onDragEnd={handleDragEnd}
    >
      <SortableContext
        items={flatNodes.map((f) => f.id)}
        strategy={verticalListSortingStrategy}
      >
        <div className="relative">
          {flatNodes.map((flat, index) => {
            // Determine if we should render a drop indicator above this item
            const showDropLineAbove =
              projection !== null &&
              activeId !== null &&
              projection.newIndex === index &&
              flat.id !== activeId

            // Also check if drop indicator should appear at the very end
            const isLast = index === flatNodes.length - 1
            const showDropLineBelow =
              isLast &&
              projection !== null &&
              activeId !== null &&
              projection.newIndex >= flatNodes.length

            return (
              <div key={flat.id}>
                {showDropLineAbove && (
                  <div
                    style={{
                      height: '2px',
                      background: 'var(--color-purple-accent)',
                      marginLeft: `${projection.depth * INDENT_WIDTH + 8}px`,
                      marginRight: '8px',
                      borderRadius: '1px',
                    }}
                  />
                )}
                <SortableNavItem
                  flat={flat}
                  selectedPath={selectedPath}
                  onSelect={onSelect}
                  activeTitle={activeTitle}
                  activePath={activePath}
                  onSidebarUpdate={onSidebarUpdate}
                  onAddSubpage={onAddSubpage}
                  addingSubpageUnder={addingSubpageUnder}
                  newPageTitle={newPageTitle}
                  onNewPageTitleChange={onNewPageTitleChange}
                  onNewPageConfirm={onNewPageConfirm}
                  onNewPageCancel={onNewPageCancel}
                  isCollapsed={collapsedIds.has(flat.id)}
                  onToggleCollapse={(id) => {
                    setCollapsedIds((prev) => {
                      const next = new Set(prev)
                      if (next.has(id)) next.delete(id)
                      else next.add(id)
                      return next
                    })
                  }}
                  flatNodes={flatNodes}
                  isDraggingActive={!!activeId}
                />
                {showDropLineBelow && (
                  <div
                    style={{
                      height: '2px',
                      background: 'var(--color-purple-accent)',
                      marginLeft: `${projection.depth * INDENT_WIDTH + 8}px`,
                      marginRight: '8px',
                      borderRadius: '1px',
                    }}
                  />
                )}
              </div>
            )
          })}
        </div>
      </SortableContext>
      <DragOverlay>
        {activeFlat && (
          <NavItemOverlay
            node={activeFlat.node}
            projectedDepth={projectedDepth}
            activeTitle={activeTitle}
            activePath={activePath}
          />
        )}
      </DragOverlay>
    </DndContext>
  )
}

interface SortableNavItemProps {
  flat: FlatNode
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  activeTitle?: string
  activePath?: string | null
  onSidebarUpdate: (newTree: NavNode[]) => void
  onAddSubpage: (parentPath: string) => void
  addingSubpageUnder: string | null
  newPageTitle: string
  onNewPageTitleChange: (val: string) => void
  onNewPageConfirm: (parentPath: string) => void
  onNewPageCancel: () => void
  isCollapsed: boolean
  onToggleCollapse: (id: string) => void
  flatNodes: FlatNode[]
  isDraggingActive: boolean
}

function SortableNavItem({
  flat,
  selectedPath,
  onSelect,
  activeTitle,
  activePath,
  onSidebarUpdate,
  onAddSubpage,
  addingSubpageUnder,
  newPageTitle,
  onNewPageTitleChange,
  onNewPageConfirm,
  onNewPageCancel,
  isCollapsed,
  onToggleCollapse,
  flatNodes,
  isDraggingActive,
}: SortableNavItemProps) {
  const { node, depth } = flat
  const [hovered, setHovered] = useState(false)
  const [showContextMenu, setShowContextMenu] = useState(false)
  const contextMenuRef = useRef<HTMLDivElement>(null)

  const hasChildren = !!(node.children && node.children.length > 0)
  const isDir = node.type === 'dir'
  const isSelected = selectedPath === node.path.replace(/\.md$/, '')
  const nodePath = node.path.replace(/\.md$/, '')
  const isAddingSubpageHere = addingSubpageUnder === nodePath

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
    opacity: isDragging ? 0 : 1,
  }

  // Close context menu on outside click
  useEffect(() => {
    if (!showContextMenu) return
    const handler = (e: MouseEvent) => {
      if (contextMenuRef.current && !contextMenuRef.current.contains(e.target as Node)) {
        setShowContextMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showContextMenu])

  const paddingLeft = depth * INDENT_WIDTH + 8

  const displayTitle =
    activePath && activePath === node.path.replace(/\.md$/, '')
      ? (activeTitle ?? node.title)
      : node.title

  // Context menu actions
  function handleMoveToRoot() {
    setShowContextMenu(false)
    // Find the active flat node and rebuild with depth=0 and parentId=null
    const proj: Projection = { depth: 0, parentId: null, newIndex: flatNodes.length - 1 }
    const newTree = buildTree(flatNodes, proj, node.path)
    onSidebarUpdate(newTree)
  }

  function handlePromote() {
    setShowContextMenu(false)
    if (flat.depth === 0) return
    // Move to depth-1, find new parent
    const currentIndex = flatNodes.findIndex((f) => f.id === node.path)
    let newParentId: string | null = null
    if (flat.depth >= 2) {
      for (let i = currentIndex - 1; i >= 0; i--) {
        if (flatNodes[i].depth === flat.depth - 2) {
          newParentId = flatNodes[i].id
          break
        }
      }
    }
    const proj: Projection = {
      depth: flat.depth - 1,
      parentId: newParentId,
      newIndex: currentIndex,
    }
    const newTree = buildTree(flatNodes, proj, node.path)
    onSidebarUpdate(newTree)
  }

  // Inline sub-page input shown below this item when active
  const subpageInput = isAddingSubpageHere && (
    <div className="py-1 pr-2" style={{ paddingLeft: `${paddingLeft + 12}px` }}>
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

  return (
    <div ref={setNodeRef} style={style}>
      <div
        className={`flex w-full items-center py-1 text-xs transition-colors group ${
          isSelected && !isDir
            ? 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-deep)] font-medium'
            : 'text-[var(--color-foreground)] hover:bg-[var(--color-muted-foreground)]/5'
        }`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      >
        {/* Indent spacer */}
        <span style={{ width: `${paddingLeft}px`, flexShrink: 0 }} />

        {/* Collapse/expand chevron or icon */}
        <div className="relative w-5 h-5 shrink-0 flex items-center justify-center mr-1">
          {hasChildren ? (
            <>
              {isDir
                ? <FolderOpen size={13} className="shrink-0 transition-opacity group-hover:opacity-0" />
                : <span className="text-sm leading-none transition-opacity group-hover:opacity-0 select-none">{node.icon ?? DEFAULT_ICON}</span>
              }
              <button
                className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                onClick={(e) => { e.stopPropagation(); onToggleCollapse(node.path) }}
                tabIndex={-1}
                aria-label={isCollapsed ? 'Uitklappen' : 'Inklappen'}
                type="button"
              >
                <ChevronRight
                  size={12}
                  className={`text-[var(--color-muted-foreground)] transition-transform ${isCollapsed ? '' : 'rotate-90'}`}
                />
              </button>
            </>
          ) : (
            isDir
              ? <FolderOpen size={13} className="shrink-0 text-[var(--color-muted-foreground)]" />
              : <span className="shrink-0 text-sm leading-none select-none">{node.icon ?? DEFAULT_ICON}</span>
          )}
        </div>

        {/* Title button */}
        <button
          className={`flex flex-1 items-center min-w-0 text-left ${
            isDir
              ? 'font-medium text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]'
              : isSelected
                ? 'text-[var(--color-purple-deep)] font-medium'
                : 'text-[var(--color-foreground)]'
          }`}
          onClick={() => { if (!isDir) onSelect(node) }}
          disabled={isDir && !hasChildren}
        >
          <span className="truncate">{displayTitle}</span>
        </button>

        {/* Action buttons */}
        <div className="flex items-center gap-0.5 mr-1.5 shrink-0">
          {(hovered || showContextMenu) && !isDraggingActive && (
            <>
              <button
                type="button"
                className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                onClick={() => onAddSubpage(nodePath)}
                title={m.docs_pages_add_subpage()}
                aria-label={m.docs_pages_add_subpage()}
              >
                <Plus size={10} />
              </button>
              <div className="relative" ref={contextMenuRef}>
                <button
                  type="button"
                  className="flex items-center justify-center w-4 h-4 rounded hover:bg-[var(--color-muted-foreground)]/15 text-[var(--color-muted-foreground)] hover:text-[var(--color-purple-deep)]"
                  onClick={() => setShowContextMenu((v) => !v)}
                  aria-label="Meer opties"
                >
                  <MoreHorizontal size={10} />
                </button>
                {showContextMenu && (
                  <div className="absolute right-0 top-5 z-20 w-44 rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md py-1">
                    {flat.depth > 0 && (
                      <button
                        className="w-full text-left px-3 py-1.5 text-xs text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]"
                        onClick={handlePromote}
                      >
                        {m.docs_tree_promote()}
                      </button>
                    )}
                    {flat.depth > 0 && (
                      <button
                        className="w-full text-left px-3 py-1.5 text-xs text-[var(--color-foreground)] hover:bg-[var(--color-secondary)]"
                        onClick={handleMoveToRoot}
                      >
                        {m.docs_tree_move_to_root()}
                      </button>
                    )}
                    {flat.depth === 0 && (
                      <p className="px-3 py-1.5 text-xs text-[var(--color-muted-foreground)] italic">
                        Al op rootniveau
                      </p>
                    )}
                  </div>
                )}
              </div>
            </>
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

// Overlay shown while dragging — reflects live projected depth
function NavItemOverlay({
  node,
  projectedDepth,
  activeTitle,
  activePath,
}: {
  node: NavNode
  projectedDepth: number
  activeTitle?: string
  activePath?: string | null
}) {
  const displayTitle =
    activePath && activePath === node.path.replace(/\.md$/, '')
      ? (activeTitle ?? node.title)
      : node.title

  const isDir = node.type === 'dir'
  const paddingLeft = projectedDepth * INDENT_WIDTH + 8

  return (
    <div
      className="flex items-center gap-1.5 rounded text-xs font-medium bg-[var(--color-card)] border border-[var(--color-border)] shadow-md py-1 pr-2 text-[var(--color-purple-deep)]"
      style={{ paddingLeft: `${paddingLeft}px` }}
    >
      {isDir
        ? <FolderOpen size={13} className="shrink-0" />
        : <span className="shrink-0 text-sm leading-none">{node.icon ?? DEFAULT_ICON}</span>
      }
      <span className="truncate">{displayTitle}</span>
    </div>
  )
}
