import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  BookOpen,
  FileText,
  Loader2,
  Settings,
  Users,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import * as m from '@/paraglide/messages'
import { usePlatformOrgDetail } from './-hooks'
import {
  BotsSection,
  KnowledgeBasesSection,
  OrgSummaryStats,
  TemplatesSection,
  TenantDangerZone,
  TenantFeaturesSection,
  UsersSection,
} from './-components/OrgDetailSections'

type TabId = 'features' | 'users' | 'bots' | 'knowledge-bases' | 'templates' | 'danger'

const VALID_TABS = new Set<TabId>([
  'features',
  'users',
  'bots',
  'knowledge-bases',
  'templates',
  'danger',
])

type DetailSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/admin/platform/orgs/$orgId')({
  validateSearch: (search: Record<string, unknown>): DetailSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: PlatformOrgDetailPage,
})

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

function PlatformOrgDetailPage() {
  const { orgId } = useParams({ from: '/admin/platform/orgs/$orgId' })
  const search = Route.useSearch()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = usePlatformOrgDetail(orgId)
  const activeTab: TabId = search.tab ?? 'features'

  if (isLoading) {
    return (
      <div className="mx-auto max-w-4xl px-6 pt-4 pb-10">
        <p className="py-8 text-sm text-gray-400">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_shared_loading()}
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-6 pt-4 pb-10">
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (!data) return null

  const tabs: TabItem<TabId>[] = [
    {
      id: 'features',
      label: m.admin_settings_tab_features(),
      icon: Settings,
    },
    {
      id: 'users',
      label: m.platform_tab_users(),
      icon: Users,
      count: data.users.length,
      countTone: 'neutral',
    },
    {
      id: 'bots',
      label: m.platform_tab_bots(),
      icon: Bot,
      count: data.bots.length,
      countTone: 'neutral',
    },
    {
      id: 'knowledge-bases',
      label: m.platform_tab_knowledge_bases(),
      icon: BookOpen,
      count: data.knowledge_bases.length,
      countTone: 'neutral',
    },
    {
      id: 'templates',
      label: m.platform_tab_templates(),
      icon: FileText,
      count: data.templates.length,
      countTone: 'neutral',
    },
    {
      id: 'danger',
      label: m.admin_shared_tab_danger(),
      icon: AlertTriangle,
    },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/admin/platform/orgs/$orgId',
      params: { orgId },
      search: { tab },
    })
  }

  return (
    <div className="mx-auto max-w-4xl px-6 pt-4 pb-10 space-y-8">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {data.org.name}
          </h1>
          <div className="mt-2 flex items-center gap-2 flex-wrap text-sm text-gray-400">
            <span className="font-mono">{data.org.slug}</span>
            <span>·</span>
            <Badge variant="outline">{data.org.plan}</Badge>
            <Badge
              variant={
                data.org.provisioning_status === 'ready'
                  ? 'success'
                  : 'outline'
              }
            >
              {data.org.provisioning_status}
            </Badge>
            <span>·</span>
            <span>
              {m.platform_created_at({ date: fmtDate(data.org.created_at) })}
            </span>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => void navigate({ to: '/admin/platform' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.platform_back_to_platform()}
        </Button>
      </div>

      <OrgSummaryStats
        org={data.org}
        templateCount={data.templates.length}
      />

      <Tabs
        tabs={tabs}
        value={activeTab}
        onValueChange={setTab}
        className="overflow-x-auto"
      />

      {activeTab === 'features' && (
        <TenantFeaturesSection orgId={orgId} org={data.org} />
      )}
      {activeTab === 'users' && (
        <UsersSection orgId={orgId} users={data.users} />
      )}
      {activeTab === 'bots' && <BotsSection bots={data.bots} fmtDate={fmtDate} />}
      {activeTab === 'knowledge-bases' && (
        <KnowledgeBasesSection
          knowledgeBases={data.knowledge_bases}
          fmtDate={fmtDate}
        />
      )}
      {activeTab === 'templates' && (
        <TemplatesSection templates={data.templates} fmtDate={fmtDate} />
      )}
      {activeTab === 'danger' && <TenantDangerZone org={data.org} />}
    </div>
  )
}
