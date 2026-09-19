import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, BookOpen, Check, FileText, MessageCircle, PlusCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs } from '@/components/ui/tabs'
import { Label } from '@/components/ui/label'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { Tooltip } from '@/components/ui/tooltip'
import { PageContainer } from '@/components/ui/page-container'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { appNavActivityIsVisible, appNavGapsIsVisible } from '@/routes/app/-app-tools'
import { TranscriptImportDialog } from './_components/TranscriptImportDialog'
import { caseSourceLabel, diagnosisLabel } from './-support-helpers'

type GapsSearch = { days?: number; gapType?: string; language?: string; include_resolved?: boolean }
const VALID_DAYS = new Set([7, 14, 30, 60, 90])
// BCP-47-ish: 2-3 letter language subtag, optional 2-4 letter script/region
// subtag, or the "undetermined" code. Matches the values the backend groups
// gaps by instead of a fixed nl/en allowlist.
const LANGUAGE_CODE_RE = /^[a-z]{2,3}(-[A-Za-z]{2,4})?$/
const isValidLanguageCode = (value: unknown): value is string =>
  typeof value === 'string' && (value === 'und' || LANGUAGE_CODE_RE.test(value))

export const Route = createFileRoute('/app/knowledge/gaps/')({
  validateSearch: (search: Record<string, unknown>): GapsSearch => ({
    days: VALID_DAYS.has(Number(search.days)) ? Number(search.days) : undefined,
    gapType: search.gapType === 'hard' || search.gapType === 'soft' ? (search.gapType as string) : undefined,
    language: isValidLanguageCode(search.language) ? search.language : undefined,
    include_resolved:
      search.include_resolved === true || search.include_resolved === 'true' ? true : undefined,
  }),
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <GapsPage />
      </RoleGuard>
    </ProductGuard>
  ),
})

interface GapRow {
  query_text: string
  // "hard"/"soft" for retrieval telemetry; "content" for support-case findings
  // (support-gap-detection.md: support findings use gap_type="content").
  gap_type: string
  top_score: number | null
  nearest_kb_slug: string | null
  occurrence_count: number
  last_occurred: string
  language: string | null
  source: 'automatic' | 'review' | 'support'
  conversation_id: number | null
  resolved_at: string | null
  resolved_by: 'rescorer' | 'review' | 'manual' | 'test' | null
  resolved_by_name: string | null
  // Support-case fields (null for automatic/review rows). diagnosis is one of
  // the six content diagnoses; support_case_ids links the evidence detail page.
  diagnosis: string | null
  audience: string | null
  support_case_ids: number[]
  support_sources?: string[]
  group_key?: string | null
  topic: { id: number; name: string } | null
}

interface TopicGroup {
  key: string
  kbSlug: string | null
  topicName: string
  needs: GapRow[]
}

function groupContentByTopic(gaps: GapRow[], ungroupedLabel: string): TopicGroup[] {
  const groups = new Map<string, TopicGroup>()
  for (const gap of gaps) {
    const kbSlug = gap.nearest_kb_slug
    const key = `${kbSlug ?? ''}::${gap.topic?.id ?? 'null'}`
    let group = groups.get(key)
    if (!group) {
      group = { key, kbSlug, topicName: gap.topic?.name ?? ungroupedLabel, needs: [] }
      groups.set(key, group)
    }
    group.needs.push(gap)
  }
  return Array.from(groups.values()).sort((a, b) => {
    if (a.kbSlug !== b.kbSlug) {
      if (a.kbSlug == null) return 1
      if (b.kbSlug == null) return -1
      return a.kbSlug.localeCompare(b.kbSlug)
    }
    const aUngrouped = a.topicName === ungroupedLabel
    const bUngrouped = b.topicName === ungroupedLabel
    if (aUngrouped !== bUngrouped) return aUngrouped ? 1 : -1
    return a.topicName.localeCompare(b.topicName)
  })
}

/** Relative timestamps for the closed-row line; same approach as
    knowledge/activity/index.tsx (not shared -- the two routes don't share a
    lib module today and this is the only other caller). */
function formatRelativeTime(isoString: string): string {
  // The portal language, not the browser's: mixed-language lines otherwise.
  const rtf = new Intl.RelativeTimeFormat(getLocale(), { numeric: 'auto' })
  const diffSeconds = (new Date(isoString).getTime() - Date.now()) / 1000
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31536000],
    ['month', 2592000],
    ['day', 86400],
    ['hour', 3600],
    ['minute', 60],
  ]
  for (const [unit, seconds] of units) {
    if (Math.abs(diffSeconds) >= seconds) {
      return rtf.format(Math.round(diffSeconds / seconds), unit)
    }
  }
  return rtf.format(Math.round(diffSeconds), 'second')
}

/** "rescorer", "beoordeling/review" and "testbericht" are fixed labels; only
    'manual' names the actual colleague who closed it. */
function closerLabel(gap: GapRow): string {
  if (gap.resolved_by === 'rescorer') return m.gaps_resolved_by_rescorer()
  if (gap.resolved_by === 'review') return m.gaps_resolved_by_review()
  if (gap.resolved_by === 'test') return m.gaps_resolved_by_test()
  return gap.resolved_by_name ?? ''
}

interface GapsResponse {
  gaps: GapRow[]
  total: number
}

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

export function GapsPage() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  const queryClient = useQueryClient()
  // SPEC-PORTAL-UNIFY-KB-001: kb.gaps capability gate.
  // Admins bypass through hasCapability; users without kb.gaps see a grayed unavailable state.
  const hasGapsCapability = user?.hasCapability('kb.gaps') === true
  const hasActivityCapability = user?.hasCapability('kb.activity') === true
  // The drill-in link must use the same predicate as the sidebar (-app-tools.ts):
  // capability alone is not enough when the tenant has not unlocked widgets +
  // knowledge_activity, or the link points at a screen that 403s.
  // The tenant unlocks decide both the drill-in link and whether this screen
  // is on at all (knowledge_gaps, off by default while it is unfinished).
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasGapsCapability,
  })
  const me = meQuery.data
  const access = {
    hasCapability: (cap: string) => user?.hasCapability(cap) === true,
    unlockedFeatures: me?.platform_unlocked_features ?? [],
  }
  const canDrillIntoActivity = hasActivityCapability && appNavActivityIsVisible(access)
  const gapsUnlocked = appNavGapsIsVisible(access)
  const navigate = useNavigate({ from: '/app/knowledge/gaps/' })

  const {
    days: daysParam,
    gapType: gapTypeParam,
    language: languageParam,
    include_resolved: includeResolvedParam,
  } = Route.useSearch()
  const days = daysParam ?? 30
  const gapType = gapTypeParam ?? ''
  const language = languageParam ?? ''
  const includeResolved = includeResolvedParam ?? false
  const [activePicker, setActivePicker] = useState<string | null>(null)
  const [closingKey, setClosingKey] = useState<string | null>(null)
  const [view, setView] = useState<'content' | 'signals'>('content')

  const { data, isLoading } = useQuery<GapsResponse>({
    queryKey: ['app-gaps', days, gapType, language, includeResolved],
    queryFn: async () => {
      const params = new URLSearchParams({ days: String(days), limit: '100' })
      if (gapType) params.set('gap_type', gapType)
      if (language) params.set('language', language)
      if (includeResolved) params.set('include_resolved', 'true')
      try {
        return await apiFetch<GapsResponse>(`/api/app/gaps?${params}`)
      } catch (err) {
        queryLogger.warn('Gaps fetch failed', { error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated && hasGapsCapability && gapsUnlocked,
    retry: false,
  })

  const { data: kbsData } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases-for-gaps'],
    queryFn: async () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated && hasGapsCapability && gapsUnlocked,
    retry: false,
  })

  const closeMutation = useMutation({
    mutationFn: (gap: GapRow) => {
      // Ordinary (automatic/review) closing is unchanged. A support finding is
      // grouped by diagnosis + nearest_kb + audience as well, so those values
      // are sent too — otherwise the resolve would also close a different
      // diagnosis or KB group for the same question (support-gap-detection.md).
      const body =
        gap.source === 'support'
          ? {
              query_text: gap.query_text,
              gap_type: gap.gap_type,
              language: gap.language,
              diagnosis: gap.diagnosis,
              audience: gap.audience,
              nearest_kb_slug: gap.nearest_kb_slug,
              group_key: gap.group_key,
            }
          : {
              query_text: gap.query_text,
              gap_type: gap.gap_type,
              language: gap.language,
            }
      return apiFetch<{ resolved: number }>('/api/app/gaps/resolve', {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['app-gaps'] }),
    onError: (err: unknown) => {
      queryLogger.warn('Gap close failed', { error: err })
      toast.error(m.gaps_close_failed())
    },
    onSettled: () => setClosingKey(null),
  })

  const orgKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => kb.owner_type === 'org')

  if (!hasGapsCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl/none font-semibold text-gray-900">
            {m.gaps_page_title()}
          </h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-600">
            {m.capability_tooltip_knowledge_only()}
          </p>
        </Tooltip>
      </div>
    )
  }

  const gaps = data?.gaps ?? []
  const contentGaps = gaps.filter((gap) => gap.source !== 'automatic')
  const signalGaps = gaps.filter((gap) => gap.source === 'automatic')
  const topicGroups = groupContentByTopic(contentGaps, m.gaps_topic_ungrouped())
  const activeRows = view === 'content' ? contentGaps : signalGaps
  const rows: Array<{ heading: TopicGroup } | { gap: GapRow }> =
    view === 'content'
      ? topicGroups.flatMap((group) => [
          { heading: group },
          ...group.needs.map((gap) => ({ gap })),
        ])
      : signalGaps.map((gap) => ({ gap }))
  // The API groups gaps by (question, type, language), so the language filter
  // options come from what is actually loaded rather than a fixed nl/en list;
  // the current search value is kept even if the loaded page has no row for it.
  const languageOptions = Array.from(
    new Set(gaps.map((gap) => gap.language).filter((code): code is string => Boolean(code))),
  ).sort()
  if (language && !languageOptions.includes(language)) languageOptions.push(language)

  // The unlock read decides everything below: show its own loading and
  // error states instead of an empty gaps list while it is unresolved.
  if (hasGapsCapability && meQuery.isLoading) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (hasGapsCapability && meQuery.isError) {
    return (
      <PageContainer width="6xl" gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }
  if (hasGapsCapability && me !== undefined && !gapsUnlocked) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.gaps_unlock_required()} />
      </PageContainer>
    )
  }

  return (
    <PageContainer width="6xl">
      <div className="flex flex-col gap-4 sm:flex-row sm:justify-between mb-6">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl font-display-bold text-gray-900">
            {m.gaps_page_title()}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TranscriptImportDialog orgKbs={orgKbs} />
          <Button variant="outline" size="sm" asChild>
            <Link to="/app/knowledge/gaps/support-cases">
              <FileText className="h-4 w-4 mr-2" />
              {m.support_cases_action_open()}
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to="/app/knowledge">
              <ArrowLeft className="h-4 w-4 mr-2" />
              {m.knowledge_page_intro_heading()}
            </Link>
          </Button>
        </div>
      </div>

      <Tabs
        className="mb-4"
        value={view}
        onValueChange={setView}
        tabs={[
          { id: 'content', label: m.gaps_view_content() },
          { id: 'signals', label: m.gaps_view_signals() },
        ]}
      />

      <p className="text-gray-600 mb-6 leading-relaxed">
        {view === 'content' ? m.gaps_index_card_body() : m.gaps_signals_note()}
      </p>

      {/* Filters */}
      <div className="flex items-end gap-4 mb-6">
        <div className="space-y-1.5">
          <Label htmlFor="gap-days">{m.gaps_filter_days()}</Label>
          <Select
            id="gap-days"
            value={String(days)}
            onChange={(e) => void navigate({ search: (prev) => ({ ...prev, days: Number(e.target.value) }) })}
            className="w-auto"
          >
            <option value="7">7d</option>
            <option value="14">14d</option>
            <option value="30">30d</option>
            <option value="60">60d</option>
            <option value="90">90d</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="gap-type">{m.gaps_filter_type()}</Label>
          <Select
            id="gap-type"
            value={gapType}
            onChange={(e) => void navigate({ search: (prev) => ({ ...prev, gapType: e.target.value || undefined }) })}
            className="w-auto"
          >
            <option value="">{m.gaps_filter_all()}</option>
            <option value="hard">{m.gaps_type_hard()}</option>
            <option value="soft">{m.gaps_type_soft()}</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="gap-language">{m.gaps_filter_language()}</Label>
          <Select
            id="gap-language"
            value={language}
            onChange={(e) => void navigate({ search: (prev) => ({ ...prev, language: e.target.value || undefined }) })}
            className="w-auto"
          >
            <option value="">{m.gaps_filter_all()}</option>
            {languageOptions.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-center gap-2 pb-2">
          <Switch
            id="gap-include-resolved"
            checked={includeResolved}
            onCheckedChange={(checked) =>
              void navigate({ search: (prev) => ({ ...prev, include_resolved: checked || undefined }) })
            }
          />
          <Label htmlFor="gap-include-resolved">{m.gaps_filter_show_resolved()}</Label>
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : activeRows.length === 0 ? (
        <ListEmptyState icon={AlertTriangle} title={m.gaps_empty_state()} />
      ) : (
        <div className="overflow-x-auto">
        <DataTable className="table-fixed min-w-5xl">
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.gaps_column_query()}</DataTableHead>
              <DataTableHead className="w-24">{m.gaps_column_type()}</DataTableHead>
              <DataTableHead className="w-20">{m.gaps_column_language()}</DataTableHead>
              <DataTableHead className="w-40">{m.gaps_column_source()}</DataTableHead>
              <DataTableHead className="w-32">{m.gaps_column_nearest_kb()}</DataTableHead>
              <DataTableHead align="right" className="w-20">{m.gaps_column_count()}</DataTableHead>
              <DataTableHead align="right" className="w-28">{m.gaps_column_last()}</DataTableHead>
              <DataTableHead align="right" className="w-36" />
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {rows.map((item) => {
              if ('heading' in item) {
                const group = item.heading
                return (
                  <DataTableRow key={`h:${group.key}`} className="bg-gray-50">
                    <DataTableCell colSpan={8}>
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-gray-900">{group.topicName}</span>
                        {group.kbSlug && (
                          <span className="text-xs text-gray-500">{group.kbSlug}</span>
                        )}
                        <Badge
                          variant="secondary"
                          aria-label={m.gaps_topic_need_count({ count: group.needs.length })}
                        >
                          {group.needs.length}
                        </Badge>
                      </div>
                    </DataTableCell>
                  </DataTableRow>
                )
              }
              const gap = item.gap
              const rowKey = JSON.stringify([
                gap.source, gap.query_text, gap.gap_type, gap.language,
                gap.diagnosis, gap.nearest_kb_slug, gap.audience, gap.group_key,
              ])
              const isResolved = gap.resolved_at != null
              return (
                <DataTableRow key={rowKey} confirming={closingKey === rowKey}>
                  <DataTableCell className="truncate" title={gap.query_text}>
                    <div className="truncate">{gap.query_text}</div>
                    {gap.source === 'support' && gap.diagnosis && (
                      <div className="mt-1 flex items-center gap-2">
                        <Badge variant="outline">{diagnosisLabel(gap.diagnosis)}</Badge>
                        {gap.audience && (
                          <span className="text-xs text-gray-500 truncate">{gap.audience}</span>
                        )}
                      </div>
                    )}
                    {isResolved && (
                      <div className="mt-1 flex items-center gap-2">
                        <Badge variant="secondary">{m.gaps_status_resolved()}</Badge>
                        <span className="text-xs text-gray-500">
                          {closerLabel(gap)
                            ? m.gaps_resolved_line({
                                time: formatRelativeTime(gap.resolved_at!),
                                closer: closerLabel(gap),
                              })
                            : m.gaps_resolved_line_no_actor({ time: formatRelativeTime(gap.resolved_at!) })}
                        </span>
                      </div>
                    )}
                  </DataTableCell>
                  <DataTableCell>
                    <Badge
                      variant={
                        gap.gap_type === 'hard'
                          ? 'destructive'
                          : gap.gap_type === 'content'
                            ? 'info'
                            : 'warning'
                      }
                    >
                      {gap.gap_type === 'hard'
                        ? m.gaps_type_hard()
                        : gap.gap_type === 'content'
                          ? m.gaps_type_content()
                          : m.gaps_type_soft()}
                    </Badge>
                  </DataTableCell>
                  <DataTableCell className="text-gray-600">{gap.language ?? '–'}</DataTableCell>
                  <DataTableCell>
                    <div className="flex flex-wrap gap-1">
                      {gap.source === 'support' && (gap.support_sources ?? []).length > 0
                        ? (gap.support_sources ?? []).map((source) => (
                            <Badge key={source} variant="info">{caseSourceLabel(source)}</Badge>
                          ))
                        : (
                            <Badge variant={gap.source === 'automatic' ? 'secondary' : 'info'}>
                              {gap.source === 'support'
                                ? m.gaps_source_support()
                                : gap.source === 'review'
                                  ? m.gaps_source_review()
                                  : m.gaps_source_automatic()}
                            </Badge>
                          )}
                    </div>
                  </DataTableCell>
                  <DataTableCell className="text-gray-600">
                    {gap.nearest_kb_slug ?? '—'}
                  </DataTableCell>
                  <DataTableCell align="right" className="font-medium tabular-nums">
                    {gap.occurrence_count}
                  </DataTableCell>
                  <DataTableCell align="right" className="whitespace-nowrap tabular-nums text-gray-600">
                    {new Date(gap.last_occurred).toLocaleDateString()}
                  </DataTableCell>
                  <DataTableCell align="right">
                    <InlineDeleteConfirm
                      isConfirming={closingKey === rowKey}
                      isPending={closeMutation.isPending}
                      label={m.gaps_close_confirm()}
                      cancelLabel={m.gaps_close_cancel()}
                      onConfirm={() => closeMutation.mutate(gap)}
                      onCancel={() => setClosingKey(null)}
                    >
                      <RowActionGroup>
                        {gap.source === 'support' && gap.support_case_ids.length > 0 && (
                          <BorderedRowActionIconButton
                            asChild
                            tone="neutral"
                            label={m.gaps_action_view_evidence()}
                          >
                            <Link
                              to="/app/knowledge/gaps/support-cases/$caseId"
                              params={{ caseId: String(gap.support_case_ids[0]) }}
                            >
                              <FileText />
                            </Link>
                          </BorderedRowActionIconButton>
                        )}
                        {gap.conversation_id != null && canDrillIntoActivity && (
                          <BorderedRowActionIconButton
                            asChild
                            tone="neutral"
                            label={m.gaps_action_open_conversation()}
                          >
                            <Link
                              to="/app/knowledge/activity/$conversationId"
                              params={{ conversationId: String(gap.conversation_id) }}
                            >
                              <MessageCircle />
                            </Link>
                          </BorderedRowActionIconButton>
                        )}
                        {gap.gap_type === 'soft' && gap.nearest_kb_slug ? (
                          <BorderedRowActionIconButton
                            icon={PlusCircle}
                            tone="primary"
                            label={m.gaps_action_add()}
                            onClick={() =>
                              void navigate({
                                to: '/app/docs/$kbSlug',
                                params: { kbSlug: gap.nearest_kb_slug! },
                              })
                            }
                          />
                        ) : (
                          <BorderedRowActionIconButton
                            icon={BookOpen}
                            tone="primary"
                            label={m.gaps_action_pick_kb()}
                            onClick={() => setActivePicker(rowKey)}
                          />
                        )}
                        {!isResolved && (
                          <BorderedRowActionIconButton
                            icon={Check}
                            tone="neutral"
                            label={m.gaps_action_close()}
                            onClick={() => setClosingKey(rowKey)}
                          />
                        )}
                      </RowActionGroup>
                    </InlineDeleteConfirm>
                    {activePicker === rowKey && (
                      <Select
                        value=""
                        onChange={(e) => {
                          if (e.target.value) {
                            void navigate({
                              to: '/app/docs/$kbSlug',
                              params: { kbSlug: e.target.value },
                            })
                            setActivePicker(null)
                          }
                        }}
                        onBlur={() => setActivePicker(null)}
                        className="w-32 text-xs"
                        autoFocus
                      >
                        <option value="">{m.gaps_action_pick_kb()}</option>
                        {orgKbs.map((kb) => (
                          <option key={kb.id} value={kb.slug}>
                            {kb.name}
                          </option>
                        ))}
                      </Select>
                    )}
                  </DataTableCell>
                </DataTableRow>
              )
            })}
          </DataTableBody>
        </DataTable>
        </div>
      )}
    </PageContainer>
  )
}
