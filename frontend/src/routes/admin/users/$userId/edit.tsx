import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/admin/users/$userId/edit')({
  component: EditUserPage,
})

type Language = 'nl' | 'en'

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
    </div>
  )
}
