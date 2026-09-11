import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  BookOpen,
  FileText,
  Loader2,
  MessageSquare,
  Settings,
  BarChart3,
  ThumbsDown,
  ThumbsUp,
  Users,
  X,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Select } from '@/components/ui/select'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { useAuth } from '@/lib/auth'
import { apiFetch } from '@/lib/apiFetch'
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
import { UsageSection } from './-components/stats/UsageSection'
import type {
  PlatformBot,
  PlatformBotConversationDetail,
  PlatformBotConversationItem,
  PlatformUsageRange,
} from './-types'
import { PageContainer } from '@/components/ui/page-container'
// REQ-9 (Finding B-9): reuse the tested URL-scheme allowlist for conversation
// source links rather than re-implementing it here.
import { _isSafeHttpUrl } from '../widgets/_components/tabs/ActivityTab'

type TabId =
  | 'features'
  | 'users'
  | 'bots'
  | 'conversations'
  | 'knowledge-bases'
  | 'templates'
  | 'usage'
  | 'danger'

const VALID_TABS = new Set<TabId>([
  'features',
  'users',
  'bots',
  'conversations',
  'knowledge-bases',
  'templates',
  'usage',
  'danger',
])

type DetailSearch = {
  tab?: TabId
  range?: PlatformUsageRange
  widgetId?: string
}

export const Route = createFileRoute('/admin/platform/orgs/$orgId')({
  validateSearch: (search: Record<string, unknown>): DetailSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
    range:
      search.range === '7d' || search.range === '30d' || search.range === '90d'
        ? search.range
        : undefined,
    widgetId:
      typeof search.widgetId === 'string' && search.widgetId
        ? search.widgetId
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
  const range = search.range ?? '30d'

  if (isLoading) {
    return (
      <PageContainer width="4xl">
        <p className="py-8 text-sm text-gray-600">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_shared_loading()}
        </p>
      </PageContainer>
    )
  }

  if (error) {
    return (
      <PageContainer width="4xl">
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      </PageContainer>
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
    },
    {
      id: 'bots',
      label: m.platform_tab_bots(),
      icon: Bot,
      count: data.bots.length,
    },
    {
      id: 'conversations',
      label: m.admin_widgets_activity_recent_conversations_title(),
      icon: MessageSquare,
    },
    {
      id: 'knowledge-bases',
      label: m.platform_tab_knowledge_bases(),
      icon: BookOpen,
      count: data.knowledge_bases.length,
    },
    {
      id: 'templates',
      label: m.platform_tab_templates(),
      icon: FileText,
      count: data.templates.length,
    },
    {
      id: 'usage',
      label: m.platform_org_tab_usage(),
      icon: BarChart3,
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
      search: { tab, range: tab === 'usage' && range !== '30d' ? range : undefined },
    })
  }

  return (
    <PageContainer width="4xl" gap="8">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-display-bold text-gray-900">
            {data.org.name}
          </h1>
          <div className="mt-2 flex items-center gap-2 flex-wrap text-sm text-gray-600">
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
          variant="outline"
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
      {activeTab === 'bots' && (
        <BotsSection bots={data.bots} orgId={orgId} fmtDate={fmtDate} />
      )}
      {activeTab === 'conversations' && (
        <ConversationsSection bots={data.bots} initialWidgetId={search.widgetId} />
      )}
      {activeTab === 'knowledge-bases' && (
        <KnowledgeBasesSection
          knowledgeBases={data.knowledge_bases}
          fmtDate={fmtDate}
        />
      )}
      {activeTab === 'templates' && (
        <TemplatesSection templates={data.templates} fmtDate={fmtDate} />
      )}
      {activeTab === 'usage' && (
        <UsageSection orgId={orgId} range={range} fmtDate={fmtDate} />
      )}
      {activeTab === 'danger' && <TenantDangerZone org={data.org} />}
    </PageContainer>
  )
}

// Cross-tenant widget conversation browser for platform staff. Mirrors the
// tenant-side ActivityTab pattern (row list + transcript drawer — a deliberate
// exception to the "admin detail = separate route" rule, valid for chat
// transcripts; see SPEC-WIDGET-ACTIVITY-001).

function ConversationsSection({
  bots,
  initialWidgetId,
}: {
  bots: PlatformBot[]
  initialWidgetId?: string
}) {
  const auth = useAuth()
  const [selected, setSelected] = useState(
    () => bots.find((b) => b.id === initialWidgetId)?.id ?? bots[0]?.id ?? '',
  )
  const [openConvId, setOpenConvId] = useState<number | null>(null)

  const convsQuery = useQuery({
    queryKey: ['platform-bot-conversations', selected],
    queryFn: async () =>
      apiFetch<PlatformBotConversationItem[]>(
        `/api/admin/platform/bots/${selected}/conversations?limit=50`,
      ),
    enabled: auth.isAuthenticated && !!selected,
  })
  const conversations = Array.isArray(convsQuery.data) ? convsQuery.data : []

  if (bots.length === 0) {
    return (
      <p className="text-sm text-gray-600">{m.platform_no_bots()}</p>
    )
  }

  return (
    <section className="space-y-4">
      {bots.length > 1 && (
        <div className="max-w-sm space-y-1.5">
          <Label htmlFor="platform-bot-select">{m.platform_col_bot()}</Label>
          <Select
            id="platform-bot-select"
            value={selected}
            onChange={(e) => {
              setSelected(e.target.value)
              setOpenConvId(null)
            }}
          >
            {bots.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </Select>
        </div>
      )}

      {convsQuery.isLoading && (
        <p className="text-sm text-gray-600">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_shared_loading()}
        </p>
      )}
      {convsQuery.isError && (
        <p className="text-sm text-[var(--color-destructive)]">
          Kon gesprekken niet laden.
        </p>
      )}
      {!convsQuery.isLoading && conversations.length === 0 && !convsQuery.isError && (
        <p className="text-sm text-gray-600">
          Nog geen gesprekken voor deze bot.
        </p>
      )}
      {conversations.length > 0 && (
        <ul className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {conversations.map((c) => (
            <li key={c.id}>
              <Button
                type="button"
                variant="outline"
                onClick={() => setOpenConvId(c.id)}
                className="klai-hover h-auto w-full justify-start rounded-none px-2 py-3.5 text-left"
              >
                <MessageSquare className="h-4 w-4 mt-0.5 text-gray-500 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="truncate text-sm text-gray-900">
                    {c.first_user_query || (
                      <span className="text-gray-600 italic">
                        (geen vraag opgeslagen)
                      </span>
                    )}
                  </p>
                  <p className="mt-0.5 flex items-center gap-2 text-xs text-gray-600">
                    <span>{fmtDate(c.started_at)}</span>
                    <span>·</span>
                    <span>
                      {c.message_count === 1
                        ? '1 bericht'
                        : `${c.message_count} berichten`}
                    </span>
                    {c.language_detected && (
                      <>
                        <span>·</span>
                        <span className="uppercase">{c.language_detected}</span>
                      </>
                    )}
                  </p>
                </div>
              </Button>
            </li>
          ))}
        </ul>
      )}

      {openConvId !== null && (
        <PlatformConversationDrawer
          widgetId={selected}
          convId={openConvId}
          onClose={() => setOpenConvId(null)}
        />
      )}
    </section>
  )
}

/**
 * REQ-3 (SPEC-CHAT-QUALITY-LOOP-001): the nightly judge's verdict via the
 * platform cross-tenant sidecar endpoint. Local shape, mirroring the
 * tenant-side drawer in widgets/…/ActivityTab.tsx — deliberately not shared.
 */
interface PlatformConversationQuality {
  outcome: string
  failure_category: string | null
  reasoning: string | null
  confidence: string | null
  suggested_action: string | null
  judged_at: string | null
}

/** Outcome → existing Badge semantic variant; no ad-hoc colors. */
const OUTCOME_BADGE_VARIANT: Record<string, 'success' | 'warning' | 'secondary'> = {
  resolved: 'success',
  partially_resolved: 'success',
  escalated: 'warning',
  unresolved: 'secondary',
  abandoned_early: 'secondary',
  out_of_scope: 'secondary',
}

/** Judge verdict panel. Renders only when a judgment exists — a 404
 * ("not judged yet") is normal and shows nothing. */
function QualityPanel({ quality }: { quality: PlatformConversationQuality }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
      <Badge variant={OUTCOME_BADGE_VARIANT[quality.outcome] ?? 'secondary'}>
        {quality.outcome}
      </Badge>
      {quality.reasoning && (
        <p className="mt-2 text-xs leading-5 text-gray-600">{quality.reasoning}</p>
      )}
      {quality.suggested_action && (
        <p className="mt-1.5 text-xs leading-5 text-gray-700">{quality.suggested_action}</p>
      )}
    </div>
  )
}

function PlatformConversationDrawer({
  widgetId,
  convId,
  onClose,
}: {
  widgetId: string
  convId: number
  onClose: () => void
}) {
  const auth = useAuth()
  const query = useQuery({
    queryKey: ['platform-bot-conversation', widgetId, convId],
    queryFn: async () =>
      apiFetch<PlatformBotConversationDetail>(
        `/api/admin/platform/bots/${widgetId}/conversations/${convId}`,
      ),
    enabled: auth.isAuthenticated,
  })
  // 404 = not judged yet (normal state): retry off, error never surfaced.
  const qualityQuery = useQuery({
    queryKey: ['platform-bot-conversation-quality', widgetId, convId],
    queryFn: () =>
      apiFetch<PlatformConversationQuality>(
        `/api/admin/platform/bots/${widgetId}/conversations/${convId}/quality`,
      ),
    enabled: !!convId && auth.isAuthenticated,
    retry: false,
  })
  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} aria-hidden />
      <div className="relative h-full w-full max-w-lg bg-white overflow-y-auto">
        <div className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-gray-200 bg-white px-5 py-3.5">
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">
              Gesprek #{convId}
            </p>
            {query.data && (
              <p className="text-xs text-gray-600">
                {fmtDate(query.data.started_at)} ·{' '}
                {query.data.message_count === 1
                  ? '1 bericht'
                  : `${query.data.message_count} berichten`}
              </p>
            )}
          </div>
          <Button
            type="button"
            onClick={onClose}
            variant="outline"
            size="icon"
            className="h-8 w-8 text-gray-500"
            aria-label="Sluiten"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        <div className="px-5 py-4 space-y-3">
          {qualityQuery.data && <QualityPanel quality={qualityQuery.data} />}
          {query.isLoading && (
            <p className="text-sm text-gray-600">
              <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
              Laden…
            </p>
          )}
          {query.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              Kon gesprek niet laden.
            </p>
          )}
          {query.data?.messages.map((msg) => (
            <div
              key={msg.id}
              className={
                msg.role === 'user'
                  ? 'ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-gray-900 px-4 py-2.5 text-sm text-white whitespace-pre-wrap'
                  : 'mr-auto max-w-[85%] rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-2.5 text-sm text-gray-900 whitespace-pre-wrap'
              }
            >
              {msg.content}
              {msg.role === 'assistant' &&
                msg.sources &&
                msg.sources.length > 0 && (
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {msg.sources.map((s) => (
                      <li key={`${msg.id}-${s.label}`}>
                        {/* REQ-9: only http/https schemes render as anchors */}
                        {_isSafeHttpUrl(s.url) ? (
                          <a
                            href={s.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={s.title}
                            className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[0.6875rem] text-gray-700 klai-hover"
                          >
                            <span className="font-medium">({s.label})</span>
                            <span className="truncate max-w-[12rem]">{s.title}</span>
                          </a>
                        ) : (
                          <span
                            title={s.title}
                            className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[0.6875rem] text-gray-700"
                          >
                            <span className="font-medium">({s.label})</span>
                            <span className="truncate max-w-[12rem]">{s.title}</span>
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              {msg.role === 'assistant' && msg.rating && (
                msg.rating === 'thumbsUp' ? (
                  <ThumbsUp
                    role="img"
                    aria-label="Door klant beoordeeld met duim omhoog"
                    className="mt-2 h-3.5 w-3.5 text-[var(--color-success-text)]"
                  />
                ) : (
                  <ThumbsDown
                    role="img"
                    aria-label="Door klant beoordeeld met duim omlaag"
                    className="mt-2 h-3.5 w-3.5 text-[var(--color-destructive)]"
                  />
                )
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
