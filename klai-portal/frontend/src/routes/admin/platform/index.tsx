import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { BookOpen, Download, Plus, RotateCw, Search, Shield } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { API_BASE } from '@/lib/api'
import { fetchMe } from '@/lib/api-me'
import { useAuth } from '@/lib/auth'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import * as m from '@/paraglide/messages'
import { usePlatformStats } from './-hooks'
import {
  BotsTab,
  ChatErrorsTab,
  KbTab,
  MessagesTab,
  OrgsTab,
  StatusTab,
  SubdomainsTab,
  SubsTab,
  TemplatesTab,
  UsersTab,
} from './-components/PlatformDashboardTabs'
import { FeedbackTab } from './-components/feedback/FeedbackTab'
import { StatCard } from '@/components/ui/stat-card'
import type { PlatformTab } from './-types'

const VALID_TABS = new Set<PlatformTab>([
  'users',
  'organizations',
  'messages',
  'knowledge-bases',
  'templates',
  'subscriptions',
  'bots',
  'feedback',
  'chat-errors',
  'status',
  'subdomains',
])

type PlatformSearch = {
  tab?: PlatformTab
  messageUserId?: string
  messageOrgId?: string
  messageRecipient?: string
}

export const Route = createFileRoute('/admin/platform/')({
  validateSearch: (search: Record<string, unknown>): PlatformSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as PlatformTab)
      : undefined,
    messageUserId: typeof search.messageUserId === 'string' ? search.messageUserId : undefined,
    messageOrgId: typeof search.messageOrgId === 'string' ? search.messageOrgId : undefined,
    messageRecipient: typeof search.messageRecipient === 'string' ? search.messageRecipient : undefined,
  }),
  component: PlatformConsole,
})

const TABS: { id: PlatformTab; label: () => string }[] = [
  { id: 'users', label: m.platform_tab_users },
  { id: 'organizations', label: m.platform_tab_organizations },
  { id: 'messages', label: m.platform_tab_messages },
  { id: 'knowledge-bases', label: m.platform_tab_knowledge_bases },
  { id: 'templates', label: m.platform_tab_templates },
  { id: 'subscriptions', label: m.platform_tab_subscriptions },
  { id: 'bots', label: m.platform_tab_bots },
  { id: 'feedback', label: m.platform_tab_feedback },
  { id: 'chat-errors', label: m.platform_tab_chat_errors },
  { id: 'status', label: m.platform_tab_status },
  { id: 'subdomains', label: m.platform_tab_subdomains },
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
  const routeSearch = Route.useSearch()
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
  const tab = routeSearch.tab ?? 'users'
  const composeOrgId = Number(routeSearch.messageOrgId)
  const composeTarget =
    routeSearch.messageUserId && Number.isFinite(composeOrgId)
      ? {
          userId: routeSearch.messageUserId,
          orgId: composeOrgId,
          recipient: routeSearch.messageRecipient ?? routeSearch.messageUserId,
        }
      : null
  const newFeedbackCount = stats?.new_feedback_count ?? 0
  const chatErrorCount = stats?.chat_error_count ?? 0
  const extensionDownloadUrl = `${API_BASE}/api/app/shield/extension.zip`

  function setPlatformTab(nextTab: PlatformTab) {
    void navigate({
      to: '/admin/platform',
      search: { tab: nextTab === 'users' ? undefined : nextTab },
    })
  }

  function clearComposeTarget() {
    void navigate({
      to: '/admin/platform',
      search: { tab: 'messages' },
      replace: true,
    })
  }

  const platformTabs: TabItem<PlatformTab>[] = TABS.map((t) => ({
    id: t.id,
    label: t.label(),
    count:
      t.id === 'feedback'
        ? newFeedbackCount
        : t.id === 'chat-errors'
          ? chatErrorCount
          : undefined,
    countTone:
      t.id === 'feedback' ? 'warning' : t.id === 'chat-errors' ? 'destructive' : undefined,
  }))

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
    void queryClient.invalidateQueries({ queryKey: ['platform-message-threads'] })
  }

  return (
    <div className="mx-auto max-w-6xl space-y-8 px-6 pt-4 pb-10">
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

      <div className="rounded-xl border border-gray-200 bg-white px-4 py-4">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Shield className="h-4 w-4 text-gray-500" />
              <h2 className="text-sm font-semibold text-gray-900">
                Shield browser test
              </h2>
            </div>
            <p className="mt-1 max-w-2xl text-sm text-gray-500">
              Download de extensie en log in met je Klai-account in Chrome of Edge.
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <Button type="button" variant="secondary" asChild>
              <a href={extensionDownloadUrl}>
                <Download className="h-4 w-4" />
                Download extensie
              </a>
            </Button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
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
        <StatCard
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
        <StatCard
          label={m.platform_stat_bots()}
          value={stats?.total_bots}
          sub={
            stats ? m.platform_stat_new_bots_today({ count: stats.new_bots_today }) : undefined
          }
          loading={statsQuery.isLoading}
        />
        <StatCard
          label={m.platform_stat_knowledge_bases()}
          value={stats?.total_kbs}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label={m.platform_stat_templates()}
          value={stats?.total_templates}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label="MRR"
          value={stats ? `€${(stats.mrr_cents / 100).toFixed(2)}` : undefined}
          sub={stats ? `€${(stats.arr_cents / 100).toFixed(0)} ARR` : undefined}
          loading={statsQuery.isLoading}
        />
        <StatCard
          label={m.platform_tab_feedback()}
          value={
            stats ? (
              <>
                {stats.new_feedback_count}{' '}
                <span className="font-display text-gray-400">
                  {m.platform_stat_new_feedback_unit()}
                </span>
              </>
            ) : undefined
          }
          sub={
            stats
              ? m.platform_stat_feedback_total({ count: stats.total_feedback_count })
              : undefined
          }
          loading={statsQuery.isLoading}
          alert={newFeedbackCount > 0}
          tone={newFeedbackCount > 0 ? 'warning' : 'default'}
          onClick={() => setPlatformTab('feedback')}
        />
        <StatCard
          label={m.platform_tab_chat_errors()}
          value={stats?.chat_error_count}
          sub={m.platform_stat_chat_errors_24h()}
          loading={statsQuery.isLoading}
          alert={chatErrorCount > 0}
          onClick={() => setPlatformTab('chat-errors')}
        />
      </div>

      <Tabs
        tabs={platformTabs}
        value={tab}
        onValueChange={setPlatformTab}
        className="overflow-x-auto"
      />

      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={
              tab === 'feedback'
                ? m.platform_feedback_search_global_placeholder()
                : m.platform_search_placeholder()
            }
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
      {tab === 'messages' && (
        <MessagesTab
          search={search}
          fmtDate={fmtDate}
          composeTarget={composeTarget}
          onClearComposeTarget={clearComposeTarget}
        />
      )}
      {tab === 'subscriptions' && <SubsTab search={search} />}
      {tab === 'knowledge-bases' && <KbTab search={search} fmtDate={fmtDate} />}
      {tab === 'templates' && (
        <TemplatesTab search={search} fmtDate={fmtDate} />
      )}
      {tab === 'bots' && <BotsTab search={search} fmtDate={fmtDate} />}
      {tab === 'feedback' && (
        <FeedbackTab search={search} fmtDate={fmtDate} />
      )}
      {tab === 'chat-errors' && <ChatErrorsTab fmtDate={fmtDate} />}
      {tab === 'status' && <StatusTab />}
      {tab === 'subdomains' && <SubdomainsTab search={search} />}
    </div>
  )
}
