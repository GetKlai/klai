import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/admin/groups/$groupId/edit')({
  component: EditGroupPage,
})

interface Group {
  id: number
  name: string
  description: string | null
}

interface Member {
  zitadel_user_id: string
}

interface OrgUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
}

function displayName(user: OrgUser): string {
  return `${user.first_name} ${user.last_name}`.trim() || user.email
}

function EditGroupPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { groupId } = Route.useParams()

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [initialized, setInitialized] = useState(false)
  const [memberUserIds, setMemberUserIds] = useState<Set<string>>(new Set())
  const [membersInitialized, setMembersInitialized] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState('')
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // ---------------------------------------------------------------------------
  // Queries
  // ---------------------------------------------------------------------------

  const { data: group, isLoading: groupLoading } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => apiFetch<{ groups: Group[] }>(`/api/admin/groups`, token),
    enabled: !!token,
    select: (data) => data.groups.find((g) => g.id === Number(groupId)),
  })

  const { data: membersData } = useQuery({
    queryKey: ['admin-group-members', groupId],
    queryFn: async () => apiFetch<{ members: Member[] }>(`/api/admin/groups/${groupId}/members`, token),
    enabled: !!token,
  })

  const { data: usersData } = useQuery({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: OrgUser[] }>(`/api/admin/users`, token),
    enabled: !!token,
  })

  useEffect(() => {
    if (group && !initialized) {
      setName(group.name)
      setDescription(group.description ?? '')
      setInitialized(true)
    }
  }, [group, initialized])

  useEffect(() => {
    if (membersData && !membersInitialized) {
      setMemberUserIds(new Set(membersData.members.map((mb) => mb.zitadel_user_id)))
      setMembersInitialized(true)
    }
  }, [membersData, membersInitialized])

  const orgUsers = usersData?.users ?? []
  const originalMemberIds = new Set(membersData?.members.map((mb) => mb.zitadel_user_id) ?? [])

  const currentMembers = orgUsers.filter((u) => memberUserIds.has(u.zitadel_user_id))
  const availableUsers = orgUsers.filter((u) => !memberUserIds.has(u.zitadel_user_id))

  function stageAdd(uid: string) {
    setMemberUserIds((prev) => new Set([...prev, uid]))
    setSelectedUserId('')
  }

  function stageRemove(uid: string) {
    setMemberUserIds((prev) => { const next = new Set(prev); next.delete(uid); return next })
    setConfirmRemoveId(null)
  }

  // ---------------------------------------------------------------------------
  // Save
  // ---------------------------------------------------------------------------

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!group) return
    setSaving(true)
    try {
      const toAdd = [...memberUserIds].filter((id) => !originalMemberIds.has(id))
      const toRemove = [...originalMemberIds].filter((id) => !memberUserIds.has(id))

      await Promise.all([
        apiFetch(`/api/admin/groups/${groupId}`, token, {
          method: 'PATCH',
          body: JSON.stringify({ name: name.trim(), description: description.trim() || null }),
        }),
        ...toAdd.map((uid) =>
          apiFetch(`/api/admin/groups/${groupId}/members`, token, {
            method: 'POST',
            body: JSON.stringify({ zitadel_user_id: uid }),
          }),
        ),
        ...toRemove.map((uid) =>
          apiFetch(`/api/admin/groups/${groupId}/members/${uid}`, token, { method: 'DELETE' }),
        ),
      ])

      void queryClient.invalidateQueries({ queryKey: ['admin-groups'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members', groupId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-memberships'] })
      toast.success(m.admin_groups_success_updated())
      void navigate({ to: '/admin/groups' })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Something went wrong')
    } finally {
      setSaving(false)
    }
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (groupLoading) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          Loading...
        </p>
      </div>
    )
  }

  if (!group) {
    return (
      <div className="p-8">
        <p className="text-sm text-[var(--color-destructive)]">Group not found</p>
      </div>
    )
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-[var(--color-foreground)]">
          {m.admin_groups_edit()}
        </h1>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/groups' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={(e) => void handleSave(e)} className="space-y-4">

            {/* Name */}
            <div className="space-y-1.5">
              <Label htmlFor="group-name">{m.admin_groups_name()}</Label>
              <Input
                id="group-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={m.admin_groups_name_placeholder()}
                required
              />
            </div>

            {/* Description */}
            <div className="space-y-1.5">
              <Label htmlFor="group-description">{m.admin_groups_description()}</Label>
              <textarea
                id="group-description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={m.admin_groups_description_placeholder()}
                rows={3}
                className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
              />
            </div>

            {/* Members */}
            <div className="border-t pt-4">
              <Label className="mb-2 block">{m.admin_groups_members_title()}</Label>

              {currentMembers.length === 0 ? (
                <p className="text-sm text-[var(--color-muted-foreground)] mb-3">
                  {m.admin_groups_members_empty()}
                </p>
              ) : (
                <div className="space-y-1 mb-3">
                  {currentMembers.map((user) => (
                    <div key={user.zitadel_user_id} className="flex items-center justify-between py-1">
                      <div>
                        <span className="text-sm text-[var(--color-foreground)]">
                          {displayName(user)}
                        </span>
                        <span className="text-xs text-[var(--color-muted-foreground)] ml-2">
                          {user.email}
                        </span>
                      </div>
                      <div className="flex items-center gap-1">
                        {confirmRemoveId === user.zitadel_user_id ? (
                          <>
                            <Button
                              type="button"
                              size="sm"
                              className="bg-[var(--color-destructive)] text-white hover:opacity-90"
                              onClick={() => stageRemove(user.zitadel_user_id)}
                            >
                              {m.admin_groups_members_remove()}
                            </Button>
                            <Button
                              type="button"
                              size="sm"
                              variant="ghost"
                              onClick={() => setConfirmRemoveId(null)}
                            >
                              {m.admin_users_cancel()}
                            </Button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setConfirmRemoveId(user.zitadel_user_id)}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {availableUsers.length > 0 && (
                <div className="flex gap-2">
                  <Select
                    value={selectedUserId}
                    onChange={(e) => setSelectedUserId(e.target.value)}
                    className="flex-1"
                  >
                    <option value="">— {m.admin_groups_members_add()} —</option>
                    {availableUsers.map((u) => (
                      <option key={u.zitadel_user_id} value={u.zitadel_user_id}>
                        {displayName(u)}
                      </option>
                    ))}
                  </Select>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!selectedUserId}
                    onClick={() => stageAdd(selectedUserId)}
                  >
                    {m.admin_groups_members_add()}
                  </Button>
                </div>
              )}
            </div>

            {/* Save */}
            <div className="pt-2 flex gap-2">
              <Button type="submit" disabled={saving || !name.trim()}>
                {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {saving ? m.admin_users_edit_submit_loading() : m.admin_users_edit_submit()}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => navigate({ to: '/admin/groups' })}
              >
                {m.admin_users_cancel()}
              </Button>
            </div>

          </form>
        </CardContent>
      </Card>
    </div>
  )
}
