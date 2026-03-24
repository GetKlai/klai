import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
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

interface EffectiveProduct {
  product: string
  source: 'direct' | 'group'
  source_name?: string
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

  // --- Effective products query ---

  const { data: effectiveProductsData } = useQuery({
    queryKey: ['admin-user-effective-products', userId, token],
    queryFn: async () => {
      const res = await fetch(
        `${API_BASE}/api/admin/users/${userId}/effective-products`,
        { headers: { Authorization: `Bearer ${token}` } },
      )
      if (!res.ok) return { products: [] as EffectiveProduct[] }
      return res.json() as Promise<{ products: EffectiveProduct[] }>
    },
    enabled: !!token,
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

      {/* Effective products section */}
      <Card className="mt-6">
        <CardContent className="pt-6">
          <h2 className="font-semibold text-lg mb-4">
            {m.admin_users_effective_products_title()}
          </h2>
          {(effectiveProductsData?.products ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {m.admin_users_effective_products_empty()}
            </p>
          ) : (
            <div className="space-y-3">
              {(effectiveProductsData?.products ?? []).map((ep) => (
                <div key={ep.product} className="flex items-center justify-between">
                  <span className="text-sm font-medium text-[var(--color-purple-deep)]">
                    {ep.product}
                  </span>
                  <Badge variant="secondary" className="text-xs">
                    {ep.source === 'direct'
                      ? m.admin_users_effective_products_source_direct()
                      : m.admin_users_effective_products_source_group({ group: ep.source_name ?? '' })}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Lifecycle action buttons */}
      {user && (
        <Card className="mt-6">
          <CardContent className="pt-6 flex flex-wrap gap-3">
            {/* Suspend: show if active and not invite_pending */}
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
                    <AlertDialogAction
                      onClick={() => suspendMutation.mutate(userId)}
                    >
                      {m.admin_users_action_suspend()}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            )}

            {/* Reactivate: show if suspended */}
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

            {/* Offboard: show if active or suspended, and not invite_pending */}
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
