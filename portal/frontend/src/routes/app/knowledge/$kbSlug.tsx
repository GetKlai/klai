import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  Brain, FileText, Globe, Lock, RefreshCw, Trash2, Loader2, Plus,
  BookOpen, Users, BarChart2, Zap,
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
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { queryLogger } from '@/lib/logger'
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

type ConnectorType = 'github' | 'google_drive' | 'notion' | 'ms_docs'

interface GitHubConfig {
  installation_id: string
  repo_owner: string
  repo_name: string
  branch: string
  path_filter: string
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
  switch (status) {
    case 'RUNNING': return <Badge variant="accent">{m.admin_connectors_status_running()}</Badge>
    case 'COMPLETED': return <Badge variant="success">{m.admin_connectors_status_completed()}</Badge>
    case 'FAILED': return <Badge variant="destructive">{m.admin_connectors_status_failed()}</Badge>
    case 'AUTH_ERROR': return <Badge variant="destructive">{m.admin_connectors_status_auth_error()}</Badge>
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
    },
  })

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}/connectors/${id}/sync`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      })
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
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
                  const isRunning = c.last_sync_status === 'RUNNING'
                  return (
                    <tr key={c.id} className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}>
                      <td className="px-4 py-2.5 font-medium text-[var(--color-purple-deep)]">{c.name}</td>
                      <td className="px-4 py-2.5"><SyncStatusBadge status={c.last_sync_status} /></td>
                      {isOwner && (
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1">
                            <Tooltip label={m.admin_connectors_action_sync()}>
                              <button
                                disabled={isSyncing || isRunning}
                                onClick={() => void handleSync(c.id)}
                                aria-label={m.admin_connectors_action_sync()}
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
  const [inviteUserId, setInviteUserId] = useState('')
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
        body: JSON.stringify({ user_id: inviteUserId, role: inviteRole }),
      })
      if (!res.ok) throw new Error('Uitnodigen mislukt')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setShowInviteUser(false)
      setInviteUserId('')
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
                      <td className="px-4 py-2.5 font-mono text-xs text-[var(--color-foreground)]">{u.user_id}</td>
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
                  <Label htmlFor="invite-user-id">{m.knowledge_members_user_id_label()}</Label>
                  <Input id="invite-user-id" required placeholder={m.knowledge_members_user_id_placeholder()} value={inviteUserId} onChange={(e) => setInviteUserId(e.target.value)} />
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
  const isPersonal = kb?.owner_type === 'user'

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
        <Link to="/app/knowledge">
          <Button variant="ghost" size="sm">{m.knowledge_new_cancel()}</Button>
        </Link>
      </div>

      <div className="h-px bg-[var(--color-border)]" />

      {/* Docs section */}
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

      {/* Connectors section */}
      <DashboardSection icon={Zap} title={m.knowledge_detail_section_connectors()}>
        <ConnectorsSection kbSlug={kbSlug} token={token} isOwner={isOwner} />
      </DashboardSection>

      {/* Stats section */}
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

      {/* Members section */}
      <DashboardSection icon={Users} title={m.knowledge_detail_section_members()}>
        <MembersSection kbSlug={kbSlug} token={token} isOwner={isOwner} isPersonal={isPersonal} />
      </DashboardSection>
    </div>
  )
}
