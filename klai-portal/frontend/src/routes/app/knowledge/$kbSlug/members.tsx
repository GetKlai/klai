import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState, type ElementType } from 'react'
import { Globe, Lock, Users } from 'lucide-react'
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
import { RoleGuard } from '@/components/layout/RoleGuard'
import { apiFetch } from '@/lib/apiFetch'
import { useAuth } from '@/lib/auth'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import * as m from '@/paraglide/messages'
import {
  kbMembersQueryKey,
  useInviteGroup,
  useInviteUser,
  useKnowledgeBaseUpdate,
  useRemoveGroup,
  useRemoveUser,
} from './-members-hooks'
import type { KnowledgeBase, MembersResponse } from './-kb-types'
import { InviteSection, type OrgGroup, type OrgUser } from './_components/-InviteSection'
import { MemberRow } from './_components/-MemberRow'

export const Route = createFileRoute('/app/knowledge/$kbSlug/members')({
  component: () => (
    <RoleGuard minRole="kb_manager">
      <MembersTab />
    </RoleGuard>
  ),
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

  const [groupSearch, setGroupSearch] = useState('')
  const [groupFocused, setGroupFocused] = useState(false)
  const [userSearch, setUserSearch] = useState('')
  const [userFocused, setUserFocused] = useState(false)
  const [confirmingRemoveUser, setConfirmingRemoveUser] = useState<number | null>(null)
  const [confirmingRemoveGroup, setConfirmingRemoveGroup] = useState<number | null>(null)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
    enabled: auth.isAuthenticated,
  })

  const { data: members, isLoading } = useQuery<MembersResponse>({
    queryKey: kbMembersQueryKey(kbSlug),
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated,
  })

  const { data: groupsData } = useQuery<{ groups: OrgGroup[] }>({
    queryKey: ['app-groups'],
    queryFn: () => apiFetch<{ groups: OrgGroup[] }>('/api/app/groups'),
    enabled: auth.isAuthenticated && kb?.owner_type === 'org',
  })

  const { data: usersData } = useQuery<{ users: OrgUser[] }>({
    queryKey: ['app-users'],
    queryFn: () => apiFetch<{ users: OrgUser[] }>('/api/app/users'),
    enabled: auth.isAuthenticated && kb?.owner_type === 'org',
  })

  const updateKbMutation = useKnowledgeBaseUpdate(kbSlug)
  const inviteUserMutation = useInviteUser(kbSlug, () => setUserSearch(''))
  const inviteGroupMutation = useInviteGroup(kbSlug, () => setGroupSearch(''))
  const removeUserMutation = useRemoveUser(kbSlug)
  const removeGroupMutation = useRemoveGroup(kbSlug)

  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((user) => user.user_id === myUserId && user.role === 'owner'))
  const isPersonal = kb?.owner_type === 'user'

  if (isPersonal) {
    return (
      <p className="text-sm text-gray-400">{m.knowledge_members_personal_kb_hint()}</p>
    )
  }

  if (isLoading) {
    return <p className="text-sm text-gray-400">{m.admin_connectors_loading()}</p>
  }

  const visibilityMode = kb ? deriveVisibilityMode(kb) : 'restricted'
  const allowContribute = kb?.default_org_role === 'contributor'
  const existingGroupIds = new Set(members?.groups.map((group) => group.group_id) ?? [])
  const filteredGroups = (groupsData?.groups ?? []).filter(
    (group) =>
      !existingGroupIds.has(group.id) &&
      group.name.toLowerCase().includes(groupSearch.toLowerCase()),
  )
  const existingUserIds = new Set(members?.users.map((user) => user.user_id) ?? [])
  const filteredUsers = (usersData?.users ?? []).filter(
    (user) =>
      !existingUserIds.has(user.zitadel_user_id) &&
      (user.display_name.toLowerCase().includes(userSearch.toLowerCase()) ||
        user.email.toLowerCase().includes(userSearch.toLowerCase())),
  )

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
    updateKbMutation.mutate({ default_org_role: allowContribute ? 'viewer' : 'contributor' })
  }

  return (
    <div className="space-y-6">
      {kb && kb.owner_type === 'org' && isOwner && (
        <VisibilitySelector
          visibilityMode={visibilityMode}
          onVisibilityChange={handleVisibilityChange}
        />
      )}

      {kb && kb.owner_type === 'org' && isOwner && visibilityMode !== 'restricted' && (
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={allowContribute}
            onChange={handleContributeToggle}
            className="mt-1 h-4 w-4 rounded border-gray-200 text-gray-400 focus:ring-[var(--color-ring)]"
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

      {kb && kb.owner_type === 'org' && !isOwner && (
        <VisibilitySummary visibilityMode={visibilityMode} />
      )}

      <InviteSection
        kind="groups"
        title={visibilityMode !== 'restricted' ? m.knowledge_sharing_groups_extra() : m.knowledge_sharing_groups()}
        isOwner={isOwner}
        search={groupSearch}
        onSearchChange={setGroupSearch}
        focused={groupFocused}
        onFocusedChange={setGroupFocused}
        options={filteredGroups}
        error={inviteGroupMutation.error}
        onInviteGroup={(groupId) => inviteGroupMutation.mutate({ groupId, role: 'viewer' })}
        emptyReadOnlyMessage={m.knowledge_members_empty_groups()}
        isEmpty={!members?.groups || members.groups.length === 0}
      >
        {members?.groups.map((group) => (
          <MemberRow
            key={group.id}
            kind="group"
            member={group}
            isOwner={isOwner}
            onRemove={setConfirmingRemoveGroup}
          />
        ))}
      </InviteSection>

      <InviteSection
        kind="users"
        title={visibilityMode !== 'restricted' ? m.knowledge_sharing_persons_extra() : m.knowledge_sharing_persons()}
        isOwner={isOwner}
        search={userSearch}
        onSearchChange={setUserSearch}
        focused={userFocused}
        onFocusedChange={setUserFocused}
        options={filteredUsers}
        error={inviteUserMutation.error}
        onInviteUser={(email) => inviteUserMutation.mutate({ email, role: 'viewer' })}
        emptyReadOnlyMessage={m.knowledge_members_empty_users()}
        isEmpty={!members?.users || members.users.length === 0}
      >
        {members?.users.map((user) => (
          <MemberRow
            key={user.id}
            kind="user"
            member={user}
            isOwner={isOwner}
            myUserId={myUserId}
            onRemove={setConfirmingRemoveUser}
          />
        ))}
      </InviteSection>

      <p className="text-xs text-gray-400 italic">
        {m.knowledge_sharing_creator_note({ name: '' })}
      </p>

      <RemoveMemberDialog
        open={confirmingRemoveUser !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmingRemoveUser(null)
        }}
        onConfirm={() => {
          if (confirmingRemoveUser) removeUserMutation.mutate(confirmingRemoveUser)
          setConfirmingRemoveUser(null)
        }}
      />

      <RemoveMemberDialog
        open={confirmingRemoveGroup !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmingRemoveGroup(null)
        }}
        onConfirm={() => {
          if (confirmingRemoveGroup) removeGroupMutation.mutate(confirmingRemoveGroup)
          setConfirmingRemoveGroup(null)
        }}
      />
    </div>
  )
}

interface VisibilityOption {
  mode: VisibilityMode
  icon: ElementType
  label: string
  description: string
}

function getVisibilityOptions(): VisibilityOption[] {
  return [
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
}

interface VisibilitySelectorProps {
  visibilityMode: VisibilityMode
  onVisibilityChange: (mode: VisibilityMode) => void
}

function VisibilitySelector({ visibilityMode, onVisibilityChange }: VisibilitySelectorProps) {
  const visibilityOptions = getVisibilityOptions()

  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-gray-900">
        {m.knowledge_sharing_who_can_access()}
      </h2>
      <div className="flex flex-col gap-2">
        {visibilityOptions.map(({ mode, icon: Icon, label, description }) => (
          <button
            key={mode}
            type="button"
            onClick={() => onVisibilityChange(mode)}
            className={`flex items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
              visibilityMode === mode
                ? 'border-gray-200 bg-black/[0.06]'
                : 'border-gray-200 hover:border-gray-300'
            }`}
          >
            <Icon className="h-4 w-4 mt-0.5 shrink-0 text-gray-400" />
            <div>
              <span className="text-sm font-medium text-gray-900">{label}</span>
              <p className="text-xs text-gray-400 mt-0.5">{description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

function VisibilitySummary({ visibilityMode }: { visibilityMode: VisibilityMode }) {
  const Icon = getVisibilityOptions().find((option) => option.mode === visibilityMode)?.icon ?? Lock
  return (
    <div className="flex items-center gap-2 text-sm text-gray-400">
      <Icon className="h-4 w-4" />
      <span>
        {visibilityMode === 'public' && m.knowledge_sharing_visibility_public()}
        {visibilityMode === 'org' && m.knowledge_sharing_visibility_org()}
        {visibilityMode === 'restricted' && m.knowledge_sharing_visibility_restricted()}
      </span>
    </div>
  )
}

interface RemoveMemberDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

function RemoveMemberDialog({ open, onOpenChange, onConfirm }: RemoveMemberDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{m.knowledge_members_remove_confirm_title()}</AlertDialogTitle>
          <AlertDialogDescription>{m.knowledge_members_remove_confirm_body()}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{m.knowledge_members_invite_cancel()}</AlertDialogCancel>
          <AlertDialogAction
            className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
            onClick={onConfirm}
          >
            {m.knowledge_members_remove_button()}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
