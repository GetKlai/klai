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
import { PROFILE_LADDER, type ProfileRole } from '@/lib/profiles'
import { cleanErrorMessage } from '../_components/errors'

export const Route = createFileRoute('/admin/users/invite')({
  component: InviteUserPage,
})

type Language = 'nl' | 'en'

// SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 — per-user account type
// (billing tier), DERIVED from Profile. Admin no longer selects this
// directly; the FE shows a read-only badge that updates when the
// Profile dropdown changes. ``viewer`` is gone — getklai.com/pricing
// has only Klai Chat and Klai Chat + Knowledge.
type AccountType = 'chat' | 'knowledge'

const ACCOUNT_PRICE_MONTHLY: Record<AccountType, number> = {
  chat: 28,
  knowledge: 68,
}

// v0.5.0: Profile derives account type. Mirrors
// ``klai-portal/backend/app/core/seats.py::DEFAULT_SEAT_FOR_ROLE``.
// This is the single canonical mapping on the FE side; the server
// runs the equivalent ``suggest_seat(role)`` regardless of what the
// FE sends, so this client-side derivation is just for the read-only
// badge that the admin sees while filling in the form.
function accountTypeForRole(role: ProfileRole): AccountType {
  if (role === 'kb_manager' || role === 'group_manager' || role === 'admin') {
    return 'knowledge'
  }
  return 'chat'
}

function accountTypeLabel(account: AccountType): string {
  if (account === 'chat') return m.admin_users_account_chat_label()
  return m.admin_users_account_knowledge_label()
}

interface InviteForm {
  first_name: string
  last_name: string
  email: string
  role: ProfileRole
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
    role: 'personal',
    preferred_language: defaultLanguage,
  })

  // Derived state: account type updates automatically when role changes.
  // v0.5.0: no admin override, no manual state to track.
  const account: AccountType = accountTypeForRole(form.role)
  const price = ACCOUNT_PRICE_MONTHLY[account]

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

  const msgs = m as unknown as Record<string, (() => string) | undefined>

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
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
            <Label htmlFor="role">{m.admin_users_field_profile()}</Label>
            <Select
              id="role"
              value={form.role}
              onChange={(e) => setForm((prev) => ({ ...prev, role: e.target.value as ProfileRole }))}
            >
              {PROFILE_LADDER.map((role) => {
                const labelFn = msgs[`profile_${role}_label`]
                return (
                  <option key={role} value={role}>
                    {labelFn ? labelFn() : role}
                  </option>
                )
              })}
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

        {/*
          SPEC-PORTAL-PRICING-PER-USER-001 v0.5.0 — derived account type.
          Updates automatically when the Profile dropdown changes. No
          admin override; the server runs the same ``suggest_seat(role)``
          derivation regardless.

          A11y note: this is a display-only badge (no interactive form
          control), so we use a plain heading <div> + aria-labelledby on
          the readout region — NOT a <Label htmlFor=>, which assumes a
          focusable form control as its target. The readout has
          role="status" so SR users hear updates when the Profile
          dropdown re-derives the badge.
        */}
        <div className="space-y-1.5">
          <div
            id="account-type-label"
            className="font-display-bold text-sm text-gray-900"
          >
            {m.admin_users_field_account_type()}
          </div>
          <div
            role="status"
            aria-labelledby="account-type-label"
            aria-live="polite"
            data-testid="account-type-display"
            className="flex items-center justify-between rounded-md border border-[var(--color-border)] bg-[var(--color-rl-cream)] px-4 py-3 text-sm"
          >
            <div className="flex flex-col">
              <span className="font-medium text-gray-900">{accountTypeLabel(account)}</span>
              <span className="text-xs text-gray-500">
                {m.admin_users_account_derived_hint()}
              </span>
            </div>
            <span className="font-display-bold text-gray-900">
              {m.admin_users_account_price_per_month({ amount: price })}
            </span>
          </div>
        </div>

        {inviteMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {cleanErrorMessage(inviteMutation.error, m.admin_users_error_invite_generic())}
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
