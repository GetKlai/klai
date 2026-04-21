import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { Globe, Lock, Users, Search, X } from 'lucide-react'
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
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
  })

  const { data: members, isLoading } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
  })

  // Fetch org groups and users for comboboxes
  const { data: groupsData } = useQuery({
    queryKey: ['app-groups'],
    queryFn: () => apiFetch<{ groups: OrgGroup[] }>('/api/app/groups'),
    enabled: kb?.owner_type === 'org',
  })

  const { data: usersData } = useQuery({
    queryKey: ['app-users'],
    queryFn: () => apiFetch<{ users: OrgUser[] }>('/api/app/users'),
    enabled: kb?.owner_type === 'org',
  })

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isPersonal = kb?.owner_type === 'user'

  // Mutations
  const updateKbMutation = useMutation({
    mutationFn: async (body: { visibility?: string; default_org_role?: string }) => {
      return apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, {
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
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users`, {
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
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups`, {
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
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/users/${id}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] }),
  })

  const removeGroupMutation = useMutation({
    mutationFn: async (id: number) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups/${id}`, {
        method: 'DELETE',
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-members', kbSlug] }),
  })

  // Option A: every collection supports sharing. Previously `user`-owned KBs
  // were hard-blocked here with a hint. Now Access works uniformly.
  void isPersonal

  if (isLoading) {
    return <p className="text-sm text-gray-400">{m.admin_connectors_loading()}</p>
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
    <div className="space-y-6">
      {/* Visibility selector -- owners only */}
      {kb && kb.owner_type === 'org' && isOwner && (
        <div className="space-y-2">
          <h2 className="text-sm font-semibold text-gray-900">
            {m.knowledge_sharing_who_can_access()}
          </h2>
          <div className="flex flex-col gap-2">
            {visibilityOptions.map(({ mode, icon: Icon, label, description }) => (
              <button
                key={mode}
                type="button"
                onClick={() => handleVisibilityChange(mode)}
                className={`flex items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
                  visibilityMode === mode
                    ? 'border-gray-900 bg-gray-50'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <Icon className={`h-4 w-4 mt-0.5 shrink-0 ${
                  visibilityMode === mode ? 'text-gray-900' : 'text-gray-400'
                }`} />
                <div>
                  <span className="text-sm font-medium text-gray-900">
                    {label}
                  </span>
                  <p className="text-xs text-gray-400 mt-0.5">
                    {description}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Contribute toggle -- only for public/org, owners only */}
      {kb && kb.owner_type === 'org' && isOwner && visibilityMode !== 'restricted' && (
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={allowContribute}
            onChange={handleContributeToggle}
            className="mt-1 h-4 w-4 rounded border-gray-200 text-gray-900 focus:ring-gray-400"
          />
          <div>
            <span className="text-sm font-medium text-gray-900">
              {m.knowledge_sharing_contributor_toggle()}
            </span>
            <p className="text-xs text-gray-400 mt-0.5">
              {m.knowledge_sharing_contributor_toggle_description()}
            </p>
          </div>
        </label>
      )}

      {/* Non-owner visibility display */}
      {kb && kb.owner_type === 'org' && !isOwner && (
        <div className="flex items-center gap-2 text-sm text-gray-400">
          {visibilityMode === 'public' && <Globe className="h-4 w-4" />}
          {visibilityMode === 'org' && <Users className="h-4 w-4" />}
          {visibilityMode === 'restricted' && <Lock className="h-4 w-4" />}
          <span>
            {visibilityMode === 'public' && m.knowledge_sharing_visibility_public()}
            {visibilityMode === 'org' && m.knowledge_sharing_visibility_org()}
            {visibilityMode === 'restricted' && m.knowledge_sharing_visibility_restricted()}
          </span>
        </div>
      )}

      {/* Groups -- MemberPicker style */}
      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-gray-900">
          {visibilityMode !== 'restricted' ? m.knowledge_sharing_groups_extra() : m.knowledge_sharing_groups()}
        </h2>
        {/* Group search combobox -- owners only */}
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
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input
              value={groupSearch}
              onChange={(e) => setGroupSearch(e.target.value)}
              placeholder={m.knowledge_sharing_search_group()}
              className="pl-9 rounded-lg border-gray-200"
            />
            {groupFocused && filteredGroups.length > 0 && (
              <div className="absolute z-10 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-md max-h-40 overflow-y-auto">
                {filteredGroups.map((g) => (
                  <button
                    key={g.id}
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      inviteGroupMutation.mutate({ groupId: g.id, role: 'viewer' })
                    }}
                    className="w-full px-3 py-2 text-left text-sm text-gray-900 hover:bg-gray-50 transition-colors"
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
            className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-3 py-2"
          >
            <span className="text-sm text-gray-900">{g.group_name}</span>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">{g.role}</span>
              {isOwner && (
                <button
                  type="button"
                  onClick={() => setConfirmingRemoveGroup(g.id)}
                  className="flex h-6 w-6 items-center justify-center text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        ))}

        {(!members?.groups || members.groups.length === 0) && !isOwner && (
          <p className="text-sm text-gray-400">{m.knowledge_members_empty_groups()}</p>
        )}
      </div>

      {/* Persons -- MemberPicker style */}
      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-gray-900">
          {visibilityMode !== 'restricted' ? m.knowledge_sharing_persons_extra() : m.knowledge_sharing_persons()}
        </h2>
        {/* Person search combobox -- owners only */}
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
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input
              value={userSearch}
              onChange={(e) => setUserSearch(e.target.value)}
              placeholder={m.knowledge_sharing_search_person()}
              className="pl-9 rounded-lg border-gray-200"
            />
            {userFocused && filteredUsers.length > 0 && (
              <div className="absolute z-10 mt-1 w-full rounded-lg border border-gray-200 bg-white shadow-md max-h-40 overflow-y-auto">
                {filteredUsers.map((u) => (
                  <button
                    key={u.zitadel_user_id}
                    type="button"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      inviteUserMutation.mutate({ email: u.email, role: 'viewer' })
                    }}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-gray-50 transition-colors"
                  >
                    <span className="text-gray-900">{u.display_name}</span>
                    <span className="ml-2 text-xs text-gray-400">{u.email}</span>
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
            className="flex items-center justify-between rounded-lg border border-gray-200 bg-white px-3 py-2"
          >
            <div>
              <span className="text-sm text-gray-900">{u.display_name ?? u.email ?? u.user_id}</span>
              {u.display_name && u.email && (
                <span className="ml-2 text-xs text-gray-400">{u.email}</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">{u.role}</span>
              {isOwner && u.user_id !== myUserId && (
                <button
                  type="button"
                  onClick={() => setConfirmingRemoveUser(u.id)}
                  className="flex h-6 w-6 items-center justify-center text-gray-400 hover:text-[var(--color-destructive)] transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        ))}

        {(!members?.users || members.users.length === 0) && !isOwner && (
          <p className="text-sm text-gray-400">{m.knowledge_members_empty_users()}</p>
        )}
      </div>

      <p className="text-xs text-gray-400 italic">
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
