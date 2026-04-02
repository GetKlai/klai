import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
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

export const Route = createFileRoute('/app/knowledge/$kbSlug/members')({
  component: MembersTab,
})

function MembersTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()

  const [showInviteUser, setShowInviteUser] = useState(false)
  const [showInviteGroup, setShowInviteGroup] = useState(false)
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteGroupId, setInviteGroupId] = useState('')
  const [inviteRole, setInviteRole] = useState('viewer')
  const [confirmingRemoveUser, setConfirmingRemoveUser] = useState<number | null>(null)
  const [confirmingRemoveGroup, setConfirmingRemoveGroup] = useState<number | null>(null)

  // Reuse cached KB data from parent layout
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

  const myUserId = auth.user?.profile?.sub
  const isOwner = !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))
  const isPersonal = kb?.owner_type === 'user'

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
    mutationFn: async () => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/members/groups`, token, {
        method: 'POST',
        body: JSON.stringify({ group_id: Number(inviteGroupId), role: inviteRole }),
      })
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
