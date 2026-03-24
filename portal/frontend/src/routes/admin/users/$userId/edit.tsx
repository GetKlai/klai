import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2, Trash2 } from 'lucide-react'
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
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { toast } from 'sonner'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { useSuspendUser, useReactivateUser, useOffboardUser } from '@/hooks/useUserLifecycle'

export const Route = createFileRoute('/admin/users/$userId/edit')({
  component: EditUserPage,
})

type Language = 'nl' | 'en'
type UserStatus = 'active' | 'suspended' | 'offboarded'

interface EditForm {
  first_name: string
  last_name: string
  preferred_language: Language
}

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  preferred_language: Language
  status: UserStatus
  invite_pending: boolean
}

interface UserGroup {
  id: number
  name: string
}

interface AllGroup {
  id: number
  name: string
}

function EditUserPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { userId } = Route.useParams()

  const [form, setForm] = useState<EditForm>({
    first_name: '',
    last_name: '',
    preferred_language: 'nl',
  })
  const [selectedGroupId, setSelectedGroupId] = useState('')

  const { data } = useQuery({
    queryKey: ['admin-users', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/users`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(m.admin_users_error_fetch({ status: String(res.status) }))
      return res.json() as Promise<{ users: User[] }>
    },
    enabled: !!token,
  })

  const user = data?.users.find((u) => u.zitadel_user_id === userId)

  useEffect(() => {
    if (user) {
      setForm({
        first_name: user.first_name,
        last_name: user.last_name,
        preferred_language: user.preferred_language,
      })
    }
  }, [user])

  const editMutation = useMutation({
    mutationFn: async (formData: EditForm) => {
      const res = await fetch(`${API_BASE}/api/admin/users/${userId}`, {
        method: 'PATCH',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData),
      })
      if (!res.ok) throw new Error(m.admin_users_error_edit({ status: String(res.status) }))
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      void navigate({ to: '/admin/users' })
    },
  })

  // --- User groups query ---

  const { data: userGroupsData } = useQuery({
    queryKey: ['admin-user-groups', userId, token],
    queryFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/admin/users/${userId}/groups`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) return { groups: [] as UserGroup[] }
      return res.json() as Promise<{ groups: UserGroup[] }>
    },
    enabled: !!token,
  })

  const { data: allGroupsData } = useQuery({
    queryKey: ['admin-groups'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return { groups: [] as AllGroup[] }
      return res.json() as Promise<{ groups: AllGroup[] }>
    },
    enabled: !!token,
  })

  const userGroups = userGroupsData?.groups ?? []
  const userGroupIds = new Set(userGroups.map((g) => g.id))
  const availableGroups = (allGroupsData?.groups ?? []).filter((g) => !userGroupIds.has(g.id))

  // --- Group membership mutations ---

  const addToGroupMutation = useMutation({
    mutationFn: async (groupId: number) => {
      const res = await fetch(`${API_BASE}/api/admin/groups/${groupId}/members`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ zitadel_user_id: userId }),
      })
      if (!res.ok) throw new Error(`Failed to add to group (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-user-groups', userId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members'] })
      setSelectedGroupId('')
      toast.success(m.admin_users_groups_add_success())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  const removeFromGroupMutation = useMutation({
    mutationFn: async (groupId: number) => {
      const res = await fetch(
        `${API_BASE}/api/admin/groups/${groupId}/members/${userId}`,
        {
          method: 'DELETE',
          headers: { Authorization: `Bearer ${token}` },
        },
      )
      if (!res.ok) throw new Error(`Failed to remove from group (${res.status})`)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-user-groups', userId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members'] })
      toast.success(m.admin_users_groups_remove_success())
    },
    onError: (err: Error) => {
      toast.error(err.message)
    },
  })

  // --- Lifecycle hooks ---

  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const offboardMutation = useOffboardUser()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    editMutation.mutate(form)
  }

  function handleCancel() {
    void navigate({ to: '/admin/users' })
  }

  function handleAddToGroup() {
    if (selectedGroupId) {
      addToGroupMutation.mutate(Number(selectedGroupId))
    }
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_users_edit_heading()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={handleCancel}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="first-name">{m.admin_users_field_first_name()}</Label>
                <Input
                  id="first-name"
                  type="text"
                  required
                  value={form.first_name}
                  onChange={(e) => setForm((prev) => ({ ...prev, first_name: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="last-name">{m.admin_users_field_last_name()}</Label>
                <Input
                  id="last-name"
                  type="text"
                  required
                  value={form.last_name}
                  onChange={(e) => setForm((prev) => ({ ...prev, last_name: e.target.value }))}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="language">{m.admin_users_field_language()}</Label>
              <Select
                id="language"
                value={form.preferred_language}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, preferred_language: e.target.value as Language }))
                }
              >
                <option value="nl">{m.admin_users_language_nl()}</option>
                <option value="en">{m.admin_users_language_en()}</option>
              </Select>
            </div>

            {editMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">
                {editMutation.error instanceof Error
                  ? editMutation.error.message
                  : m.admin_users_error_edit_generic()}
              </p>
            )}

            <div className="pt-2">
              <Button type="submit" disabled={editMutation.isPending || !user}>
                {editMutation.isPending
                  ? m.admin_users_edit_submit_loading()
                  : m.admin_users_edit_submit()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Groups section */}
      <Card className="mt-6">
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-lg">{m.admin_users_groups_title()}</h2>
          </div>

          {userGroups.length === 0 ? (
            <p className="text-sm text-muted-foreground mb-4">
              {m.admin_users_groups_empty()}
            </p>
          ) : (
            <div className="space-y-2 mb-4">
              {userGroups.map((group) => (
                <div key={group.id} className="flex items-center justify-between py-1">
                  <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                    {group.name}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={removeFromGroupMutation.isPending && removeFromGroupMutation.variables === group.id}
                    onClick={() => removeFromGroupMutation.mutate(group.id)}
                  >
                    {removeFromGroupMutation.isPending && removeFromGroupMutation.variables === group.id ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4 text-[var(--color-destructive)]" />
                    )}
                  </Button>
                </div>
              ))}
            </div>
          )}

          {availableGroups.length > 0 && (
            <div className="flex gap-2">
              <Select
                value={selectedGroupId}
                onChange={(e) => setSelectedGroupId(e.target.value)}
                className="flex-1"
              >
                <option value="">— {m.admin_users_groups_add()} —</option>
                {availableGroups.map((g) => (
                  <option key={g.id} value={String(g.id)}>
                    {g.name}
                  </option>
                ))}
              </Select>
              <Button
                size="sm"
                disabled={!selectedGroupId || addToGroupMutation.isPending}
                onClick={handleAddToGroup}
              >
                {addToGroupMutation.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : m.admin_users_groups_add()}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Lifecycle action buttons — only show when at least one button will render */}
      {user && (user.status === 'suspended' || (user.status === 'active' && !user.invite_pending)) && (
        <Card className="mt-6">
          <CardContent className="pt-6 flex flex-wrap gap-3">
            {user.status === 'active' && !user.invite_pending && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" disabled={suspendMutation.isPending}>
                    {suspendMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                    {m.admin_users_action_suspend()}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{m.admin_users_confirm_suspend_title()}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {m.admin_users_confirm_suspend_description()}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
                    <AlertDialogAction onClick={() => suspendMutation.mutate(userId)}>
                      {m.admin_users_action_suspend()}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}

            {user.status === 'suspended' && (
              <Button
                variant="outline"
                disabled={reactivateMutation.isPending}
                onClick={() => reactivateMutation.mutate(userId)}
              >
                {reactivateMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {m.admin_users_action_reactivate()}
              </Button>
            )}

            {(user.status === 'active' || user.status === 'suspended') && !user.invite_pending && (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="destructive" disabled={offboardMutation.isPending}>
                    {offboardMutation.isPending && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                    {m.admin_users_action_offboard()}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>{m.admin_users_confirm_offboard_title()}</AlertDialogTitle>
                    <AlertDialogDescription>
                      {m.admin_users_confirm_offboard_description()}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
                    <AlertDialogAction
                      onClick={() => {
                        offboardMutation.mutate(userId, {
                          onSuccess: () => {
                            void navigate({ to: '/admin/users' })
                          },
                        })
                      }}
                    >
                      {m.admin_users_action_offboard()}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
