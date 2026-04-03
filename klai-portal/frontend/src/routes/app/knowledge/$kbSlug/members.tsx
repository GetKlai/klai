import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Globe, Lock, Users, Search, X } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
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
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import type { KnowledgeBase, MembersResponse } from './-kb-types'

interface OrgGroup {
  id: number
  name: string
  is_system: boolean
}

interface OrgUser {
  zitadel_user_id: string
  display_name: string
  email: string
}

export const Route = createFileRoute('/app/knowledge/$kbSlug/members')({
  component: MembersTab,
})

type VisibilityMode = 'public' | 'org' | 'restricted'

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

  // Combobox state
  const [groupSearch, setGroupSearch] = useState('')
  const [groupFocused, setGroupFocused] = useState(false)
  const [userSearch, setUserSearch] = useState('')
  const [userFocused, setUserFocused] = useState(false)
  const groupRef = useRef<HTMLDivElement>(null)
  const userRef = useRef<HTMLDivElement>(null)

  // Remove confirmations
  const [confirmingRemoveUser, setConfirmingRemoveUser] = useState<number | null>(null)
  const [confirmingRemoveGroup, setConfirmingRemoveGroup] = useState<number | null>(null)

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

  // Fetch org groups and users for comboboxes
  const { data: groupsData } = useQuery({
    queryKey: ['app-groups'],
    queryFn: () => apiFetch<{ groups: OrgGroup[] }>('/api/app/groups', token),
    enabled: !!token && kb?.owner_type === 'org',
  })

  const { data: usersData } = useQuery({
    queryKey: ['app-users'],
    queryFn: () => apiFetch<{ users: OrgUser[] }>('/api/app/users', token),
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
    mutationFn: async ({ email, role }: { email: string; role: string }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users`, token, {
        method: 'POST',
        body: JSON.stringify({ email, role }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setUserSearch('')
    },
  })

  const inviteGroupMutation = useMutation({
    mutationFn: async ({ groupId, role }: { groupId: number; role: string }) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups`, token, {
        method: 'POST',
        body: JSON.stringify({ group_id: groupId, role }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] })
      setGroupSearch('')
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
      body.default_org_role = ''
    }

    updateKbMutation.mutate(body)
  }

  function handleContributeToggle() {
    if (!kb) return
    const newRole = allowContribute ? 'viewer' : 'contributor'
    updateKbMutation.mutate({ default_org_role: newRole })
  }

  // Filter groups: exclude system groups and already-added groups
  const existingGroupIds = new Set(members?.groups.map((g) => g.group_id) ?? [])
  const filteredGroups = (groupsData?.groups ?? []).filter(
    (g) =>
      !g.is_system &&
      !existingGroupIds.has(g.id) &&
      g.name.toLowerCase().includes(groupSearch.toLowerCase())
  )

  // Filter users: exclude already-added users
  const existingUserIds = new Set(members?.users.map((u) => u.user_id) ?? [])
  const filteredUsers = (usersData?.users ?? []).filter(
    (u) =>
      !existingUserIds.has(u.zitadel_user_id) &&
      (u.display_name.toLowerCase().includes(userSearch.toLowerCase()) ||
        u.email.toLowerCase().includes(userSearch.toLowerCase()))
  )

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

          </CardContent>
        </Card>
      )}

      {/* Contribute toggle — separate card, only for public/org, owners only */}
      {kb && kb.owner_type === 'org' && isOwner && visibilityMode !== 'restricted' && (
        <Card>
          <CardContent className="pt-4">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={allowContribute}
                onChange={handleContributeToggle}
                className="mt-1 h-4 w-4 rounded border-[var(--color-border)] text-[var(--color-accent)] focus:ring-[var(--color-ring)]"
              />
              <div>
                <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                  {m.knowledge_sharing_contributor_toggle()}
                </span>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                  {m.knowledge_sharing_contributor_toggle_description()}
                </p>
              </div>
            </label>
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

      {/* Groups — MemberPicker style */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            {visibilityMode !== 'restricted' ? m.knowledge_sharing_groups_extra() : m.knowledge_sharing_groups()}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {/* Group search combobox — owners only */}
          {isOwner && (
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
                        inviteGroupMutation.mutate({ groupId: g.id, role: 'viewer' })
                      }}
                      className="w-full px-3 py-2 text-left text-sm text-[var(--color-purple-deep)] hover:bg-[var(--color-secondary)] transition-colors"
                    >
                      {g.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {inviteGroupMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">{String(inviteGroupMutation.error)}</p>
          )}

          {/* Existing group members as cards */}
          {members?.groups.map((g) => (
            <div
              key={g.id}
              className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2"
            >
              <span className="text-sm text-[var(--color-purple-deep)]">{g.group_name}</span>
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--color-muted-foreground)]">{g.role}</span>
                {isOwner && (
                  <button
                    type="button"
                    onClick={() => setConfirmingRemoveGroup(g.id)}
                    className="flex h-6 w-6 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))}

          {(!members?.groups || members.groups.length === 0) && !isOwner && (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_members_empty_groups()}</p>
          )}
        </CardContent>
      </Card>

      {/* Persons — MemberPicker style */}
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            {visibilityMode !== 'restricted' ? m.knowledge_sharing_persons_extra() : m.knowledge_sharing_persons()}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {/* Person search combobox — owners only */}
          {isOwner && (
            <div
              className="relative"
              ref={userRef}
              onFocusCapture={() => setUserFocused(true)}
              onBlurCapture={(e) => {
                if (!userRef.current?.contains(e.relatedTarget as Node)) {
                  setUserFocused(false)
                }
              }}
            >
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--color-muted-foreground)]" />
              <Input
                value={userSearch}
                onChange={(e) => setUserSearch(e.target.value)}
                placeholder={m.knowledge_sharing_search_person()}
                className="pl-9"
              />
              {userFocused && filteredUsers.length > 0 && (
                <div className="absolute z-10 mt-1 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] shadow-md max-h-40 overflow-y-auto">
                  {filteredUsers.map((u) => (
                    <button
                      key={u.zitadel_user_id}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        inviteUserMutation.mutate({ email: u.email, role: 'viewer' })
                      }}
                      className="w-full px-3 py-2 text-left text-sm hover:bg-[var(--color-secondary)] transition-colors"
                    >
                      <span className="text-[var(--color-purple-deep)]">{u.display_name}</span>
                      <span className="ml-2 text-xs text-[var(--color-muted-foreground)]">{u.email}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {inviteUserMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">{String(inviteUserMutation.error)}</p>
          )}

          {/* Existing user members as cards */}
          {members?.users.map((u) => (
            <div
              key={u.id}
              className="flex items-center justify-between rounded-lg border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-2"
            >
              <div>
                <span className="text-sm text-[var(--color-purple-deep)]">{u.display_name ?? u.email ?? u.user_id}</span>
                {u.display_name && u.email && (
                  <span className="ml-2 text-xs text-[var(--color-muted-foreground)]">{u.email}</span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--color-muted-foreground)]">{u.role}</span>
                {isOwner && u.user_id !== myUserId && (
                  <button
                    type="button"
                    onClick={() => setConfirmingRemoveUser(u.id)}
                    className="flex h-6 w-6 items-center justify-center text-[var(--color-muted-foreground)] hover:text-[var(--color-destructive)] transition-colors"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            </div>
          ))}

          {(!members?.users || members.users.length === 0) && !isOwner && (
            <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_members_empty_users()}</p>
          )}
        </CardContent>
      </Card>

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
