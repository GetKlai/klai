import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import {
  RefreshCw, Trash2, Loader2, Plus, Pencil, AlertTriangle,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { SyncStatusBadge, ASSERTION_MODE_OPTIONS } from './-kb-helpers'
import type { ConnectorSummary, MembersResponse, GitHubConfig, WebCrawlerConfig } from './-kb-types'

type ConnectorSearch = {
  edit?: string
}

export const Route = createFileRoute('/app/knowledge/$kbSlug/connectors')({
  validateSearch: (search: Record<string, unknown>): ConnectorSearch => ({
    edit: typeof search.edit === 'string' ? search.edit : undefined,
  }),
  component: ConnectorsTab,
})

function ConnectorsTab() {
  const { kbSlug } = Route.useParams()
  const { edit: editingId } = Route.useSearch()
  const navigate = useNavigate({ from: Route.fullPath })
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())

  // Determine ownership from cached members query
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token,
  })
  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  // Edit state
  const [editName, setEditName] = useState('')
  const [editWebcrawlerConfig, setEditWebcrawlerConfig] = useState<WebCrawlerConfig>({ base_url: '', path_prefix: '', max_pages: '200', content_selector: '' })
  const [editGithubConfig, setEditGithubConfig] = useState<GitHubConfig>({ installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '' })
  const editInitializedRef = useRef<string | null>(null)
  const [editPreviewResult, setEditPreviewResult] = useState<{ fit_markdown: string; word_count: number; warnings: string[] } | null>(null)
  const [editAllowedAssertionModes, setEditAllowedAssertionModes] = useState<string[]>([])

  const { data: connectors = [], isLoading } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token),
    enabled: !!token,
    refetchInterval: (query) => {
      const data = query.state.data
      if (Array.isArray(data) && data.some((c) => c.last_sync_status === 'RUNNING' || c.last_sync_status === 'running')) {
        return 5000
      }
      return false
    },
  })

  useEffect(() => {
    if (!editingId) {
      editInitializedRef.current = null
      return
    }
    if (editInitializedRef.current === editingId) return
    const c = connectors.find((conn) => conn.id === editingId)
    if (!c) return
    editInitializedRef.current = editingId
    setEditPreviewResult(null)
    setEditName(c.name)
    setEditAllowedAssertionModes(c.allowed_assertion_modes ?? [])
    if (c.connector_type === 'web_crawler') {
      const cfg = c.config as { base_url?: string; path_prefix?: string; max_pages?: number; content_selector?: string }
      setEditWebcrawlerConfig({
        base_url: String(cfg.base_url ?? ''),
        path_prefix: String(cfg.path_prefix ?? ''),
        max_pages: String(cfg.max_pages ?? '200'),
        content_selector: cfg.content_selector ?? '',
      })
    }
    if (c.connector_type === 'github') {
      const cfg = c.config as { installation_id?: number; repo_owner?: string; repo_name?: string; branch?: string; path_filter?: string }
      setEditGithubConfig({
        installation_id: String(cfg.installation_id ?? ''),
        repo_owner: String(cfg.repo_owner ?? ''),
        repo_name: String(cfg.repo_name ?? ''),
        branch: String(cfg.branch ?? 'main'),
        path_filter: String(cfg.path_filter ?? ''),
      })
    }
  }, [editingId, connectors])

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}`, token, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] }),
  })

  const updateMutation = useMutation({
    mutationFn: async (id: string) => {
      const connector = connectors.find((c) => c.id === id)
      if (!connector) return
      const config: Record<string, unknown> = {}
      if (connector.connector_type === 'github') {
        config.installation_id = Number(editGithubConfig.installation_id)
        config.repo_owner = editGithubConfig.repo_owner
        config.repo_name = editGithubConfig.repo_name
        config.branch = editGithubConfig.branch
        if (editGithubConfig.path_filter) config.path_filter = editGithubConfig.path_filter
      }
      if (connector.connector_type === 'web_crawler') {
        config.base_url = editWebcrawlerConfig.base_url
        if (editWebcrawlerConfig.path_prefix) config.path_prefix = editWebcrawlerConfig.path_prefix
        if (editWebcrawlerConfig.max_pages) config.max_pages = Number(editWebcrawlerConfig.max_pages)
        if (editWebcrawlerConfig.content_selector) config.content_selector = editWebcrawlerConfig.content_selector
      }
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}`, token, {
        method: 'PATCH',
        body: JSON.stringify({
          name: editName,
          config,
          allowed_assertion_modes: editAllowedAssertionModes.length > 0 ? editAllowedAssertionModes : null,
        }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      void navigate({ search: { edit: undefined } })
    },
  })

  const editPreviewMutation = useMutation({
    mutationFn: async ({ url, content_selector }: { url: string; content_selector?: string }) => {
      try {
        return await apiFetch<{ fit_markdown: string; word_count: number; warnings: string[]; url: string }>(
          `/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`,
          token,
          { method: 'POST', body: JSON.stringify({ url, content_selector: content_selector || null }) },
        )
      } catch {
        return { fit_markdown: '', word_count: 0, warnings: [] as string[] }
      }
    },
    onSuccess: (data) => setEditPreviewResult(data),
    onError: () => setEditPreviewResult({ fit_markdown: '', word_count: 0, warnings: [] }),
  })

  function startEdit(c: ConnectorSummary) {
    editInitializedRef.current = null
    void navigate({ search: { edit: c.id } })
    setEditPreviewResult(null)
    setEditName(c.name)
    setEditAllowedAssertionModes(c.allowed_assertion_modes ?? [])
    if (c.connector_type === 'web_crawler') {
      const cfg = c.config as { base_url?: string; path_prefix?: string; max_pages?: number; content_selector?: string }
      setEditWebcrawlerConfig({
        base_url: String(cfg.base_url ?? ''),
        path_prefix: String(cfg.path_prefix ?? ''),
        max_pages: String(cfg.max_pages ?? '200'),
        content_selector: cfg.content_selector ?? '',
      })
    }
    if (c.connector_type === 'github') {
      const cfg = c.config as { installation_id?: number; repo_owner?: string; repo_name?: string; branch?: string; path_filter?: string }
      setEditGithubConfig({
        installation_id: String(cfg.installation_id ?? ''),
        repo_owner: String(cfg.repo_owner ?? ''),
        repo_name: String(cfg.repo_name ?? ''),
        branch: String(cfg.branch ?? 'main'),
        path_filter: String(cfg.path_filter ?? ''),
      })
    }
  }

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}/sync`, token, { method: 'POST' })
      queryClient.setQueryData(['kb-connectors-portal', kbSlug], (old: ConnectorSummary[] | undefined) =>
        old?.map((c) => c.id === id ? { ...c, last_sync_status: 'running' } : c)
      )
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    } catch {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    } finally {
      setSyncingIds((prev) => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  if (isLoading) {
    return <p className="py-4 text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
  }

  return (
    <div className="space-y-3">
      {connectors.length > 0 && (
        <Card>
          <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.admin_connectors_col_name()}</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.admin_connectors_col_status()}</th>
                  {isOwner && <th className="px-4 py-2.5 w-20" />}
                </tr>
              </thead>
              <tbody>
                {connectors.map((c, i) => {
                  const isSyncing = syncingIds.has(c.id)
                  const isRunning = c.last_sync_status?.toUpperCase() === 'RUNNING'
                  return (
                    <tr key={c.id} className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}>
                      <td className="px-4 py-2.5 font-medium text-[var(--color-purple-deep)]">{c.name}</td>
                      <td className="px-4 py-2.5"><SyncStatusBadge status={c.last_sync_status} /></td>
                      {isOwner && (
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1">
                            <Tooltip label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}>
                              <button
                                disabled={isSyncing || isRunning}
                                onClick={() => void handleSync(c.id)}
                                aria-label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] hover:opacity-70 disabled:opacity-40"
                              >
                                {isSyncing || isRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                              </button>
                            </Tooltip>
                            <Tooltip label={m.admin_connectors_action_edit()}>
                              <button
                                onClick={() => startEdit(c)}
                                aria-label={m.admin_connectors_action_edit()}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] hover:opacity-70"
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </button>
                            </Tooltip>
                            <Tooltip label={m.admin_connectors_action_delete()}>
                              <button
                                onClick={() => setConfirmingDeleteId(c.id)}
                                aria-label={m.admin_connectors_action_delete()}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] hover:opacity-70"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </Tooltip>
                          </div>
                        </td>
                      )}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {connectors.length === 0 && (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_detail_connectors_empty()}</p>
      )}

      {editingId !== undefined && (() => {
        const connector = connectors.find((c) => c.id === editingId)
        if (!connector) return null
        return (
          <Card>
            <CardContent className="pt-6">
              <div className="space-y-4">
                {connector.connector_type === 'web_crawler' && (
                  <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate(editingId) }} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                      <Input id="edit-conn-name" required value={editName} onChange={(e) => setEditName(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-base-url">{m.admin_connectors_webcrawler_base_url()}</Label>
                      <Input id="edit-conn-base-url" type="url" required value={editWebcrawlerConfig.base_url} onChange={(e) => setEditWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-content-selector">{m.admin_connectors_webcrawler_content_selector()}</Label>
                      <Input id="edit-conn-content-selector" placeholder={m.admin_connectors_webcrawler_content_selector_placeholder()} value={editWebcrawlerConfig.content_selector} onChange={(e) => setEditWebcrawlerConfig((p) => ({ ...p, content_selector: e.target.value }))} />
                    </div>
                    <div>
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={!editWebcrawlerConfig.base_url || editPreviewMutation.isPending}
                        onClick={() => editPreviewMutation.mutate({ url: editWebcrawlerConfig.base_url, content_selector: editWebcrawlerConfig.content_selector })}
                      >
                        {editPreviewMutation.isPending ? m.admin_connectors_webcrawler_preview_loading() : m.admin_connectors_webcrawler_preview_button()}
                      </Button>
                    </div>
                    {editPreviewResult !== null && (editPreviewResult.warnings ?? []).length > 0 && (
                      <div className="flex gap-2 items-start rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                        <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                        <span>{m.admin_connectors_webcrawler_warning_nav_detected()}</span>
                      </div>
                    )}
                    {editPreviewResult !== null && (
                      <div className="rounded-lg border border-[var(--color-border)] p-3 space-y-2">
                        <div className="flex items-center justify-between">
                          <span className="text-sm font-medium text-[var(--color-purple-deep)]">{m.admin_connectors_webcrawler_preview_title()}</span>
                          <span className="text-xs text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_word_count({ count: String(editPreviewResult.word_count) })}</span>
                        </div>
                        {editPreviewResult.fit_markdown ? (
                          <div className="overflow-y-auto max-h-64 text-xs [&_h1]:text-sm [&_h1]:font-semibold [&_h1]:text-[var(--color-purple-deep)] [&_h1]:mb-1 [&_h2]:text-xs [&_h2]:font-semibold [&_h2]:text-[var(--color-purple-deep)] [&_h2]:mb-1 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-[var(--color-purple-deep)] [&_h3]:mb-1 [&_p]:text-[var(--color-muted-foreground)] [&_p]:mb-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:text-[var(--color-muted-foreground)] [&_ul]:mb-1.5 [&_ol]:list-decimal [&_ol]:pl-4 [&_ol]:text-[var(--color-muted-foreground)] [&_ol]:mb-1.5 [&_strong]:font-semibold [&_strong]:text-[var(--color-purple-deep)] [&_hr]:border-[var(--color-border)] [&_hr]:my-2">
                            <ReactMarkdown components={{ a: ({ children }) => <span className="text-[var(--color-accent)]">{children}</span> }}>{editPreviewResult.fit_markdown}</ReactMarkdown>
                          </div>
                        ) : (
                          <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_webcrawler_preview_empty()}</p>
                        )}
                      </div>
                    )}
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-path-prefix">{m.admin_connectors_webcrawler_path_prefix()}</Label>
                      <Input id="edit-conn-path-prefix" value={editWebcrawlerConfig.path_prefix} onChange={(e) => setEditWebcrawlerConfig((p) => ({ ...p, path_prefix: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-max-pages">{m.admin_connectors_webcrawler_max_pages()}</Label>
                      <Input id="edit-conn-max-pages" type="number" min="1" max="2000" value={editWebcrawlerConfig.max_pages} onChange={(e) => setEditWebcrawlerConfig((p) => ({ ...p, max_pages: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                      <MultiSelect options={ASSERTION_MODE_OPTIONS} value={editAllowedAssertionModes} onChange={setEditAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                    </div>
                    {updateMutation.error && (
                      <p className="text-sm text-[var(--color-destructive)]">
                        {updateMutation.error instanceof Error ? updateMutation.error.message : m.admin_connectors_error_create_generic()}
                      </p>
                    )}
                    <div className="flex gap-2 pt-1">
                      <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => void navigate({ search: { edit: undefined } })}>{m.admin_connectors_cancel()}</Button>
                    </div>
                  </form>
                )}
                {connector.connector_type === 'github' && (
                  <form onSubmit={(e) => { e.preventDefault(); updateMutation.mutate(editingId) }} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-name">{m.admin_connectors_field_name()}</Label>
                      <Input id="edit-conn-name" required value={editName} onChange={(e) => setEditName(e.target.value)} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-install">{m.admin_connectors_github_installation_id()}</Label>
                      <Input id="edit-conn-install" type="number" required value={editGithubConfig.installation_id} onChange={(e) => setEditGithubConfig((p) => ({ ...p, installation_id: e.target.value }))} />
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label htmlFor="edit-conn-owner">{m.admin_connectors_github_repo_owner()}</Label>
                        <Input id="edit-conn-owner" required value={editGithubConfig.repo_owner} onChange={(e) => setEditGithubConfig((p) => ({ ...p, repo_owner: e.target.value }))} />
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="edit-conn-repo">{m.admin_connectors_github_repo_name()}</Label>
                        <Input id="edit-conn-repo" required value={editGithubConfig.repo_name} onChange={(e) => setEditGithubConfig((p) => ({ ...p, repo_name: e.target.value }))} />
                      </div>
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="edit-conn-branch">{m.admin_connectors_github_branch()}</Label>
                      <Input id="edit-conn-branch" required value={editGithubConfig.branch} onChange={(e) => setEditGithubConfig((p) => ({ ...p, branch: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label>{m.admin_connectors_assertion_modes_label()}</Label>
                      <MultiSelect options={ASSERTION_MODE_OPTIONS} value={editAllowedAssertionModes} onChange={setEditAllowedAssertionModes} placeholder={m.admin_connectors_assertion_modes_placeholder()} />
                    </div>
                    {updateMutation.error && (
                      <p className="text-sm text-[var(--color-destructive)]">
                        {updateMutation.error instanceof Error ? updateMutation.error.message : m.admin_connectors_error_create_generic()}
                      </p>
                    )}
                    <div className="flex gap-2 pt-1">
                      <Button type="submit" size="sm" disabled={updateMutation.isPending}>{m.admin_connectors_save()}</Button>
                      <Button type="button" size="sm" variant="ghost" onClick={() => void navigate({ search: { edit: undefined } })}>{m.admin_connectors_cancel()}</Button>
                    </div>
                  </form>
                )}
              </div>
            </CardContent>
          </Card>
        )
      })()}

      {isOwner && (
        <Button size="sm" variant="outline" onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/add-connector', params: { kbSlug } })}>
          <Plus className="h-4 w-4 mr-1" />
          {m.admin_connectors_add_button()}
        </Button>
      )}

      <AlertDialog open={confirmingDeleteId !== null} onOpenChange={(open) => { if (!open) setConfirmingDeleteId(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_connectors_delete_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.admin_connectors_delete_confirm_description()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_connectors_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => { if (confirmingDeleteId) deleteMutation.mutate(confirmingDeleteId); setConfirmingDeleteId(null) }}
            >
              {m.admin_connectors_action_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
