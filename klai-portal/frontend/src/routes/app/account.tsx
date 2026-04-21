import { createFileRoute, Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  ChevronRight,
  Download,
  User as UserIcon,
  Globe,
  Building2,
  CreditCard,
  Shield,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useLocale } from '@/lib/locale'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { number } from '@/paraglide/registry'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser } from '@/hooks/useCurrentUser'

function SectionHeader({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof UserIcon
  title: string
  description?: string
}) {
  return (
    <div className="flex items-start gap-3 mb-5">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50">
        <Icon size={16} strokeWidth={1.75} className="text-gray-500" />
      </div>
      <div className="min-w-0 flex-1">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        {description && <p className="text-xs text-gray-400 mt-0.5">{description}</p>}
      </div>
    </div>
  )
}

export const Route = createFileRoute('/app/account')({
  component: AccountPage,
})

type MfaPolicy = 'optional' | 'recommended' | 'required'
type Lang = 'nl' | 'en'
type Plan = 'core' | 'professional' | 'complete' | 'free'
type BillingCycle = 'monthly' | 'yearly'
type BillingStatus = 'pending' | 'mandate_requested' | 'active' | 'payment_failed' | 'cancelled'

interface AdminSettings {
  name: string
  default_language: Lang
  mfa_policy: MfaPolicy
}

interface BillingStatusResponse {
  billing_status: BillingStatus
  plan: Plan
  billing_cycle: BillingCycle
  seats: number
  moneybird_contact_id: string | null
}

const PLAN_PRICES: Record<Plan, { monthly: number; yearly: number }> = {
  core: { monthly: 22, yearly: 18 },
  professional: { monthly: 42, yearly: 34 },
  complete: { monthly: 60, yearly: 48 },
  free: { monthly: 0, yearly: 0 },
}

function getPlanLabel(plan: Plan): string {
  if (plan === 'free') return m.admin_billing_free_title()
  if (plan === 'core') return 'Chat'
  if (plan === 'professional') return 'Chat + Focus'
  return 'Chat + Focus + Scribe'
}

function AccountPage() {
  const { locale, switchLocale } = useLocale()
  const { user } = useCurrentUser()
  const isAdmin = user?.isAdmin === true

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <h1 className="text-[26px] font-display-bold text-gray-900 mb-2">
        {m.account_heading()}
      </h1>
      <p className="text-sm text-gray-400 mb-8">{m.account_subtitle()}</p>

      <div className="space-y-4">
        <ProfileSection />
        <LanguageSection locale={locale} switchLocale={switchLocale} />
        {isAdmin && <OrgSettingsSection />}
        {isAdmin && <BillingSection />}
        <SarSection />
      </div>
    </div>
  )
}

/* ── Profile (read-only) ──────────────────────────────────────────────── */

function ProfileSection() {

  const { data: me } = useQuery<{ name?: string; email?: string }>({
    queryKey: ['me-profile'],
    queryFn: async () => {
      try {
        return await apiFetch<{ name?: string; email?: string }>(`/api/me`)
      } catch {
        return {}
      }
    },
  })

  const name = me?.name ?? ''
  const email = me?.email ?? ''

  return (
    <section className="rounded-lg border border-gray-200 p-6" data-help-id="account-profile">
      <SectionHeader icon={UserIcon} title={m.account_profile_title()} />
      <dl className="space-y-3">
        <div className="flex gap-4">
          <dt className="w-32 shrink-0 text-sm text-gray-400">{m.account_profile_name()}</dt>
          <dd className="text-sm font-medium text-gray-900">{name || '—'}</dd>
        </div>
        <div className="flex gap-4">
          <dt className="w-32 shrink-0 text-sm text-gray-400">{m.account_profile_email()}</dt>
          <dd className="text-sm font-medium text-gray-900">{email || '—'}</dd>
        </div>
      </dl>
    </section>
  )
}

/* ── Personal language preference ─────────────────────────────────────── */

function LanguageSection({ locale,
  switchLocale,
}: {
  locale: Lang
  switchLocale: (l: Lang) => void
}) {
  const [saved, setSaved] = useState(false)
  const [selectedLang, setSelectedLang] = useState<Lang>(locale)

  const { data: meData } = useQuery({
    queryKey: ['me-language'],
    queryFn: async () => {
      try {
        return await apiFetch<{ preferred_language?: Lang }>(`/api/me`)
      } catch {
        return null
      }
    },
  })

  useEffect(() => {
    if (meData?.preferred_language) setSelectedLang(meData.preferred_language)
  }, [meData])

  const saveMutation = useMutation({
    mutationFn: async (preferred_language: Lang) => {
      await apiFetch(`/api/me/language`, {
        method: 'PATCH',
        body: JSON.stringify({ preferred_language }),
      })
      return preferred_language
    },
    onSuccess: (lang) => {
      switchLocale(lang)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    },
  })

  return (
    <section className="rounded-lg border border-gray-200 p-6">
      <SectionHeader
        icon={Globe}
        title={m.account_language_title()}
        description={m.account_language_description()}
      />
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="account-language" className="text-gray-900">
            {m.account_language_label()}
          </Label>
          <Select
            id="account-language"
            value={selectedLang}
            onChange={(e) => setSelectedLang(e.target.value as Lang)}
            className="max-w-xs rounded-lg border-gray-200"
          >
            <option value="nl">{m.account_language_nl()}</option>
            <option value="en">{m.account_language_en()}</option>
          </Select>
        </div>
        {saveMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">{m.account_error_save()}</p>
        )}
        <Button
          onClick={() => saveMutation.mutate(selectedLang)}
          disabled={saveMutation.isPending || saved}
        >
          {saved ? m.account_saved() : saveMutation.isPending ? m.account_saving() : m.account_save()}
        </Button>
      </div>
    </section>
  )
}

/* ── Org settings (admin only) ────────────────────────────────────────── */

function OrgSettingsSection() {
  const { data: settings, isLoading, error } = useQuery<AdminSettings>({
    queryKey: ['admin-settings'],
    queryFn: async () => apiFetch<AdminSettings>(`/api/admin/settings`),
  })

  const [selectedLang, setSelectedLang] = useState<Lang>('nl')
  const [selectedMfa, setSelectedMfa] = useState<MfaPolicy>('optional')
  const [savedLang, setSavedLang] = useState(false)
  const [savedMfa, setSavedMfa] = useState(false)

  useEffect(() => {
    if (settings) {
      setSelectedLang(settings.default_language)
      setSelectedMfa(settings.mfa_policy ?? 'optional')
    }
  }, [settings])

  async function patchSettings(
    payload: { default_language?: Lang; mfa_policy?: MfaPolicy },
  ) {
    return apiFetch(`/api/admin/settings`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
  }

  const langMutation = useMutation({
    mutationFn: (lang: Lang) => patchSettings({ default_language: lang }),
    onSuccess: () => {
      setSavedLang(true)
      setTimeout(() => setSavedLang(false), 2500)
    },
  })

  const mfaMutation = useMutation({
    mutationFn: (policy: MfaPolicy) => patchSettings({ mfa_policy: policy }),
    onSuccess: () => {
      setSavedMfa(true)
      setTimeout(() => setSavedMfa(false), 2500)
    },
  })

  return (
    <section className="rounded-lg border border-gray-200 p-6">
      <SectionHeader
        icon={Building2}
        title={m.admin_settings_heading()}
        description={m.admin_settings_subtitle()}
      />

      {isLoading ? (
        <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
      ) : error ? (
        <p className="text-sm text-[var(--color-destructive)]">{m.admin_settings_error_fetch()}</p>
      ) : (
        <div className="space-y-6">
          {/* Default language */}
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="org-language">{m.admin_settings_language_label()}</Label>
              <p className="text-xs text-gray-400">{m.admin_settings_language_description()}</p>
              <Select
                id="org-language"
                value={selectedLang}
                onChange={(e) => setSelectedLang(e.target.value as Lang)}
                className="max-w-xs"
              >
                <option value="nl">{m.admin_settings_language_nl()}</option>
                <option value="en">{m.admin_settings_language_en()}</option>
              </Select>
            </div>
            <Button
              size="sm"
              onClick={() => langMutation.mutate(selectedLang)}
              disabled={langMutation.isPending || savedLang}
            >
              {savedLang
                ? m.admin_settings_saved()
                : langMutation.isPending
                  ? m.admin_settings_saving()
                  : m.admin_settings_save()}
            </Button>
          </div>

          <div className="border-t border-gray-200 pt-6 space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="org-mfa">{m.admin_settings_mfa_label()}</Label>
              <p className="text-xs text-gray-400">{m.admin_settings_security_description()}</p>
              <Select
                id="org-mfa"
                value={selectedMfa}
                onChange={(e) => setSelectedMfa(e.target.value as MfaPolicy)}
                className="max-w-xs"
              >
                <option value="optional">{m.admin_settings_mfa_optional()}</option>
                <option value="recommended">{m.admin_settings_mfa_recommended()}</option>
                <option value="required">{m.admin_settings_mfa_required()}</option>
              </Select>
              <p className="text-xs text-gray-400">
                {selectedMfa === 'optional' && m.admin_settings_mfa_optional_hint()}
                {selectedMfa === 'recommended' && m.admin_settings_mfa_recommended_hint()}
                {selectedMfa === 'required' && m.admin_settings_mfa_required_hint()}
              </p>
            </div>
            <Button
              size="sm"
              onClick={() => mfaMutation.mutate(selectedMfa)}
              disabled={mfaMutation.isPending || savedMfa}
            >
              {savedMfa
                ? m.admin_settings_saved()
                : mfaMutation.isPending
                  ? m.admin_settings_saving()
                  : m.admin_settings_save()}
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

/* ── Billing summary (admin only) ─────────────────────────────────────── */

function BillingSection() {
  const { data: status, isLoading, error } = useQuery<BillingStatusResponse>({
    queryKey: ['billing-status'],
    queryFn: async () => apiFetch<BillingStatusResponse>(`/api/billing/status`),
  })

  return (
    <section className="rounded-lg border border-gray-200 p-6">
      <SectionHeader
        icon={CreditCard}
        title={m.admin_billing_heading()}
        description={m.admin_billing_subtitle()}
      />

      {isLoading ? (
        <p className="text-sm text-gray-400">{m.admin_users_loading()}</p>
      ) : error || !status ? (
        <p className="text-sm text-[var(--color-destructive)]">{m.admin_billing_error_fetch()}</p>
      ) : (
        <div className="space-y-4">
          <dl className="space-y-3">
            <div className="flex gap-4">
              <dt className="w-32 shrink-0 text-sm text-gray-400">{m.admin_billing_active_plan_label()}</dt>
              <dd className="text-sm font-medium text-gray-900">{getPlanLabel(status.plan)}</dd>
            </div>
            {status.plan !== 'free' && (
              <>
                <div className="flex gap-4">
                  <dt className="w-32 shrink-0 text-sm text-gray-400">
                    {m.admin_billing_active_cycle_label()}
                  </dt>
                  <dd className="text-sm font-medium text-gray-900">
                    {status.billing_cycle === 'yearly'
                      ? m.admin_billing_cycle_yearly()
                      : m.admin_billing_cycle_monthly()}
                  </dd>
                </div>
                <div className="flex gap-4">
                  <dt className="w-32 shrink-0 text-sm text-gray-400">
                    {m.admin_billing_active_seats_label()}
                  </dt>
                  <dd className="text-sm font-medium text-gray-900">{status.seats}</dd>
                </div>
                <div className="flex gap-4">
                  <dt className="w-32 shrink-0 text-sm text-gray-400">
                    {m.admin_billing_total_excl_vat()}
                  </dt>
                  <dd className="text-sm font-medium text-gray-900">
                    {formatTotal(status.plan, status.billing_cycle, status.seats)}
                  </dd>
                </div>
              </>
            )}
          </dl>
          <div className="border-t border-gray-200 pt-4">
            <Link
              to="/admin/billing"
              className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-700 hover:text-gray-900 transition-colors"
            >
              {m.admin_billing_manage_link()}
              <ChevronRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      )}
    </section>
  )
}

function formatTotal(plan: Plan, cycle: BillingCycle, seats: number): string {
  const prices = PLAN_PRICES[plan]
  const price = cycle === 'yearly' ? prices.yearly : prices.monthly
  const total = price * seats
  return cycle === 'yearly'
    ? `\u20ac${number(getLocale(), total * 12)} ${m.admin_billing_per_year()}`
    : `\u20ac${number(getLocale(), total)} ${m.admin_billing_per_month()}`
}

/* ── SAR export ───────────────────────────────────────────────────────── */

function SarSection() {
  const sarMutation = useMutation({
    mutationFn: async () => {
      return apiFetch(`/api/me/sar-export`, { method: 'POST' })
    },
    onSuccess: (data: unknown) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const date = new Date().toISOString().split('T')[0]
      a.download = `sar-export-${date}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    },
  })

  return (
    <section className="rounded-lg border border-gray-200 p-6">
      <SectionHeader
        icon={Shield}
        title={m.account_sar_title()}
        description={m.account_sar_description()}
      />
      {sarMutation.error && (
        <p className="text-sm text-[var(--color-destructive)] mb-3">{m.account_sar_error()}</p>
      )}
      <Button
        variant="outline"
        onClick={() => sarMutation.mutate()}
        disabled={sarMutation.isPending}
        className="gap-2"
      >
        <Download className="h-4 w-4" />
        {sarMutation.isPending ? m.account_sar_downloading() : m.account_sar_button()}
      </Button>
    </section>
  )
}

