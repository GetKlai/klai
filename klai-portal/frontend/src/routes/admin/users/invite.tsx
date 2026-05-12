import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { AlertCircle, ArrowLeft } from 'lucide-react'
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

// SPEC-PORTAL-PRICING-PER-USER-001 Phase 2: per-user billing tier,
// orthogonal to role.
type SeatType = 'viewer' | 'chat' | 'knowledge'

const SEAT_PRICE_MONTHLY: Record<SeatType, number> = {
  viewer: 0,
  chat: 28,
  knowledge: 68,
}

// Smart-default seat for a role. Mirrors
// ``klai-portal/backend/app/core/seats.py::DEFAULT_SEAT_FOR_ROLE``.
// Admin can override via the seat selector — that path is what makes the
// billing axis decoupled from the role axis.
function defaultSeatForRole(role: ProfileRole): SeatType {
  if (role === 'kb_manager' || role === 'group_manager' || role === 'admin') {
    return 'knowledge'
  }
  return 'chat'
}

// True when the chosen role expects KB-management features that the
// chosen seat does NOT unlock. Surfaces a non-blocking ⚠ warning in the
// modal — the assignment still ships (AC-5), the admin is just told the
// effective UI for this user will be limited.
function isRoleSeatMismatch(role: ProfileRole, seat: SeatType): boolean {
  const roleNeedsKnowledge =
    role === 'kb_manager' || role === 'group_manager' || role === 'admin'
  return roleNeedsKnowledge && seat !== 'knowledge'
}

interface InviteForm {
  first_name: string
  last_name: string
  email: string
  role: ProfileRole
  seat_type: SeatType
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

  // SPEC-PORTAL-ADMIN-UI-001 v0.3.0 Sparring #4: dropdown blijft, default "personal".
  const [form, setForm] = useState<InviteForm>({
    first_name: '',
    last_name: '',
    email: '',
    role: 'personal',
    // SPEC-PORTAL-PRICING-PER-USER-001 Phase 2: seat-tier starts at the
    // smart-default for the initial role. The role-change handler below
    // re-syncs this value when admin has not manually overridden the seat.
    seat_type: 'chat',
    preferred_language: defaultLanguage,
  })

  // Track whether admin has touched the seat selector. Once true, role
  // changes stop re-defaulting the seat (the override is sticky — this
  // is the path that makes the two axes decoupled).
  const [seatOverridden, setSeatOverridden] = useState(false)

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
              onChange={(e) => {
                const nextRole = e.target.value as ProfileRole
                setForm((prev) => ({
                  ...prev,
                  role: nextRole,
                  // Re-sync seat unless admin has explicitly overridden it.
                  seat_type: seatOverridden ? prev.seat_type : defaultSeatForRole(nextRole),
                }))
              }}
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
          SPEC-PORTAL-PRICING-PER-USER-001 Phase 2 — seat selector + cost
          delta + role/seat mismatch warning. The smart-default tracks
          the role until admin clicks a different option; from then on
          the override is sticky (seatOverridden=true).
        */}
        <div className="space-y-1.5">
          <Label htmlFor="seat-type">{m.admin_users_field_seat()}</Label>
          <div
            id="seat-type"
            role="radiogroup"
            aria-label={m.admin_users_field_seat()}
            className="grid grid-cols-1 sm:grid-cols-3 gap-2"
          >
            {(['viewer', 'chat', 'knowledge'] as const).map((seat) => {
              const seatLabelFn = msgs[`admin_users_seat_${seat}_label`]
              const price = SEAT_PRICE_MONTHLY[seat]
              const isSelected = form.seat_type === seat
              const isSuggested = !seatOverridden && seat === defaultSeatForRole(form.role)
              return (
                <button
                  key={seat}
                  type="button"
                  role="radio"
                  aria-checked={isSelected}
                  onClick={() => {
                    setSeatOverridden(true)
                    setForm((prev) => ({ ...prev, seat_type: seat }))
                  }}
                  className={
                    'flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left text-sm transition-colors ' +
                    (isSelected
                      ? 'border-[var(--color-rl-accent)] bg-[var(--color-rl-accent-bg)] text-gray-900'
                      : 'border-[var(--color-border)] bg-white text-gray-700 hover:border-gray-400')
                  }
                >
                  <span className="font-medium">
                    {seatLabelFn ? seatLabelFn() : seat}
                  </span>
                  <span className="text-xs text-gray-500">
                    {price === 0
                      ? m.admin_users_seat_free()
                      : m.admin_users_seat_price_per_month({ amount: price })}
                  </span>
                  {isSuggested && !seatOverridden && (
                    <span className="text-[10px] uppercase tracking-wide text-[var(--color-rl-accent-dark)]">
                      {m.admin_users_seat_suggested()}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
          {isRoleSeatMismatch(form.role, form.seat_type) && (
            <div
              role="alert"
              className="mt-2 flex items-start gap-2 rounded-md bg-[var(--color-warning-bg)] px-3 py-2 text-xs text-[var(--color-warning)]"
            >
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{m.admin_users_seat_mismatch_warning()}</span>
            </div>
          )}
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
