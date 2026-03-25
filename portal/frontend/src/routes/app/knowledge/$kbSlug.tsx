import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Brain, FileText, Globe, Lock, RefreshCw, Trash2, Loader2, Plus } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
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
import { API_BASE, CONNECTOR_API_BASE } from '@/lib/api'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/knowledge/$kbSlug')({
  validateSearch: (search: Record<string, unknown>): { tab?: Tab } => ({
    tab: (search.tab as Tab | undefined) ?? undefined,
  }),
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgeDetailPage />
    </ProductGuard>
  ),
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  docs_enabled: boolean
  gitea_repo_slug: string | null
  owner_type: string
}

interface Connector {
  id: string
  name: string
  connector_type: string
  is_enabled: boolean
  last_sync_at: string | null
  last_sync_status: string | null
  created_at: string
  schedule: string | null
  config: Record<string, unknown>
}

type Tab = 'docs' | 'sources' | 'stats'
type ConnectorType = 'github' | 'google_drive' | 'notion' | 'ms_docs'

interface GitHubConfig {
  installation_id: string
  repo_owner: string
  repo_name: string
  branch: string
  path_filter: string
}

function TypeBadge({ type }: { type: string }) {
  const labels: Record<string, () => string> = {
    github: m.admin_connectors_type_github,
    google_drive: m.admin_connectors_type_google_drive,
    notion: m.admin_connectors_type_notion,
    ms_docs: m.admin_connectors_type_ms_docs,
  }
  const label = labels[type] ?? (() => type)
  return <Badge variant="secondary">{label()}</Badge>
}

function StatusBadge({ connector }: { connector: Connector }) {
  if (!connector.is_enabled) {
    return <Badge variant="outline">{m.admin_connectors_status_disabled()}</Badge>
  }
  switch (connector.last_sync_status) {
    case 'RUNNING':
      return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    case 'COMPLETED':
      return <Badge variant="success">{m.admin_connectors_status_completed()}</Badge>
    case 'FAILED':
      return <Badge variant="destructive">{m.admin_connectors_status_failed()}</Badge>
    case 'AUTH_ERROR':
      return <Badge variant="destructive">{m.admin_connectors_status_auth_error()}</Badge>
    default:
      return <Badge variant="secondary">{m.admin_connectors_status_never()}</Badge>
  }
}

function SourcesTab({ kbSlug, token }: { kbSlug: string; token: string | undefined }) {
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [selectedType, setSelectedType] = useState<ConnectorType | null>(null)
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('')
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '',
    repo_owner: '',
    repo_name: '',
    branch: 'main',
    path_filter: '',
  })

  const { data, isLoading, error } = useQuery({
    queryKey: ['kb-connectors', kbSlug, token],
    queryFn: async () => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_fetch({ status: String(res.status) }))
      const all = await res.json() as Connector[]
      return all.filter((c) => c.config?.kb_slug === kbSlug)
    },
    enabled: !!token,
  })

  const connectors = data ?? []

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_delete_error({ status: String(res.status) }))
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-connectors', kbSlug] }),
  })

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedType) return
      const config: Record<string, unknown> = { kb_slug: kbSlug }
      if (selectedType === 'github') {
        config.installation_id = Number(githubConfig.installation_id)
        config.repo_owner = githubConfig.repo_owner
        config.repo_name = githubConfig.repo_name
        config.branch = githubConfig.branch
        if (githubConfig.path_filter) config.path_filter = githubConfig.path_filter
      }
      const res = await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, connector_type: selectedType, config, schedule: schedule || null }),
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_create({ status: String(res.status) }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors', kbSlug] })
      setShowAdd(false)
      setSelectedType(null)
      setName('')
      setSchedule('')
      setGithubConfig({ installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '' })
    },
  })

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      await fetch(`${CONNECTOR_API_BASE}/api/v1/connectors/${id}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors', kbSlug] })
    } finally {
      setSyncingIds((prev) => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  const connectorTypes: { type: ConnectorType; label: () => string; available: boolean }[] = [
    { type: 'github', label: m.admin_connectors_type_github, available: true },
    { type: 'google_drive', label: m.admin_connectors_type_google_drive, available: false },
    { type: 'notion', label: m.admin_connectors_type_notion, available: false },
    { type: 'ms_docs', label: m.admin_connectors_type_ms_docs, available: false },
  ]

  if (isLoading) {
    return <p className="py-8 text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
  }

  if (error) {
    return (
      <p className="py-8 text-sm text-[var(--color-destructive)]">
        {error instanceof Error ? error.message : m.admin_connectors_error_generic()}
      </p>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_subtitle()}</p>
        {!showAdd && (
          <Button size="sm" onClick={() => setShowAdd(true)}>
            <Plus className="h-4 w-4 mr-2" />
            {m.admin_connectors_add_button()}
          </Button>
        )}
      </div>

      {connectors.length > 0 && (
        <Card>
          <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.admin_connectors_col_name()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.admin_connectors_col_type()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.admin_connectors_col_status()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.admin_connectors_col_actions()}
                  </th>
                </tr>
              </thead>
              <tbody>
                {connectors.map((connector, i) => {
                  const isSyncing = syncingIds.has(connector.id)
                  const isRunning = connector.last_sync_status === 'RUNNING'
                  return (
                    <tr
                      key={connector.id}
                      className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                    >
                      <td className="px-6 py-3 font-medium text-[var(--color-purple-deep)]">{connector.name}</td>
                      <td className="px-6 py-3"><TypeBadge type={connector.connector_type} /></td>
                      <td className="px-6 py-3"><StatusBadge connector={connector} /></td>
                      <td className="px-6 py-3">
                        <div className="flex items-center gap-1">
                          <Tooltip label={m.admin_connectors_action_sync()}>
                            <button
                              disabled={isSyncing || isRunning}
                              onClick={() => void handleSync(connector.id)}
                              aria-label={m.admin_connectors_action_sync()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 disabled:opacity-40"
                            >
                              {isSyncing || isRunning
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <RefreshCw className="h-3.5 w-3.5" />}
                            </button>
                          </Tooltip>
                          <Tooltip label={m.admin_connectors_action_delete()}>
                            <button
                              onClick={() => setConfirmingDeleteId(connector.id)}
                              aria-label={m.admin_connectors_action_delete()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </Tooltip>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      {connectors.length === 0 && !showAdd && (
        <div className="py-12 text-center space-y-3">
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">{m.admin_connectors_empty()}</p>
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_empty_description()}</p>
          <Button variant="outline" size="sm" onClick={() => setShowAdd(true)}>
            <Plus className="h-4 w-4 mr-2" />
            {m.admin_connectors_add_button()}
          </Button>
        </div>
      )}

      {showAdd && (
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-5">
              <div className="space-y-2">
                <p className="text-sm font-medium text-[var(--color-purple-deep)]">
                  {m.admin_connectors_field_type()}
                </p>
                <div className="grid grid-cols-2 gap-3">
                  {connectorTypes.map(({ type, label, available }) => (
                    <button
                      key={type}
                      type="button"
                      disabled={!available}
                      onClick={() => { if (available) setSelectedType(type) }}
                      className={[
                        'relative flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
                        !available && 'cursor-not-allowed opacity-50',
                        available && selectedType === type
                          ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                          : available
                            ? 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50'
                            : 'border-[var(--color-border)] bg-[var(--color-card)]',
                      ].join(' ')}
                    >
                      <span className="text-sm font-medium text-[var(--color-purple-deep)]">{label()}</span>
                      {!available && (
                        <Badge variant="outline" className="text-xs">{m.admin_connectors_coming_soon()}</Badge>
                      )}
                    </button>
                  ))}
                </div>
              </div>

              {selectedType === 'github' && (
                <form
                  onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }}
                  className="space-y-4"
                >
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-name">{m.admin_connectors_field_name()}</Label>
                    <Input
                      id="conn-name"
                      required
                      placeholder={m.admin_connectors_field_name_placeholder()}
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-installation-id">{m.admin_connectors_github_installation_id()}</Label>
                    <Input
                      id="conn-installation-id"
                      type="number"
                      required
                      value={githubConfig.installation_id}
                      onChange={(e) => setGithubConfig((p) => ({ ...p, installation_id: e.target.value }))}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-1.5">
                      <Label htmlFor="conn-repo-owner">{m.admin_connectors_github_repo_owner()}</Label>
                      <Input
                        id="conn-repo-owner"
                        required
                        value={githubConfig.repo_owner}
                        onChange={(e) => setGithubConfig((p) => ({ ...p, repo_owner: e.target.value }))}
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="conn-repo-name">{m.admin_connectors_github_repo_name()}</Label>
                      <Input
                        id="conn-repo-name"
                        required
                        value={githubConfig.repo_name}
                        onChange={(e) => setGithubConfig((p) => ({ ...p, repo_name: e.target.value }))}
                      />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-branch">{m.admin_connectors_github_branch()}</Label>
                    <Input
                      id="conn-branch"
                      required
                      placeholder={m.admin_connectors_github_branch_placeholder()}
                      value={githubConfig.branch}
                      onChange={(e) => setGithubConfig((p) => ({ ...p, branch: e.target.value }))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-path-filter">{m.admin_connectors_github_path_filter()}</Label>
                    <Input
                      id="conn-path-filter"
                      placeholder={m.admin_connectors_github_path_filter_placeholder()}
                      value={githubConfig.path_filter}
                      onChange={(e) => setGithubConfig((p) => ({ ...p, path_filter: e.target.value }))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-schedule">{m.admin_connectors_field_schedule()}</Label>
                    <Input
                      id="conn-schedule"
                      placeholder={m.admin_connectors_field_schedule_placeholder()}
                      value={schedule}
                      onChange={(e) => setSchedule(e.target.value)}
                    />
                    <p className="text-xs text-[var(--color-muted-foreground)]">
                      {m.admin_connectors_field_schedule_hint()}
                    </p>
                  </div>
                  {createMutation.error && (
                    <p className="text-sm text-[var(--color-destructive)]">
                      {createMutation.error instanceof Error
                        ? createMutation.error.message
                        : m.admin_connectors_error_create_generic()}
                    </p>
                  )}
                  <div className="flex gap-2 pt-2">
                    <Button type="submit" disabled={createMutation.isPending}>
                      {createMutation.isPending
                        ? m.admin_connectors_create_submit_loading()
                        : m.admin_connectors_create_submit()}
                    </Button>
                    <Button type="button" variant="ghost" onClick={() => { setShowAdd(false); setSelectedType(null) }}>
                      {m.admin_connectors_cancel()}
                    </Button>
                  </div>
                </form>
              )}

              {!selectedType && (
                <div className="flex justify-end">
                  <Button type="button" variant="ghost" onClick={() => setShowAdd(false)}>
                    {m.admin_connectors_cancel()}
                  </Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <AlertDialog
        open={confirmingDeleteId !== null}
        onOpenChange={(open) => { if (!open) setConfirmingDeleteId(null) }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_connectors_delete_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.admin_connectors_delete_confirm_description()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_connectors_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => {
                if (confirmingDeleteId) deleteMutation.mutate(confirmingDeleteId)
                setConfirmingDeleteId(null)
              }}
            >
              {m.admin_connectors_action_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function KnowledgeDetailPage() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token

  const { data: kb, isLoading, isError } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        queryLogger.warn('KB fetch failed', { slug: kbSlug, status: res.status })
        throw new Error('KB laden mislukt')
      }
      return res.json() as Promise<KnowledgeBase>
    },
    enabled: !!token,
    retry: false,
  })

  const { tab } = Route.useSearch()
  const activeTab: Tab = tab ?? 'docs'

  if (isLoading) {
    return (
      <div className="p-8">
        <div className="h-8 w-48 rounded bg-[var(--color-secondary)] animate-pulse mb-4" />
        <div className="h-4 w-96 rounded bg-[var(--color-secondary)] animate-pulse" />
      </div>
    )
  }

  if (isError || !kb) {
    return (
      <div className="p-8 text-[var(--color-muted-foreground)]">
        {m.knowledge_detail_not_found()}
      </div>
    )
  }

  return (
    <div className="p-8 max-w-3xl">
      {/* Header */}
      <div className="flex items-start gap-3 mb-6">
        <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0 mt-0.5">
          <Brain className="h-5 w-5 text-[var(--color-purple-deep)]" />
        </div>
        <div className="flex-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {kb.name}
          </h1>
          {kb.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{kb.description}</p>
          )}
          <div className="flex items-center gap-1.5 mt-2 text-xs text-[var(--color-muted-foreground)]">
            {kb.visibility === 'public' ? (
              <Globe className="h-3.5 w-3.5" />
            ) : (
              <Lock className="h-3.5 w-3.5" />
            )}
            <span>
              {kb.visibility === 'public'
                ? m.knowledge_page_kb_visibility_public()
                : m.knowledge_page_kb_visibility_internal()}
            </span>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-[var(--color-border)] mb-6">
        {(['docs', 'sources', 'stats'] as Tab[]).map((tab) => (
          <Link
            key={tab}
            to="/app/knowledge/$kbSlug"
            params={{ kbSlug }}
            search={{ tab }}
            className={[
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab
                ? 'border-[var(--color-purple-deep)] text-[var(--color-purple-deep)]'
                : 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
            ].join(' ')}
          >
            {tab === 'docs'
              ? m.knowledge_detail_tab_docs()
              : tab === 'sources'
                ? m.knowledge_detail_tab_sources()
                : m.knowledge_detail_tab_stats()}
          </Link>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'docs' && (
        <div className="flex flex-col items-center gap-4 py-12 text-center text-[var(--color-muted-foreground)]">
          <FileText className="h-10 w-10" />
          <p className="text-sm">{m.knowledge_detail_docs_stub()}</p>
        </div>
      )}

      {activeTab === 'sources' && (
        <SourcesTab kbSlug={kbSlug} token={token} />
      )}

      {activeTab === 'stats' && (
        <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">
          {m.knowledge_detail_stats_stub()}
        </div>
      )}
    </div>
  )
}
