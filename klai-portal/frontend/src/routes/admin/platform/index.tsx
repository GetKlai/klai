import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { BookOpen, Plus, RotateCw, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { fetchMe } from '@/lib/api-me'
import { useAuth } from '@/lib/auth'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import * as m from '@/paraglide/messages'
import { usePlatformStats } from './-hooks'
import {
  BotsTab,
  ChatErrorsTab,
  FeedbackTab,
  KbTab,
  OrgsTab,
  StatusTab,
  SubsTab,
  TemplatesTab,
  UsersTab,
} from './-components/PlatformDashboardTabs'
import { PlatformStatCard } from './-components/PlatformShell'
import type { PlatformTab } from './-types'

export const Route = createFileRoute('/admin/platform/')({
  component: PlatformConsole,
})

const TABS: { id: PlatformTab; label: () => string }[] = [
  { id: 'users', label: m.platform_tab_users },
  { id: 'organizations', label: m.platform_tab_organizations },
  { id: 'knowledge-bases', label: m.platform_tab_knowledge_bases },
  { id: 'templates', label: m.platform_tab_templates },
  { id: 'subscriptions', label: m.platform_tab_subscriptions },
  { id: 'bots', label: m.platform_tab_bots },
  { id: 'feedback', label: m.platform_tab_feedback },
  { id: 'chat-errors', label: m.platform_tab_chat_errors },
  { id: 'status', label: m.platform_tab_status },
]

function fmtDate(iso: string | null): string {
  if (!iso) return '-'
  return datetime(getLocale(), iso, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function PlatformConsole() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [tab, setTab] = useState<PlatformTab>('users')
  const [search, setSearch] = useState('')
  const auth = useAuth()
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: auth.isAuthenticated,
  })
  const isPlatformAdmin = meQuery.data?.is_platform_admin === true

  const statsQuery = usePlatformStats(isPlatformAdmin)
  const stats = statsQuery.data

  useEffect(() => {
    if ((meQuery.data && !isPlatformAdmin) || meQuery.isError) {
      void navigate({ to: '/admin', replace: true })
    }
  }, [isPlatformAdmin, meQuery.data, meQuery.isError, navigate])

  if (
    meQuery.isLoading ||
    (auth.isAuthenticated && !meQuery.data && !meQuery.isError) ||
    !isPlatformAdmin
  ) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-rl-accent)] border-t-transparent" />
      </div>
    )
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['platform-stats'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-users'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-orgs'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-bots'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-kbs'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-templates'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-chat-errors'] })
    void queryClient.invalidateQueries({ queryKey: ['platform-feedback-submissions'] })
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-6 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.platform_title()}
          </h1>
          <p className="mt-1 text-sm text-gray-400">
            {m.platform_description()}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <Button
            type="button"
            onClick={() =>
              void navigate({ to: '/admin/platform/onboarding-howto' })
            }
            variant="secondary"
          >
            <BookOpen className="h-4 w-4" />
            {m.platform_onboarding_howto()}
          </Button>
          <Button
            type="button"
            onClick={() => void navigate({ to: '/admin/platform/new' })}
          >
            <Plus className="h-4 w-4" />
            {m.platform_new_tenant()}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-3">
        <PlatformStatCard
          label={m.platform_stat_users()}
          value={stats?.total_users}
          sub={
            stats
              ? m.platform_stat_new_users_this_month({
                  count: stats.new_users_this_month,
                })
              : undefined
          }
          loading={statsQuery.isLoading}
        />
        <PlatformStatCard
          label={m.platform_stat_organizations()}
          value={stats?.total_orgs}
          sub={
            stats
              ? m.platform_stat_active_subscriptions({
                  count: stats.active_subscriptions,
                })
              : undefined
          }
          loading={statsQuery.isLoading}
        />
        <PlatformStatCard
          label={m.platform_stat_bots()}
          value={stats?.total_bots}
          sub={
            stats ? m.platform_stat_new_bots_today({ count: stats.new_bots_today }) : undefined
          }
          loading={statsQuery.isLoading}
        />
        <PlatformStatCard
          label={m.platform_stat_knowledge_bases()}
          value={stats?.total_kbs}
          loading={statsQuery.isLoading}
        />
        <PlatformStatCard
          label={m.platform_stat_templates()}
          value={stats?.total_templates}
          loading={statsQuery.isLoading}
        />
        <PlatformStatCard
          label="MRR"
          value={stats ? `€${(stats.mrr_cents / 100).toFixed(2)}` : undefined}
          sub={stats ? `€${(stats.arr_cents / 100).toFixed(0)} ARR` : undefined}
          loading={statsQuery.isLoading}
        />
      </div>

      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-6 overflow-x-auto">
          {TABS.map((t) => {
            const active = t.id === tab
            return (
              <Button
                key={t.id}
                type="button"
                variant="link"
                onClick={() => setTab(t.id)}
                className={[
                  'h-auto rounded-none border-b-2 px-0 pb-3 text-sm font-medium no-underline transition-colors hover:no-underline',
                  active
                    ? 'border-gray-900 text-gray-900'
                    : 'border-transparent text-gray-400 hover:text-gray-900',
                ].join(' ')}
              >
                {t.label()}
              </Button>
            )
          })}
        </nav>
      </div>

      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={m.platform_search_placeholder()}
            className="pl-9"
          />
        </div>
        <Button type="button" onClick={refresh} variant="secondary">
          <RotateCw className="h-4 w-4" />
          {m.platform_refresh()}
        </Button>
      </div>

      {tab === 'users' && <UsersTab search={search} fmtDate={fmtDate} />}
      {tab === 'organizations' && (
        <OrgsTab search={search} fmtDate={fmtDate} />
      )}
      {tab === 'subscriptions' && <SubsTab search={search} />}
      {tab === 'knowledge-bases' && <KbTab search={search} fmtDate={fmtDate} />}
      {tab === 'templates' && (
        <TemplatesTab search={search} fmtDate={fmtDate} />
      )}
      {tab === 'bots' && <BotsTab search={search} fmtDate={fmtDate} />}
      {tab === 'feedback' && <FeedbackTab search={search} fmtDate={fmtDate} />}
      {tab === 'chat-errors' && <ChatErrorsTab fmtDate={fmtDate} />}
      {tab === 'status' && <StatusTab />}
    </div>
  )
}
