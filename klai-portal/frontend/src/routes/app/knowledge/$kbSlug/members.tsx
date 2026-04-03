import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Plus, Trash2, Globe, Lock, Users, Search } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
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
import { apiFetch } from '@/lib/apiFetch'
import { roleBadge } from './-kb-helpers'
import type { KnowledgeBase, MembersResponse } from './-kb-types'

interface OrgGroup {
  id: number
  name: string
  is_system: boolean
}

export const Route = createFileRoute('/app/knowledge/$kbSlug/members')({
  component: MembersTab,
})

type VisibilityMode = 'public' | 'org' | 'restricted'

/** Derive the 3-way visibility mode from the KB's visibility + default_org_role */
function deriveVisibilityMode(kb: KnowledgeBase): VisibilityMode {
  if (kb.visibility === 'public') return 'public'
  if (kb.default_org_role) return 'org'
  return 'restricted'
}

function MembersTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  const [showInviteUser, setShowInviteUser] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('viewer')
  const [confirmingRemoveUser, setConfirmingRemoveUser] = useState<number | null>(null)
  const [confirmingRemoveGroup, setConfirmingRemoveGroup] = useState<number | null>(null)

  // Group combobox state
  const [groupSearch, setGroupSearch] = useState('')
  const [groupFocused, setGroupFocused] = useState(false)
  const [groupRole, setGroupRole] = useState('viewer')
  const groupRef = useRef<HTMLDivElement>(null)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })

  const { data: members, isLoading } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token,
  })

  // Fetch org groups for combobox
  const { data: groupsData } = useQuery({
    queryKey: ['app-groups'],
    queryFn: () => apiFetch<{ groups: OrgGroup[] }>('/api/app/groups', token),
    enabled: !!token && kb?.owner_type === 'org',
  })

  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isPersonal = kb?.owner_type === 'user'

  // Mutations
  const updateKbMutation = useMutation({
    mutationFn: async (body: { visibility?: string; default_org_role?: string }) => {
      return apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-base', kbSlug] })
    },
  })

  const inviteUserMutation = useMutation({
    mutationFn: async () => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users`, token, {
        method: 'POST',
        body: JSON.stringify({ email: inviteEmail, role: inviteRole }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setShowInviteUser(false)
      setInviteEmail('')
      setInviteRole('viewer')
    },
  })

  const inviteGroupMutation = useMutation({
    mutationFn: async (groupId: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups`, token, {
        method: 'POST',
        body: JSON.stringify({ group_id: groupId, role: groupRole }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setGroupSearch('')
      setGroupRole('viewer')
    },
  })

  const removeUserMutation = useMutation({
    mutationFn: async (id: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users/${id}`, token, {
        method: 'DELETE',
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] }),
  })

  const removeGroupMutation = useMutation({
    mutationFn: async (id: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups/${id}`, token, {
        method: 'DELETE',
      })
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

  const visibilityMode = kb ? deriveVisibilityMode(kb) : 'restricted'
  const allowContribute = kb?.default_org_role === 'contributor'

  function handleVisibilityChange(mode: VisibilityMode) {
    if (!kb) return
    const body: { visibility?: string; default_org_role?: string } = {}

    if (mode === 'public') {
      body.visibility = 'public'
      body.default_org_role = allowContribute ? 'contributor' : 'viewer'
    } else if (mode === 'org') {
      body.visibility = 'internal'
      body.default_org_role = allowContribute ? 'contributor' : 'viewer'
    } else {
      body.visibility = 'internal'
      body.default_org_role = '' // clears to null on backend
    }

    updateKbMutation.mutate(body)
  }

  function handleContributeToggle() {
    if (!kb) return
    const newRole = allowContribute ? 'viewer' : 'contributor'
    updateKbMutation.mutate({ default_org_role: newRole })
  }

  // Filter groups for combobox: exclude system groups and already-added groups
  const existingGroupIds = new Set(members?.groups.map((g) => g.group_id) ?? [])
  const filteredGroups = (groupsData?.groups ?? []).filter(
    (g) =>
      !g.is_system &&
      !existingGroupIds.has(g.id) &&
      g.name.toLowerCase().includes(groupSearch.toLowerCase())
  )

  const roles = ['viewer', 'contributor', 'owner'] as const

  const visibilityOptions: { mode: VisibilityMode; icon: React.ElementType; label: string; description: string }[] = [
    {
      mode: 'public',
      icon: Globe,
      label: m.knowledge_sharing_visibility_public(),
      description: m.knowledge_sharing_visibility_public_description(),
    },
    {
      mode: 'org',
      icon: Users,
      label: m.knowledge_sharing_visibility_org(),
      description: m.knowledge_sharing_visibility_org_description(),
    },
    {
      mode: 'restricted',
      icon: Lock,
      label: m.knowledge_sharing_visibility_restricted(),
      description: m.knowledge_sharing_visibility_restricted_description(),
    },
  ]

  return (
    <div className="space-y-4">
      {/* Visibility selector — owners only */}
      {kb && kb.owner_type === 'org' && isOwner && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">{m.knowledge_sharing_who_can_access()}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2">
              {visibilityOptions.map(({ mode, icon: Icon, label, description }) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => handleVisibilityChange(mode)}
                  className={`flex items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
                    visibilityMode === mode
                      ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5'
                      : 'border-[var(--color-border)] hover:border-[var(--color-accent)]/50'
                  }`}
                >
                  <Icon className={`h-4 w-4 mt-0.5 shrink-0 ${
                    visibilityMode === mode ? 'text-[var(--color-accent)]' : 'text-[var(--color-muted-foreground)]'
                  }`} />
                  <div>
                    <span className={`text-sm font-medium ${
                      visibilityMode === mode ? 'text-[var(--color-purple-deep)]' : 'text-[var(--color-foreground)]'
                    }`}>
                      {label}
                    </span>
                    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                      {description}
                    </p>
                  </div>
                </button>
              ))}
            </div>

            {/* Contribute toggle — only for public/org */}
            {visibilityMode !== 'restricted' && (
              <label className="flex items-center gap-3 rounded-lg border border-[var(--color-border)] p-3 cursor-pointer hover:border-[var(--color-accent)]/50 transition-colors">
                <input
                  type="checkbox"
                  checked={allowContribute}
                  onChange={handleContributeToggle}
                  className="h-4 w-4 rounded border-[var(--color-border)] text-[var(--color-accent)] focus:ring-[var(--color-ring)]"
                />
                <div>
                  <span className="text-sm font-medium text-[var(--color-foreground)]">
                    {m.knowledge_sharing_contributor_toggle()}
                  </span>
                  <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                    {m.knowledge_sharing_contributor_toggle_description()}
                  </p>
                </div>
              </label>
            )}
          </CardContent>
        </Card>
      )}

      {/* Non-owner visibility display */}
      {kb && kb.owner_type === 'org' && !isOwner && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex items-center gap-2 text-sm text-[var(--color-muted-foreground)]">
              {visibilityMode === 'public' && <Globe className="h-4 w-4" />}
              {visibilityMode === 'org' && <Users className="h-4 w-4" />}
              {visibilityMode === 'restricted' && <Lock className="h-4 w-4" />}
              <span>
                {visibilityMode === 'public' && m.knowledge_sharing_visibility_public()}
                {visibilityMode === 'org' && m.knowledge_sharing_visibility_org()}
                {visibilityMode === 'restricted' && m.knowledge_sharing_visibility_restricted()}
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Individual users */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">
            {visibilityMode !== 'restricted' ? m.knowledge_sharing_persons_extra() : m.knowledge_members_users_heading()}
          </p>
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
          <p className="text-sm font-medium text-[var(--color-purple-deep)]">
            {visibilityMode !== 'restricted' ? m.knowledge_sharing_groups_extra() : m.knowledge_members_groups_heading()}
          </p>
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
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_members_empty_groups()}</p>
        )}

        {/* Group combobox — owners only */}
        {isOwner && (
          <Card className="mt-2">
            <CardContent className="pt-4">
              <div className="space-y-3">
                <div className="flex items-end gap-3">
                  <div className="flex-1 space-y-1.5">
                    <Label>{m.knowledge_sharing_groups()}</Label>
                    <div
                      className="relative"
                      ref={groupRef}
                      onFocusCapture={() => setGroupFocused(true)}
                      onBlurCapture={(e) => {
                        if (!groupRef.current?.contains(e.relatedTarget as Node)) {
                          setGroupFocused(false)
                        }
                      }}
                    >
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
                      <Input
                        value={groupSearch}
                        onChange={(e) => setGroupSearch(e.target.value)}
                        placeholder={m.knowledge_sharing_search_group()}
                        className="pl-9"
                      />
                      {groupFocused && filteredGroups.length > 0 && (
                        <div className="absolute z-10 mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
                          {filteredGroups.map((g) => (
                            <button
                              key={g.id}
                              type="button"
                              onMouseDown={(e) => e.preventDefault()}
                              onClick={() => {
                                inviteGroupMutation.mutate(g.id)
                              }}
                              className="w-full px-3 py-2 text-left text-sm text-[var(--color-purple-deep)] hover:bg-[var(--color-secondary)] transition-colors"
                            >
                              {g.name}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label>{m.knowledge_members_role_label()}</Label>
                    <Select value={groupRole} onChange={(e) => setGroupRole(e.target.value)}>
                      {roles.map((r) => <option key={r} value={r}>{roleBadge(r)}</option>)}
                    </Select>
                  </div>
                </div>
                {inviteGroupMutation.error && (
                  <p className="text-sm text-[var(--color-destructive)]">{String(inviteGroupMutation.error)}</p>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>

      <p className="text-xs text-[var(--color-muted-foreground)] italic">
        {m.knowledge_sharing_creator_note({ name: '' })}
      </p>

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
