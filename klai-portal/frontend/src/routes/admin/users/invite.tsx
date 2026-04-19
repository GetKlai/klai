import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/admin/users/invite')({
  component: InviteUserPage,
})

type Role = 'admin' | 'member'
type Language = 'nl' | 'en'

interface InviteForm {
  first_name: string
  last_name: string
  email: string
  role: Role
  preferred_language: Language
}

interface OrgSettings {
  name: string
  default_language: Language
}

function InviteUserPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const { data: orgSettings } = useQuery({
    queryKey: ['admin-org-settings'],
    queryFn: async () => {
      try {
        return await apiFetch<OrgSettings>(`/api/admin/settings`)
      } catch {
        return null
      }
    },
    enabled: auth.isAuthenticated,
  })

  const defaultLanguage: Language = orgSettings?.default_language ?? 'nl'

  const [form, setForm] = useState<InviteForm>({
    first_name: '',
    last_name: '',
    email: '',
    role: 'member',
    preferred_language: defaultLanguage,
  })

  const inviteMutation = useMutation({
    mutationFn: async (data: InviteForm) => {
      await apiFetch(`/api/admin/users/invite`, {
        method: 'POST',
        body: JSON.stringify(data),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
      void navigate({ to: '/admin/users' })
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    inviteMutation.mutate(form)
  }

  function handleCancel() {
    void navigate({ to: '/admin/users' })
  }

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.admin_users_invite_button()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={handleCancel}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <form id="invite-form" onSubmit={handleSubmit} className="space-y-4">
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
          <Label htmlFor="email">{m.admin_users_field_email()}</Label>
          <Input
            id="email"
            type="email"
            required
            value={form.email}
            onChange={(e) => setForm((prev) => ({ ...prev, email: e.target.value }))}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="role">{m.admin_users_field_role()}</Label>
            <Select
              id="role"
              value={form.role}
              onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value as Role }))}
            >
              <option value="member">{m.admin_users_role_member()}</option>
              <option value="admin">{m.admin_users_role_admin()}</option>
            </Select>
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
        </div>

        {inviteMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {inviteMutation.error instanceof Error
              ? inviteMutation.error.message
              : m.admin_users_error_invite_generic()}
          </p>
        )}

        <div className="pt-2">
          <Button type="submit" disabled={inviteMutation.isPending}>
            {inviteMutation.isPending
              ? m.admin_users_invite_submit_loading()
              : m.admin_users_invite_submit()}
          </Button>
        </div>
      </form>
    </div>
  )
}
