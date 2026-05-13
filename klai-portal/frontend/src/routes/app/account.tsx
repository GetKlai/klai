import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Download, Settings, SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { useLocale } from '@/lib/locale'
import * as m from '@/paraglide/messages'
import { ApiError, apiFetch } from '@/lib/apiFetch'

type TabId = 'settings' | 'advanced'

const VALID_TABS = new Set<TabId>(['settings', 'advanced'])

type AccountSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/app/account')({
  validateSearch: (search: Record<string, unknown>): AccountSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: AccountPage,
})

interface MeAccount {
  preferred_language?: 'nl' | 'en'
}

function AccountPage() {
  const auth = useAuth()
  const { locale, switchLocale } = useLocale()
  const search = Route.useSearch()
  const navigate = useNavigate()

  const [saved, setSaved] = useState(false)
  const [selectedLang, setSelectedLang] = useState<'nl' | 'en'>(locale)
  const activeTab: TabId = search.tab ?? 'settings'

  // Fetch current user's preferred language from the portal DB
  const { data: meData } = useQuery({
    queryKey: ['me-language'],
    queryFn: async () => {
      try {
        return await apiFetch<MeAccount>(`/api/me`)
      } catch {
        return null
      }
    },
    enabled: auth.isAuthenticated,
  })

  useEffect(() => {
    if (meData?.preferred_language) {
      setSelectedLang(meData.preferred_language)
    }
  }, [meData])

  const saveMutation = useMutation({
    mutationFn: async (preferred_language: 'nl' | 'en') => {
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

  const name = auth.user?.profile?.name ?? auth.user?.profile?.preferred_username ?? ''
  const email = auth.user?.profile?.email ?? ''
  const hasProfileInfo = Boolean(name || email)

  const tabs: { id: TabId; label: string; icon: React.ElementType }[] = [
    { id: 'settings', label: m.account_tab_settings(), icon: Settings },
    { id: 'advanced', label: m.account_tab_advanced(), icon: SlidersHorizontal },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/app/account',
      search: { tab },
    })
  }

  return (
    <div className="mx-auto max-w-2xl px-6 py-10 space-y-8">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.account_heading()}
        </h1>
        <p className="text-sm text-gray-400">
          {m.account_subtitle()}
        </p>
      </div>

      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-6">
          {tabs.map(({ id: tabId, label, icon: TabIcon }) => {
            const isActive = tabId === activeTab
            return (
              <button
                key={tabId}
                type="button"
                onClick={() => setTab(tabId)}
                className={[
                  'flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors',
                  isActive
                    ? 'border-gray-200 text-gray-900'
                    : 'border-transparent text-gray-400 hover:text-gray-900',
                ].join(' ')}
              >
                <TabIcon className="h-4 w-4" />
                {label}
              </button>
            )
          })}
        </nav>
      </div>

      {activeTab === 'settings' && (
        <div className="space-y-6" data-help-id="account-2fa">
          {hasProfileInfo && (
            <div className="border-b border-gray-200 pb-6">
              <dl className="space-y-3">
                {name && (
                  <div className="flex flex-col gap-1 sm:flex-row sm:gap-4">
                    <dt className="w-32 shrink-0 text-sm text-gray-400">Naam</dt>
                    <dd className="text-sm font-medium text-gray-900">{name}</dd>
                  </div>
                )}
                {email && (
                  <div className="flex flex-col gap-1 sm:flex-row sm:gap-4">
                    <dt className="w-32 shrink-0 text-sm text-gray-400">E-mail</dt>
                    <dd className="text-sm font-medium text-gray-900">{email}</dd>
                  </div>
                )}
              </dl>
            </div>
          )}

          <div>
            <h2 className="text-sm font-medium text-gray-900 mb-2">
              {m.account_language_title()}
            </h2>
            <p className="text-sm text-gray-400 mb-6">
              {m.account_language_description()}
            </p>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1 space-y-1.5">
                <Label htmlFor="account-language">
                  {m.account_language_label()}
                </Label>
                <Select
                  id="account-language"
                  value={selectedLang}
                  onChange={(e) => setSelectedLang(e.target.value as 'nl' | 'en')}
                  className="max-w-xs"
                >
                  <option value="nl">{m.account_language_nl()}</option>
                  <option value="en">{m.account_language_en()}</option>
                </Select>
              </div>
              <Button
                className="w-fit"
                onClick={() => saveMutation.mutate(selectedLang)}
                disabled={saveMutation.isPending || saved}
              >
                {saved
                  ? m.account_saved()
                  : saveMutation.isPending
                    ? m.account_saving()
                    : m.account_save()}
              </Button>
            </div>
            {saveMutation.error && (
              <p className="text-sm text-[var(--color-destructive)]">{m.account_error_save()}</p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'advanced' && (
        <div className="space-y-6">
          <div>
            <h2 className="text-sm font-medium text-gray-900 mb-2">{m.account_sar_title()}</h2>
            <p className="text-sm text-gray-400 mb-4">
              {m.account_sar_description()}
            </p>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => sarMutation.mutate()}
              disabled={sarMutation.isPending}
            >
              <Download className="h-4 w-4 mr-2" />
              {sarMutation.isPending ? m.account_sar_downloading() : m.account_sar_button()}
            </Button>
            {sarMutation.error && (
              <p className="mt-3 text-sm text-[var(--color-destructive)]">
                {sarMutation.error instanceof ApiError && sarMutation.error.status === 429
                  ? m.account_sar_rate_limited()
                  : m.account_sar_error()}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
