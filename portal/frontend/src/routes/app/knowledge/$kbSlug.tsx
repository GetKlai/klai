import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  Brain, FileText, Globe, Lock, RefreshCw, Trash2, Loader2, Plus,
  BookOpen, Users, BarChart2, Zap, List, FolderTree, ChevronRight, ChevronDown,
  Pencil, Check, X,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
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
import { DeleteConfirmButton } from '@/components/ui/delete-confirm-button'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { queryLogger, taxonomyLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'

export const Route = createFileRoute('/app/knowledge/$kbSlug')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgeDetailPage />
    </ProductGuard>
  ),
})

// -- Types -------------------------------------------------------------------

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

interface ConnectorSummary {
  id: string
  name: string
  connector_type: string
  last_sync_status: string | null
  last_sync_at: string | null
}

interface KBStats {
  docs_count: number | null
  connector_count: number
  connectors: ConnectorSummary[]
  volume: number | null
  usage_last_30d: number | null
}

interface UserMember {
  id: number
  user_id: string
  display_name: string | null
  email: string | null
  role: string
  granted_at: string
  granted_by: string
}

interface GroupMember {
  id: number
  group_id: number
  group_name: string
  role: string
  granted_at: string
  granted_by: string
}

interface MembersResponse {
  users: UserMember[]
  groups: GroupMember[]
}

type ConnectorType = 'github' | 'web_crawler' | 'google_drive' | 'notion' | 'ms_docs'

interface GitHubConfig {
  installation_id: string
  repo_owner: string
  repo_name: string
  branch: string
  path_filter: string
}

interface WebCrawlerConfig {
  base_url: string
  path_prefix: string
  max_pages: string
}

// -- Small helpers -----------------------------------------------------------

function roleBadge(role: string) {
  const labels: Record<string, () => string> = {
    viewer: m.knowledge_members_role_viewer,
    contributor: m.knowledge_members_role_contributor,
    owner: m.knowledge_members_role_owner,
  }
  return <Badge variant="secondary">{(labels[role] ?? (() => role))()}</Badge>
}

function SyncStatusBadge({ status }: { status: string | null }) {
  switch (status?.toUpperCase()) {
    case 'RUNNING': return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    case 'COMPLETED': return <Badge variant="success">{m.admin_connectors_status_completed()}</Badge>
    case 'FAILED': return <Badge variant="destructive">{m.admin_connectors_status_failed()}</Badge>
    case 'AUTH_ERROR': return <Badge variant="destructive">{m.admin_connectors_status_auth_error()}</Badge>
    case 'PENDING': return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    default: return <Badge variant="secondary">{m.admin_connectors_status_never()}</Badge>
  }
}

// -- Connectors section (owner: full CRUD; others: read-only) ---------------

function ConnectorsSection({
  kbSlug,
  token,
  isOwner,
}: {
  kbSlug: string
  token: string | undefined
  isOwner: boolean
}) {
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [selectedType, setSelectedType] = useState<ConnectorType | null>(null)
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('')
  const [githubConfig, setGithubConfig] = useState<GitHubConfig>({
    installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '',
  })
  const [webcrawlerConfig, setWebcrawlerConfig] = useState<WebCrawlerConfig>({
    base_url: '', path_prefix: '', max_pages: '200',
  })

  const { data: connectors = [], isLoading } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/connectors/`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_fetch({ status: String(res.status) }))
      return res.json() as Promise<ConnectorSummary[]>
    },
    enabled: !!token,
    refetchInterval: (query) => {
      const data = query.state.data
      if (Array.isArray(data) && data.some((c) => c.last_sync_status === 'RUNNING' || c.last_sync_status === 'running')) {
        return 5000
      }
      return false
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/connectors/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_connectors_delete_error({ status: String(res.status) }))
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] }),
  })

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!selectedType) return
      const config: Record<string, unknown> = {}
      if (selectedType === 'github') {
        config.installation_id = Number(githubConfig.installation_id)
        config.repo_owner = githubConfig.repo_owner
        config.repo_name = githubConfig.repo_name
        config.branch = githubConfig.branch
        if (githubConfig.path_filter) config.path_filter = githubConfig.path_filter
      }
      if (selectedType === 'web_crawler') {
        config.base_url = webcrawlerConfig.base_url
        if (webcrawlerConfig.path_prefix) config.path_prefix = webcrawlerConfig.path_prefix
        if (webcrawlerConfig.max_pages && webcrawlerConfig.max_pages !== '200') config.max_pages = Number(webcrawlerConfig.max_pages)
      }
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/connectors/`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, connector_type: selectedType, config, schedule: schedule || null }),
      })
      if (!res.ok) throw new Error(m.admin_connectors_error_create({ status: String(res.status) }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      setShowAdd(false)
      setSelectedType(null)
      setName('')
      setSchedule('')
      setGithubConfig({ installation_id: '', repo_owner: '', repo_name: '', branch: 'main', path_filter: '' })
      setWebcrawlerConfig({ base_url: '', path_prefix: '', max_pages: '200' })
    },
  })

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/connectors/${id}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        // Optimistically update cache so badge shows "Syncing" immediately
        queryClient.setQueryData(['kb-connectors-portal', kbSlug], (old: ConnectorSummary[] | undefined) =>
          old?.map((c) => c.id === id ? { ...c, last_sync_status: 'running' } : c)
        )
      }
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    } finally {
      setSyncingIds((prev) => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  const connectorTypes: { type: ConnectorType; label: () => string; available: boolean }[] = [
    { type: 'github', label: m.admin_connectors_type_github, available: true },
    { type: 'web_crawler', label: m.admin_connectors_type_website, available: true },
    { type: 'google_drive', label: m.admin_connectors_type_google_drive, available: false },
    { type: 'notion', label: m.admin_connectors_type_notion, available: false },
    { type: 'ms_docs', label: m.admin_connectors_type_ms_docs, available: false },
  ]

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

      {connectors.length === 0 && !showAdd && (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_detail_connectors_empty()}</p>
      )}

      {isOwner && !showAdd && (
        <Button size="sm" variant="outline" onClick={() => setShowAdd(true)}>
          <Plus className="h-4 w-4 mr-1" />
          {m.admin_connectors_add_button()}
        </Button>
      )}

      {showAdd && (
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                {connectorTypes.map(({ type, label, available }) => (
                  <button
                    key={type}
                    type="button"
                    disabled={!available}
                    onClick={() => { if (available) setSelectedType(type) }}
                    className={[
                      'flex flex-col items-start gap-2 rounded-xl border p-4 text-left transition-all',
                      !available && 'cursor-not-allowed opacity-50',
                      available && selectedType === type
                        ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                        : available
                          ? 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50'
                          : 'border-[var(--color-border)] bg-[var(--color-card)]',
                    ].join(' ')}
                  >
                    <span className="text-sm font-medium text-[var(--color-purple-deep)]">{label()}</span>
                    {!available && <Badge variant="outline" className="text-xs">{m.admin_connectors_coming_soon()}</Badge>}
                  </button>
                ))}
              </div>

              {selectedType === 'github' && (
                <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-name">{m.admin_connectors_field_name()}</Label>
                    <Input id="conn-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-install">{m.admin_connectors_github_installation_id()}</Label>
                    <Input id="conn-install" type="number" required value={githubConfig.installation_id} onChange={(e) => setGithubConfig((p) => ({ ...p, installation_id: e.target.value }))} />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="conn-owner">{m.admin_connectors_github_repo_owner()}</Label>
                      <Input id="conn-owner" required value={githubConfig.repo_owner} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_owner: e.target.value }))} />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="conn-repo">{m.admin_connectors_github_repo_name()}</Label>
                      <Input id="conn-repo" required value={githubConfig.repo_name} onChange={(e) => setGithubConfig((p) => ({ ...p, repo_name: e.target.value }))} />
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-branch">{m.admin_connectors_github_branch()}</Label>
                    <Input id="conn-branch" required placeholder={m.admin_connectors_github_branch_placeholder()} value={githubConfig.branch} onChange={(e) => setGithubConfig((p) => ({ ...p, branch: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-schedule">{m.admin_connectors_field_schedule()}</Label>
                    <Input id="conn-schedule" placeholder={m.admin_connectors_field_schedule_placeholder()} value={schedule} onChange={(e) => setSchedule(e.target.value)} />
                  </div>
                  {createMutation.error && (
                    <p className="text-sm text-[var(--color-destructive)]">
                      {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                    </p>
                  )}
                  <div className="flex gap-2 pt-1">
                    <Button type="submit" size="sm" disabled={createMutation.isPending}>
                      {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => { setShowAdd(false); setSelectedType(null) }}>{m.admin_connectors_cancel()}</Button>
                  </div>
                </form>
              )}

              {selectedType === 'web_crawler' && (
                <form onSubmit={(e) => { e.preventDefault(); createMutation.mutate() }} className="space-y-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-name">{m.admin_connectors_field_name()}</Label>
                    <Input id="conn-name" required placeholder={m.admin_connectors_field_name_placeholder()} value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-base-url">{m.admin_connectors_webcrawler_base_url()}</Label>
                    <Input id="conn-base-url" type="url" required placeholder={m.admin_connectors_webcrawler_base_url_placeholder()} value={webcrawlerConfig.base_url} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, base_url: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-path-prefix">{m.admin_connectors_webcrawler_path_prefix()}</Label>
                    <Input id="conn-path-prefix" placeholder={m.admin_connectors_webcrawler_path_prefix_placeholder()} value={webcrawlerConfig.path_prefix} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, path_prefix: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-max-pages">{m.admin_connectors_webcrawler_max_pages()}</Label>
                    <Input id="conn-max-pages" type="number" min="1" max="2000" placeholder={m.admin_connectors_webcrawler_max_pages_placeholder()} value={webcrawlerConfig.max_pages} onChange={(e) => setWebcrawlerConfig((p) => ({ ...p, max_pages: e.target.value }))} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="conn-schedule-wc">{m.admin_connectors_field_schedule()}</Label>
                    <Input id="conn-schedule-wc" placeholder={m.admin_connectors_field_schedule_placeholder()} value={schedule} onChange={(e) => setSchedule(e.target.value)} />
                  </div>
                  {createMutation.error && (
                    <p className="text-sm text-[var(--color-destructive)]">
                      {createMutation.error instanceof Error ? createMutation.error.message : m.admin_connectors_error_create_generic()}
                    </p>
                  )}
                  <div className="flex gap-2 pt-1">
                    <Button type="submit" size="sm" disabled={createMutation.isPending}>
                      {createMutation.isPending ? m.admin_connectors_create_submit_loading() : m.admin_connectors_create_submit()}
                    </Button>
                    <Button type="button" size="sm" variant="ghost" onClick={() => { setShowAdd(false); setSelectedType(null) }}>{m.admin_connectors_cancel()}</Button>
                  </div>
                </form>
              )}

              {!selectedType && (
                <div className="flex justify-end">
                  <Button type="button" size="sm" variant="ghost" onClick={() => setShowAdd(false)}>{m.admin_connectors_cancel()}</Button>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
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

// -- Items section (personal KB only) ----------------------------------------

interface PersonalItem {
  id: string
  path: string
  assertion_mode: string | null
  tags: string[]
  created_at: string
}

interface PersonalItemsResponse {
  items: PersonalItem[]
  total: number
  limit: number
  offset: number
}

function ItemsSection({ kbSlug, token }: { kbSlug: string; token: string | undefined }) {
  const queryClient = useQueryClient()
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const { data, isLoading } = useQuery<PersonalItemsResponse>({
    queryKey: ['personal-knowledge', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/knowledge/personal/items`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Items laden mislukt')
      return res.json() as Promise<PersonalItemsResponse>
    },
    enabled: !!token,
  })

  const deleteMutation = useMutation({
    mutationFn: async (artifactId: string) => {
      setDeletingId(artifactId)
      const res = await fetch(`${API_BASE}/api/knowledge/personal/items/${artifactId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['personal-knowledge'] })
    },
    onSettled: () => setDeletingId(null),
  })

  if (isLoading) {
    return <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
  }

  if (!data?.items?.length) {
    return (
      <div className="rounded-lg border border-dashed border-[var(--color-border)] p-8 text-center">
        <List className="mx-auto h-8 w-8 text-[var(--color-muted-foreground)] mb-3" />
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_items_empty_state()}</p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] text-left text-xs uppercase tracking-wide text-[var(--color-muted-foreground)]">
              <th className="pb-2 pr-4 font-medium">{m.knowledge_items_column_title()}</th>
              <th className="pb-2 pr-4 font-medium">{m.knowledge_items_column_type()}</th>
              <th className="pb-2 pr-4 font-medium">{m.knowledge_items_column_saved_at()}</th>
              <th className="pb-2 font-medium">{m.knowledge_items_column_actions()}</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id} className="border-b border-[var(--color-border)] last:border-0">
                <td className="py-2.5 pr-4 text-[var(--color-foreground)]">
                  {item.path.replace(/\.md$/, '')}
                </td>
                <td className="py-2.5 pr-4">
                  {item.assertion_mode ? (
                    <Badge variant="secondary">{item.assertion_mode}</Badge>
                  ) : (
                    <span className="text-[var(--color-muted-foreground)]">-</span>
                  )}
                </td>
                <td className="py-2.5 pr-4 text-[var(--color-muted-foreground)]">
                  {new Date(item.created_at).toLocaleDateString()}
                </td>
                <td className="py-2.5">
                  <DeleteConfirmButton
                    onConfirm={() => deleteMutation.mutate(item.id)}
                    isDeleting={deletingId === item.id}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// -- Members section (owner only) --------------------------------------------

function MembersSection({
  kbSlug,
  token,
  isOwner,
  isPersonal,
}: {
  kbSlug: string
  token: string | undefined
  isOwner: boolean
  isPersonal: boolean
}) {
  const queryClient = useQueryClient()
  const [showInviteUser, setShowInviteUser] = useState(false)
  const [showInviteGroup, setShowInviteGroup] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteGroupId, setInviteGroupId] = useState('')
  const [inviteRole, setInviteRole] = useState('viewer')
  const [confirmingRemoveUser, setConfirmingRemoveUser] = useState<number | null>(null)
  const [confirmingRemoveGroup, setConfirmingRemoveGroup] = useState<number | null>(null)

  const { data: members, isLoading } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Members laden mislukt')
      return res.json() as Promise<MembersResponse>
    },
    enabled: !!token,
  })

  const inviteUserMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members/users`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      })
      if (res.status === 404) throw new Error(m.knowledge_members_invite_not_found())
      if (!res.ok) throw new Error(m.knowledge_members_invite_error())
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setShowInviteUser(false)
      setInviteEmail('')
      setInviteRole('viewer')
    },
  })

  const inviteGroupMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members/groups`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ group_id: Number(inviteGroupId), role: inviteRole }),
      })
      if (!res.ok) throw new Error('Groep toevoegen mislukt')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setShowInviteGroup(false)
      setInviteGroupId('')
      setInviteRole('viewer')
    },
  })

  const removeUserMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members/users/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] }),
  })

  const removeGroupMutation = useMutation({
    mutationFn: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members/groups/${id}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] }),
  })

  if (isPersonal) {
    return (
      <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_members_personal_kb_hint()}</p>
    )
  }

  if (isLoading) {
    return <p className="text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
  }

  const roles = ['viewer', 'contributor', 'owner'] as const

  return (
    <div className="space-y-4">
      {/* Individual users */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">{m.knowledge_members_users_heading()}</p>
          {isOwner && !showInviteUser && (
            <Button size="sm" variant="outline" onClick={() => setShowInviteUser(true)}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              {m.knowledge_members_invite_user()}
            </Button>
          )}
        </div>

        {members?.users && members.users.length > 0 ? (
          <Card>
            <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.knowledge_members_col_member()}</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.knowledge_members_col_role()}</th>
                    {isOwner && <th className="px-4 py-2.5 w-16" />}
                  </tr>
                </thead>
                <tbody>
                  {members.users.map((u, i) => (
                    <tr key={u.id} className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}>
                      <td className="px-4 py-2.5 text-sm text-[var(--color-foreground)]">
                        <span>{u.display_name ?? u.email ?? u.user_id}</span>
                        {u.display_name && u.email && (
                          <span className="block text-xs text-[var(--color-muted-foreground)]">{u.email}</span>
                        )}
                      </td>
                      <td className="px-4 py-2.5">{roleBadge(u.role)}</td>
                      {isOwner && (
                        <td className="px-4 py-2.5">
                          <Tooltip label={m.knowledge_members_remove_button()}>
                            <button
                              onClick={() => setConfirmingRemoveUser(u.id)}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] hover:opacity-70"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </Tooltip>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        ) : (
          !showInviteUser && (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_members_empty_users()}</p>
          )
        )}

        {showInviteUser && (
          <Card className="mt-2">
            <CardContent className="pt-4">
              <form onSubmit={(e) => { e.preventDefault(); inviteUserMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="invite-user-email">{m.knowledge_members_invite_email_label()}</Label>
                  <Input id="invite-user-email" type="email" required placeholder={m.knowledge_members_invite_email_placeholder()} value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="invite-user-role">{m.knowledge_members_role_label()}</Label>
                  <Select id="invite-user-role" value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                    {roles.map((r) => <option key={r} value={r}>{roleBadge(r)}</option>)}
                  </Select>
                </div>
                {inviteUserMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">{String(inviteUserMutation.error)}</p>
                )}
                <div className="flex gap-2">
                  <Button type="submit" size="sm" disabled={inviteUserMutation.isPending}>
                    {inviteUserMutation.isPending ? m.knowledge_members_invite_user_submit_loading() : m.knowledge_members_invite_submit()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setShowInviteUser(false)}>{m.knowledge_members_invite_cancel()}</Button>
                </div>
              </form>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Groups */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">{m.knowledge_members_groups_heading()}</p>
          {isOwner && !showInviteGroup && (
            <Button size="sm" variant="outline" onClick={() => setShowInviteGroup(true)}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              {m.knowledge_members_invite_group()}
            </Button>
          )}
        </div>

        {members?.groups && members.groups.length > 0 ? (
          <Card>
            <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-[var(--color-border)]">
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.knowledge_members_col_member()}</th>
                    <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.knowledge_members_col_role()}</th>
                    {isOwner && <th className="px-4 py-2.5 w-16" />}
                  </tr>
                </thead>
                <tbody>
                  {members.groups.map((g, i) => (
                    <tr key={g.id} className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}>
                      <td className="px-4 py-2.5 font-medium text-[var(--color-foreground)]">{g.group_name}</td>
                      <td className="px-4 py-2.5">{roleBadge(g.role)}</td>
                      {isOwner && (
                        <td className="px-4 py-2.5">
                          <Tooltip label={m.knowledge_members_remove_button()}>
                            <button
                              onClick={() => setConfirmingRemoveGroup(g.id)}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] hover:opacity-70"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </Tooltip>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>
        ) : (
          !showInviteGroup && (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_members_empty_groups()}</p>
          )
        )}

        {showInviteGroup && (
          <Card className="mt-2">
            <CardContent className="pt-4">
              <form onSubmit={(e) => { e.preventDefault(); inviteGroupMutation.mutate() }} className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="invite-group-id">{m.knowledge_members_group_label()}</Label>
                  <Input id="invite-group-id" type="number" required placeholder="Group ID" value={inviteGroupId} onChange={(e) => setInviteGroupId(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="invite-group-role">{m.knowledge_members_role_label()}</Label>
                  <Select id="invite-group-role" value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}>
                    {roles.map((r) => <option key={r} value={r}>{roleBadge(r)}</option>)}
                  </Select>
                </div>
                {inviteGroupMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">{String(inviteGroupMutation.error)}</p>
                )}
                <div className="flex gap-2">
                  <Button type="submit" size="sm" disabled={inviteGroupMutation.isPending}>
                    {inviteGroupMutation.isPending ? m.knowledge_members_invite_group_submit_loading() : m.knowledge_members_invite_submit()}
                  </Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setShowInviteGroup(false)}>{m.knowledge_members_invite_cancel()}</Button>
                </div>
              </form>
            </CardContent>
          </Card>
        )}
      </div>

      {/* Remove confirmations */}
      <AlertDialog open={confirmingRemoveUser !== null} onOpenChange={(open) => { if (!open) setConfirmingRemoveUser(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.knowledge_members_remove_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.knowledge_members_remove_confirm_body()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.knowledge_members_invite_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => { if (confirmingRemoveUser) removeUserMutation.mutate(confirmingRemoveUser); setConfirmingRemoveUser(null) }}
            >
              {m.knowledge_members_remove_button()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmingRemoveGroup !== null} onOpenChange={(open) => { if (!open) setConfirmingRemoveGroup(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.knowledge_members_remove_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.knowledge_members_remove_confirm_body()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.knowledge_members_invite_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => { if (confirmingRemoveGroup) removeGroupMutation.mutate(confirmingRemoveGroup); setConfirmingRemoveGroup(null) }}
            >
              {m.knowledge_members_remove_button()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// -- Taxonomy section --------------------------------------------------------

interface TaxonomyNode {
  id: number
  kb_id: number
  parent_id: number | null
  name: string
  slug: string
  doc_count: number
  sort_order: number
  created_at: string
  created_by: string
}

interface TaxonomyProposal {
  id: number
  kb_id: number
  proposal_type: string
  status: string
  title: string
  payload: Record<string, unknown>
  confidence_score: number | null
  created_at: string
  reviewed_at: string | null
  reviewed_by: string | null
  rejection_reason: string | null
}

function TaxonomyTree({
  nodes,
  parentId,
  depth,
  canEdit,
  canDelete,
  onAddChild,
  onRename,
  onDelete,
}: {
  nodes: TaxonomyNode[]
  parentId: number | null
  depth: number
  canEdit: boolean
  canDelete: boolean
  onAddChild: (parentId: number) => void
  onRename: (node: TaxonomyNode, newName: string) => void
  onDelete: (nodeId: number) => void
}) {
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set())
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null)

  const children = nodes.filter((n) => n.parent_id === parentId).sort((a, b) => a.sort_order - b.sort_order)
  if (children.length === 0) return null

  function toggleExpand(id: number) {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function startRename(node: TaxonomyNode) {
    setEditingId(node.id)
    setEditName(node.name)
  }

  function submitRename(node: TaxonomyNode) {
    if (editName.trim() && editName.trim() !== node.name) {
      onRename(node, editName.trim())
    }
    setEditingId(null)
  }

  const hasChildren = (id: number) => nodes.some((n) => n.parent_id === id)

  return (
    <div>
      {children.map((node) => {
        const isExpanded = expandedIds.has(node.id)
        const hasKids = hasChildren(node.id)
        return (
          <div key={node.id}>
            <div
              className="group flex items-center gap-1 py-1.5 pr-2 rounded hover:bg-[var(--color-secondary)] transition-colors"
              style={{ paddingLeft: depth * 20 + 4 }}
            >
              <button
                type="button"
                onClick={() => toggleExpand(node.id)}
                className="flex h-5 w-5 items-center justify-center shrink-0"
                aria-label={isExpanded ? 'Collapse' : 'Expand'}
              >
                {hasKids ? (
                  isExpanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-muted-foreground)]" />
                ) : (
                  <span className="h-3.5 w-3.5" />
                )}
              </button>

              {editingId === node.id ? (
                <form
                  className="flex items-center gap-1 flex-1"
                  onSubmit={(e) => { e.preventDefault(); submitRename(node) }}
                >
                  <Input
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    className="h-6 text-sm py-0 px-1.5 flex-1"
                    autoFocus
                    onKeyDown={(e) => { if (e.key === 'Escape') setEditingId(null) }}
                  />
                  <button type="submit" className="flex h-5 w-5 items-center justify-center rounded bg-[var(--color-success)] text-white hover:opacity-90">
                    <Check className="h-3 w-3" />
                  </button>
                  <button type="button" onClick={() => setEditingId(null)} className="flex h-5 w-5 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)]">
                    <X className="h-3 w-3" />
                  </button>
                </form>
              ) : (
                <>
                  <span className="text-sm text-[var(--color-foreground)] truncate flex-1">{node.name}</span>
                  <span className="text-xs text-[var(--color-muted-foreground)] tabular-nums shrink-0">
                    {node.doc_count > 0 && m.knowledge_taxonomy_node_doc_count({ count: String(node.doc_count) })}
                  </span>

                  {canEdit && (
                    <div className="hidden group-hover:flex items-center gap-0.5 ml-1">
                      <button
                        type="button"
                        onClick={() => onAddChild(node.id)}
                        aria-label={m.knowledge_taxonomy_node_add_child()}
                        className="flex h-5 w-5 items-center justify-center text-[var(--color-accent)] hover:opacity-70"
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => startRename(node)}
                        aria-label={m.knowledge_taxonomy_node_rename()}
                        className="flex h-5 w-5 items-center justify-center text-[var(--color-warning)] hover:opacity-70"
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      {canDelete && (confirmDeleteId === node.id ? (
                        <div className="flex items-center gap-0.5">
                          <button
                            type="button"
                            onClick={() => { onDelete(node.id); setConfirmDeleteId(null) }}
                            className="flex h-5 w-5 items-center justify-center rounded bg-[var(--color-destructive)] text-white hover:opacity-90"
                          >
                            <Check className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirmDeleteId(null)}
                            className="flex h-5 w-5 items-center justify-center rounded border border-[var(--color-border)] text-[var(--color-muted-foreground)] hover:bg-[var(--color-border)]"
                          >
                            <X className="h-3 w-3" />
                          </button>
                        </div>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteId(node.id)}
                          aria-label={m.knowledge_taxonomy_node_delete()}
                          className="flex h-5 w-5 items-center justify-center text-[var(--color-destructive)] hover:opacity-70"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>

            {hasKids && isExpanded && (
              <TaxonomyTree
                nodes={nodes}
                parentId={node.id}
                depth={depth + 1}
                canEdit={canEdit}
                canDelete={canDelete}
                onAddChild={onAddChild}
                onRename={onRename}
                onDelete={onDelete}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}

function TaxonomySection({
  kbSlug,
  token,
  canEdit,
  canDelete,
}: {
  kbSlug: string
  token: string | undefined
  canEdit: boolean
  canDelete: boolean
}) {
  const queryClient = useQueryClient()
  const [showAddRoot, setShowAddRoot] = useState(false)
  const [addParentId, setAddParentId] = useState<number | null>(null)
  const [newNodeName, setNewNodeName] = useState('')
  const [rejectingProposalId, setRejectingProposalId] = useState<number | null>(null)
  const [rejectReason, setRejectReason] = useState('')

  const nodesQuery = useQuery<{ nodes: TaxonomyNode[] }>({
    queryKey: ['taxonomy-nodes', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        taxonomyLogger.warn('Taxonomy nodes fetch failed', { slug: kbSlug, status: res.status })
        throw new Error(m.knowledge_taxonomy_error_fetch())
      }
      return res.json() as Promise<{ nodes: TaxonomyNode[] }>
    },
    enabled: !!token,
  })

  const proposalsQuery = useQuery<{ proposals: TaxonomyProposal[] }>({
    queryKey: ['taxonomy-proposals', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals?status=pending`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        taxonomyLogger.warn('Taxonomy proposals fetch failed', { slug: kbSlug, status: res.status })
        throw new Error(m.knowledge_taxonomy_error_fetch())
      }
      return res.json() as Promise<{ proposals: TaxonomyProposal[] }>
    },
    enabled: !!token,
  })

  const createNodeMutation = useMutation({
    mutationFn: async ({ name, parentId }: { name: string; parentId: number | null }) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, parent_id: parentId }),
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_create())
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
      setNewNodeName('')
      setShowAddRoot(false)
      setAddParentId(null)
    },
  })

  const renameNodeMutation = useMutation({
    mutationFn: async ({ nodeId, name }: { nodeId: number; name: string }) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_rename())
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })

  const deleteNodeMutation = useMutation({
    mutationFn: async (nodeId: number) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/nodes/${nodeId}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_delete())
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] }),
  })

  const approveMutation = useMutation({
    mutationFn: async (proposalId: number) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/approve`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_approve())
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-nodes', kbSlug] })
    },
  })

  const rejectMutation = useMutation({
    mutationFn: async ({ proposalId, reason }: { proposalId: number; reason: string }) => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals/${proposalId}/reject`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason }),
      })
      if (!res.ok) throw new Error(m.knowledge_taxonomy_error_reject())
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['taxonomy-proposals', kbSlug] })
      setRejectingProposalId(null)
      setRejectReason('')
    },
  })

  function handleAddChild(parentId: number) {
    setAddParentId(parentId)
    setShowAddRoot(false)
    setNewNodeName('')
  }

  function handleRename(node: TaxonomyNode, newName: string) {
    renameNodeMutation.mutate({ nodeId: node.id, name: newName })
  }

  const nodes = nodesQuery.data?.nodes ?? []
  const proposals = proposalsQuery.data?.proposals ?? []
  const isAddingChild = addParentId !== null

  const proposalTypeBadge: Record<string, { label: () => string; variant: 'accent' | 'success' | 'secondary' | 'destructive' }> = {
    new_node: { label: m.knowledge_taxonomy_proposals_type_new_node, variant: 'accent' },
    merge: { label: m.knowledge_taxonomy_proposals_type_merge, variant: 'secondary' },
    split: { label: m.knowledge_taxonomy_proposals_type_split, variant: 'secondary' },
    rename: { label: m.knowledge_taxonomy_proposals_type_rename, variant: 'accent' },
  }

  return (
    <div className="space-y-8">
      {/* Category tree */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <FolderTree className="h-4 w-4 text-[var(--color-purple-deep)]" />
            <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{m.knowledge_taxonomy_tree_heading()}</h2>
          </div>
          {canEdit && !showAddRoot && !isAddingChild && (
            <Button size="sm" variant="outline" onClick={() => { setShowAddRoot(true); setAddParentId(null) }}>
              <Plus className="h-3.5 w-3.5 mr-1" />
              {m.knowledge_taxonomy_node_add_root()}
            </Button>
          )}
        </div>

        {nodes.length === 0 && !nodesQuery.isLoading && (
          <div className="rounded-lg border border-dashed border-[var(--color-border)] p-6 text-center">
            <FolderTree className="mx-auto h-8 w-8 text-[var(--color-muted-foreground)] mb-2" />
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_taxonomy_tree_empty()}</p>
            <p className="text-xs text-[var(--color-muted-foreground)] mt-1">{m.knowledge_taxonomy_tree_empty_hint()}</p>
          </div>
        )}

        {nodes.length > 0 && (
          <Card>
            <CardContent className="pt-3 pb-3 px-2">
              <TaxonomyTree
                nodes={nodes}
                parentId={null}
                depth={0}
                canEdit={canEdit}
                canDelete={canDelete}
                onAddChild={handleAddChild}
                onRename={handleRename}
                onDelete={(id) => deleteNodeMutation.mutate(id)}
              />
            </CardContent>
          </Card>
        )}

        {nodesQuery.isLoading && (
          <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
            <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
            {m.admin_connectors_loading()}
          </p>
        )}

        {/* Inline add form (root or child) */}
        {(showAddRoot || isAddingChild) && (
          <form
            className="mt-2 flex items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (newNodeName.trim()) {
                createNodeMutation.mutate({ name: newNodeName.trim(), parentId: addParentId })
              }
            }}
          >
            <Input
              value={newNodeName}
              onChange={(e) => setNewNodeName(e.target.value)}
              placeholder={m.knowledge_taxonomy_node_name_placeholder()}
              className="h-8 text-sm max-w-xs"
              autoFocus
            />
            <Button type="submit" size="sm" disabled={createNodeMutation.isPending || !newNodeName.trim()}>
              {m.knowledge_taxonomy_node_add_submit()}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setShowAddRoot(false); setAddParentId(null); setNewNodeName('') }}>
              {m.knowledge_taxonomy_node_add_cancel()}
            </Button>
          </form>
        )}

        {createNodeMutation.error && (
          <p className="text-sm text-[var(--color-destructive)] mt-1">
            {createNodeMutation.error instanceof Error ? createNodeMutation.error.message : m.knowledge_taxonomy_error_create()}
          </p>
        )}
      </div>

      {/* Review queue */}
      <div>
        <div className="flex items-center gap-2 mb-3">
          <BarChart2 className="h-4 w-4 text-[var(--color-purple-deep)]" />
          <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{m.knowledge_taxonomy_proposals_heading()}</h2>
          {proposals.length > 0 && (
            <Badge variant="accent">{String(proposals.length)}</Badge>
          )}
        </div>

        {proposalsQuery.isLoading && (
          <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
            <Loader2 className="inline h-4 w-4 animate-spin mr-1" />
            {m.admin_connectors_loading()}
          </p>
        )}

        {!proposalsQuery.isLoading && proposals.length === 0 && (
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_taxonomy_proposals_empty()}</p>
        )}

        {proposals.length > 0 && (
          <div className="space-y-3">
            {proposals.map((proposal) => {
              const typeInfo = proposalTypeBadge[proposal.proposal_type] ?? { label: () => proposal.proposal_type, variant: 'secondary' as const }
              return (
                <Card key={proposal.id}>
                  <CardContent className="pt-4 pb-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <Badge variant={typeInfo.variant}>{typeInfo.label()}</Badge>
                          {proposal.confidence_score != null && (
                            <span className="text-xs text-[var(--color-muted-foreground)]">
                              {m.knowledge_taxonomy_proposals_col_confidence()}: {Math.round(proposal.confidence_score * 100)}%
                            </span>
                          )}
                        </div>
                        <p className="text-sm font-medium text-[var(--color-foreground)]">{proposal.title}</p>
                        <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                          {new Date(proposal.created_at).toLocaleDateString()}
                        </p>
                      </div>

                      {canEdit && (
                        <div className="flex items-center gap-1.5 shrink-0">
                          {rejectingProposalId === proposal.id ? (
                            <form
                              className="flex items-center gap-1.5"
                              onSubmit={(e) => {
                                e.preventDefault()
                                rejectMutation.mutate({ proposalId: proposal.id, reason: rejectReason })
                              }}
                            >
                              <Input
                                value={rejectReason}
                                onChange={(e) => setRejectReason(e.target.value)}
                                placeholder={m.knowledge_taxonomy_proposals_reject_reason_placeholder()}
                                className="h-7 text-xs w-48"
                                autoFocus
                              />
                              <Button type="submit" size="sm" variant="outline" className="h-7 text-xs px-2" disabled={rejectMutation.isPending}>
                                {m.knowledge_taxonomy_proposals_reject()}
                              </Button>
                              <Button type="button" size="sm" variant="ghost" className="h-7 text-xs px-2" onClick={() => { setRejectingProposalId(null); setRejectReason('') }}>
                                <X className="h-3 w-3" />
                              </Button>
                            </form>
                          ) : (
                            <>
                              <Button
                                size="sm"
                                className="h-7 text-xs px-2.5 bg-[var(--color-success)] text-white hover:opacity-90"
                                onClick={() => approveMutation.mutate(proposal.id)}
                                disabled={approveMutation.isPending}
                              >
                                {m.knowledge_taxonomy_proposals_approve()}
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-7 text-xs px-2.5 text-[var(--color-destructive)] border-[var(--color-destructive)]/30 hover:bg-[var(--color-destructive)]/5"
                                onClick={() => setRejectingProposalId(proposal.id)}
                              >
                                {m.knowledge_taxonomy_proposals_reject()}
                              </Button>
                            </>
                          )}
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// -- Dashboard section card --------------------------------------------------

function DashboardSection({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ElementType
  title: string
  children: React.ReactNode
}) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Icon className="h-4 w-4 text-[var(--color-purple-deep)]" />
        <h2 className="text-sm font-semibold text-[var(--color-purple-deep)]">{title}</h2>
      </div>
      {children}
    </div>
  )
}

// -- Main page ---------------------------------------------------------------

type KBTab = 'overview' | 'connectors' | 'members' | 'items' | 'taxonomy'

function KnowledgeDetailPage() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [activeTab, setActiveTab] = useState<KBTab>('overview')

  const { mutate: deleteKb, isPending: isDeleting } = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Verwijderen mislukt')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge' })
    },
  })

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

  const { data: stats } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/stats`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Stats laden mislukt')
      return res.json() as Promise<KBStats>
    },
    enabled: !!token && !!kb,
  })

  // Determine caller's role for this KB
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Members laden mislukt')
      return res.json() as Promise<MembersResponse>
    },
    enabled: !!token && !!kb,
  })

  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isContributor = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && (u.role === 'owner' || u.role === 'contributor')))
  const isPersonal = kb?.owner_type === 'user'

  const pendingProposalsQuery = useQuery<{ proposals: TaxonomyProposal[] }>({
    queryKey: ['taxonomy-proposals-count', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/taxonomy/proposals?status=pending`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return { proposals: [] }
      return res.json() as Promise<{ proposals: TaxonomyProposal[] }>
    },
    enabled: !!token && !!kb,
  })
  const pendingCount = pendingProposalsQuery.data?.proposals.length ?? 0

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

  const docsLabel =
    stats?.docs_count == null
      ? m.knowledge_detail_docs_none()
      : stats.docs_count === 1
        ? m.knowledge_detail_docs_count_one()
        : m.knowledge_detail_docs_count({ count: String(stats.docs_count) })

  return (
    <div className="p-8 max-w-2xl space-y-8">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0 mt-0.5">
          <Brain className="h-5 w-5 text-[var(--color-purple-deep)]" />
        </div>
        <div className="flex-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">{kb.name}</h1>
          {kb.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{kb.description}</p>
          )}
          <div className="flex items-center gap-1.5 mt-1.5 text-xs text-[var(--color-muted-foreground)]">
            {kb.visibility === 'public' ? <Globe className="h-3.5 w-3.5" /> : <Lock className="h-3.5 w-3.5" />}
            <span>{kb.visibility === 'public' ? m.knowledge_page_kb_visibility_public() : m.knowledge_page_kb_visibility_internal()}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isOwner && (
            <Button
              variant="ghost"
              size="sm"
              className="text-[var(--color-destructive)] hover:text-[var(--color-destructive)]"
              onClick={() => setShowDeleteDialog(true)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
          <Link to="/app/knowledge">
            <Button variant="ghost" size="sm">{m.knowledge_new_cancel()}</Button>
          </Link>
        </div>
      </div>

      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.knowledge_detail_delete_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>
              {m.knowledge_detail_delete_confirm_body({ name: kb.name })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.knowledge_new_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteKb()}
              disabled={isDeleting}
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
            >
              {m.knowledge_detail_delete_confirm_action()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Tab bar */}
      <div className="border-b border-[var(--color-border)]">
        <nav className="-mb-px flex gap-6">
          {([
            { id: 'overview', icon: BarChart2, label: m.knowledge_detail_tab_overview() },
            ...(isPersonal ? [{ id: 'items' as KBTab, icon: List, label: m.knowledge_detail_tab_items() }] : []),
            { id: 'connectors', icon: Zap, label: m.knowledge_detail_tab_connectors() },
            { id: 'members', icon: Users, label: m.knowledge_detail_tab_members() },
            { id: 'taxonomy', icon: FolderTree, label: m.knowledge_detail_tab_taxonomy(), badge: pendingCount > 0 ? pendingCount : undefined },
          ] as { id: KBTab; icon: React.ElementType; label: string; badge?: number }[]).map(({ id, icon: Icon, label, badge }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActiveTab(id)}
              className={[
                'flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors',
                activeTab === id
                  ? 'border-[var(--color-accent)] text-[var(--color-purple-deep)]'
                  : 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
              ].join(' ')}
            >
              <Icon className="h-4 w-4" />
              {label}
              {badge != null && <Badge variant="accent" className="ml-1 text-[10px] px-1.5 py-0 min-w-[18px]">{String(badge)}</Badge>}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab panels */}
      {activeTab === 'overview' && (
        <div className="space-y-8">
          <DashboardSection icon={BookOpen} title={m.knowledge_detail_section_docs()}>
            {!kb.docs_enabled ? (
              <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_detail_docs_not_enabled()}</p>
            ) : (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                  <span className="text-sm text-[var(--color-foreground)]">{docsLabel}</span>
                </div>
                {kb.gitea_repo_slug && (
                  <Link to="/app/docs/$kbSlug" params={{ kbSlug: kb.slug }}>
                    <Button variant="outline" size="sm">{m.knowledge_detail_view_in_docs()}</Button>
                  </Link>
                )}
              </div>
            )}
          </DashboardSection>

          <DashboardSection icon={BarChart2} title={m.knowledge_detail_section_stats()}>
            <div className="flex gap-8">
              <div>
                <p className="text-xs text-[var(--color-muted-foreground)] uppercase tracking-wide mb-1">Volume</p>
                <p className="text-sm font-medium text-[var(--color-foreground)]">
                  {stats?.volume != null
                    ? m.knowledge_detail_volume({ count: String(stats.volume) })
                    : m.knowledge_detail_volume_unknown()}
                </p>
              </div>
              <div>
                <p className="text-xs text-[var(--color-muted-foreground)] uppercase tracking-wide mb-1">Queries (30d)</p>
                <p className="text-sm font-medium text-[var(--color-foreground)]">
                  {stats?.usage_last_30d != null
                    ? m.knowledge_detail_usage({ count: String(stats.usage_last_30d) })
                    : m.knowledge_detail_usage_unknown()}
                </p>
              </div>
            </div>
          </DashboardSection>
        </div>
      )}

      {activeTab === 'items' && isPersonal && (
        <ItemsSection kbSlug={kbSlug} token={token} />
      )}

      {activeTab === 'connectors' && (
        <ConnectorsSection kbSlug={kbSlug} token={token} isOwner={isOwner} />
      )}

      {activeTab === 'members' && (
        <MembersSection kbSlug={kbSlug} token={token} isOwner={isOwner} isPersonal={isPersonal} />
      )}

      {activeTab === 'taxonomy' && (
        <TaxonomySection
          kbSlug={kbSlug}
          token={token}
          canEdit={isContributor}
          canDelete={isOwner}
        />
      )}
    </div>
  )
}
