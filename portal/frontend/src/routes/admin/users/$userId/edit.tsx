import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2, X } from 'lucide-react'
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

interface User {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  preferred_language: Language
  status: UserStatus
  invite_pending: boolean
}

interface Group {
  id: number
  name: string
}

function EditUserPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { userId } = Route.useParams()

  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [language, setLanguage] = useState<Language>('nl')
  // Local staged group state: the desired final membership
  const [memberGroupIds, setMemberGroupIds] = useState<Set<number>>(new Set())
  const [groupsInitialized, setGroupsInitialized] = useState(false)
  const [selectedGroupId, setSelectedGroupId] = useState('')
  const [saving, setSaving] = useState(false)

  const { data: usersData } = useQuery({
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

  const user = usersData?.users.find((u) => u.zitadel_user_id === userId)

  useEffect(() => {
    if (user) {
      setFirstName(user.first_name)
      setLastName(user.last_name)
      setLanguage(user.preferred_language)
    }
  }, [user])

  const { data: userGroupsData } = useQuery({
    queryKey: ['admin-user-groups', userId, token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/users/${userId}/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return { groups: [] as Group[] }
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
  })

  const { data: allGroupsData } = useQuery({
    queryKey: ['admin-groups', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/admin/groups`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) return { groups: [] as Group[] }
      return res.json() as Promise<{ groups: Group[] }>
    },
    enabled: !!token,
  })

  const allGroups = allGroupsData?.groups ?? []

  // Initialize staged group state once server data arrives
  useEffect(() => {
    if (!groupsInitialized && userGroupsData) {
      setMemberGroupIds(new Set(userGroupsData.groups.map((g) => g.id)))
      setGroupsInitialized(true)
    }
  }, [userGroupsData, groupsInitialized])

  const currentGroups = allGroups.filter((g) => memberGroupIds.has(g.id))
  const availableGroups = allGroups.filter((g) => !memberGroupIds.has(g.id))

  function stageAdd(groupId: number) {
    setMemberGroupIds((prev) => new Set([...prev, groupId]))
    setSelectedGroupId('')
  }

  function stageRemove(groupId: number) {
    setMemberGroupIds((prev) => {
      const next = new Set(prev)
      next.delete(groupId)
      return next
    })
  }

  const originalGroupIds = new Set(userGroupsData?.groups.map((g) => g.id) ?? [])

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setSaving(true)
    try {
      const groupsToAdd = [...memberGroupIds].filter((id) => !originalGroupIds.has(id))
      const groupsToRemove = [...originalGroupIds].filter((id) => !memberGroupIds.has(id))

      await Promise.all([
        fetch(`${API_BASE}/api/admin/users/${userId}`, {
          method: 'PATCH',
          headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
          body: JSON.stringify({ first_name: firstName, last_name: lastName, preferred_language: language }),
        }).then((r) => { if (!r.ok) throw new Error(m.admin_users_error_edit({ status: String(r.status) })) }),
        ...groupsToAdd.map((id) =>
          fetch(`${API_BASE}/api/admin/groups/${id}/members`, {
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ zitadel_user_id: userId }),
          }).then((r) => { if (!r.ok) throw new Error(`Groep toevoegen mislukt (${r.status})`) }),
        ),
        ...groupsToRemove.map((id) =>
          fetch(`${API_BASE}/api/admin/groups/${id}/members/${userId}`, {
            method: 'DELETE',
            headers: { Authorization: `Bearer ${token}` },
          }).then((r) => { if (!r.ok) throw new Error(`Groep verwijderen mislukt (${r.status})`) }),
        ),
      ])

      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-user-groups', userId] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-members'] })
      void queryClient.invalidateQueries({ queryKey: ['admin-group-memberships'] })
      void navigate({ to: '/admin/users' })
    } catch (err) {
      toast.error(err instanceof Error ? err.message : m.admin_users_error_edit_generic())
    } finally {
      setSaving(false)
    }
  }

  const suspendMutation = useSuspendUser()
  const reactivateMutation = useReactivateUser()
  const offboardMutation = useOffboardUser()

  return (
    <div className="p-8 max-w-lg">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.admin_users_edit_heading()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={() => void navigate({ to: '/admin/users' })}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <form onSubmit={(e) => void handleSave(e)} className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="first-name">{m.admin_users_field_first_name()}</Label>
                <Input
                  id="first-name"
                  type="text"
                  required
                  value={firstName}
                  onChange={(e) => setFirstName(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="last-name">{m.admin_users_field_last_name()}</Label>
                <Input
                  id="last-name"
                  type="text"
                  required
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="language">{m.admin_users_field_language()}</Label>
              <Select
                id="language"
                value={language}
                onChange={(e) => setLanguage(e.target.value as Language)}
              >
                <option value="nl">{m.admin_users_language_nl()}</option>
                <option value="en">{m.admin_users_language_en()}</option>
              </Select>
            </div>

            <div className="border-t pt-4">
              <Label className="mb-2 block">{m.admin_users_groups_title()}</Label>

              {currentGroups.length === 0 ? (
                <p className="text-sm text-muted-foreground mb-3">{m.admin_users_groups_empty()}</p>
              ) : (
                <div className="space-y-1 mb-3">
                  {currentGroups.map((group) => (
                    <div key={group.id} className="flex items-center justify-between py-1">
                      <span className="text-sm text-[var(--color-purple-deep)]">
                        {group.name}
                      </span>
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button type="button" variant="ghost" size="sm">
                            <X className="h-4 w-4 text-muted-foreground" />
                          </Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>
                              {m.admin_users_confirm_remove_group_title()}
                            </AlertDialogTitle>
                            <AlertDialogDescription>
                              {m.admin_users_confirm_remove_group_description({ name: group.name })}
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>{m.admin_users_cancel()}</AlertDialogCancel>
                            <AlertDialogAction
                              className="bg-[var(--color-destructive)] text-white hover:opacity-90"
                              onClick={() => stageRemove(group.id)}
                            >
                              {m.admin_users_confirm_remove_group_title()}
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
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
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={!selectedGroupId}
                    onClick={() => stageAdd(Number(selectedGroupId))}
                  >
                    {m.admin_users_groups_add()}
                  </Button>
                </div>
              )}
            </div>

            <div className="pt-2 flex gap-2">
              <Button type="submit" disabled={saving || !user}>
                {saving && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                {saving ? m.admin_users_edit_submit_loading() : m.admin_users_edit_submit()}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => void navigate({ to: '/admin/users' })}
              >
                {m.admin_users_cancel()}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      {/* Lifecycle actions — destructive, separate from save */}
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
