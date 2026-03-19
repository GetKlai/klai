import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation } from '@tanstack/react-query'
import { useState, useEffect, useRef, useCallback, forwardRef, useImperativeHandle } from 'react'
import { ChevronRight, FileText, FolderOpen, ArrowLeft, Upload, Plus, Check, X, MoreHorizontal, Loader2, Users, Lock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCreateBlockNote } from '@blocknote/react'
import { BlockNoteView } from '@blocknote/mantine'
import '@blocknote/mantine/style.css'
import * as m from '@/paraglide/messages'

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
  path: string
  type: 'file' | 'dir'
  children?: NavNode[]
}

interface PageData {
  frontmatter: { title?: string; description?: string; edit_access?: 'org' | string[] }
  content: string
}

function KBEditorPage() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const orgSlug = getOrgSlug()

  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const [editorKey, setEditorKey] = useState(0)           // increment to force BlockPageEditor remount
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')

  const [newPageTitle, setNewPageTitle] = useState('')
  const [showNewPage, setShowNewPage] = useState(false)
  const [showMenu, setShowMenu] = useState(false)
  const [showAccessPanel, setShowAccessPanel] = useState(false)
  const [accessMode, setAccessMode] = useState<'org' | 'specific'>('org')
  const [accessUsers, setAccessUsers] = useState<string[]>([])
  const [newUserId, setNewUserId] = useState('')
  const [accessSaveStatus, setAccessSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
  const editorRef = useRef<{ getMarkdown: () => string }>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Stable refs for save function — avoids stale closures entirely
  const tokenRef = useRef(token)
  const selectedPathRef = useRef(selectedPath)
  const editTitleRef = useRef(editTitle)
  tokenRef.current = token
  selectedPathRef.current = selectedPath
  editTitleRef.current = editTitle

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
  })

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
      setSaveStatus('idle')
      setEditorKey((k) => k + 1)   // remount editor with fresh content
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

  const handleNewPage = async () => {
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
      // Cancel any pending autosave for the old page
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
      await refetchTree()
      // Reset editor immediately to avoid flashing old content
      setSelectedPath(path)
      setEditTitle(newPageTitle)
      setEditContent('')
      setEditorKey((k) => k + 1)
      setNewPageTitle('')
      setShowNewPage(false)
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

  // Stable save function — reads all live values from refs, no stale closures
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
          body: JSON.stringify({ title, content }),
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
  }, [orgSlug, kbSlug, refetchTree])   // orgSlug/kbSlug are stable; refetchTree is stable

  // Stable debounce — always uses latest doSave which reads from refs
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
          body: JSON.stringify({ title: editTitleRef.current, content, edit_access: editAccess }),
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
          {tree.length === 0 ? (
            <p className="px-3 py-2 text-xs text-[var(--color-muted-foreground)]">
              {m.docs_pages_empty()}
            </p>
          ) : (
            <NavTree
              nodes={tree}
              selectedPath={selectedPath}
              onSelect={(node) => {
                if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
                setSelectedPath(node.path.replace(/\.md$/, ''))
                setEditContent('')
                setEditorKey((k) => k + 1)
              }}
            />
          )}
        </div>
        <div className="px-3 py-3 border-t border-[var(--color-border)] space-y-2">
          {showNewPage ? (
            <div className="space-y-1.5">
              <Input
                value={newPageTitle}
                onChange={(e) => setNewPageTitle(e.target.value)}
                placeholder={m.docs_editor_title_placeholder()}
                className="h-7 text-xs"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleNewPage()
                  if (e.key === 'Escape') { setShowNewPage(false); setNewPageTitle('') }
                }}
              />
              <div className="flex gap-1">
                <Button
                  size="sm"
                  className="flex-1 h-6 text-xs"
                  onClick={handleNewPage}
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
              onClick={() => setShowNewPage(true)}
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
                {/* Save status indicator */}
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
                {/* Settings menu */}
                <div className="relative">
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

function NavTree({
  nodes,
  selectedPath,
  onSelect,
  depth = 0,
}: {
  nodes: NavNode[]
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  depth?: number
}) {
  return (
    <>
      {nodes.map((node) => (
        <NavItem
          key={node.path}
          node={node}
          selectedPath={selectedPath}
          onSelect={onSelect}
          depth={depth}
        />
      ))}
    </>
  )
}

function NavItem({
  node,
  selectedPath,
  onSelect,
  depth,
}: {
  node: NavNode
  selectedPath: string | null
  onSelect: (node: NavNode) => void
  depth: number
}) {
  const [open, setOpen] = useState(true)
  const isSelected = selectedPath === node.path.replace(/\.md$/, '')

  if (node.type === 'dir') {
    return (
      <div>
        <button
          className="flex w-full items-center gap-1.5 px-3 py-1 text-xs font-medium text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]"
          style={{ paddingLeft: `${12 + depth * 12}px` }}
          onClick={() => setOpen((o) => !o)}
        >
          <ChevronRight size={11} className={`transition-transform ${open ? 'rotate-90' : ''}`} />
          <FolderOpen size={13} />
          {node.title}
        </button>
        {open && node.children && (
          <NavTree
            nodes={node.children}
            selectedPath={selectedPath}
            onSelect={onSelect}
            depth={depth + 1}
          />
        )}
      </div>
    )
  }

  return (
    <button
      className={`flex w-full items-center gap-1.5 py-1 text-xs transition-colors ${
        isSelected
          ? 'bg-[var(--color-purple-accent)]/10 text-[var(--color-purple-deep)] font-medium'
          : 'text-[var(--color-foreground)] hover:bg-[var(--color-muted-foreground)]/5'
      }`}
      style={{ paddingLeft: `${16 + depth * 12}px`, paddingRight: '12px' }}
      onClick={() => onSelect(node)}
    >
      <FileText size={13} className="shrink-0" />
      <span className="truncate">{node.title}</span>
    </button>
  )
}
