import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, MessageSquare, ThumbsDown, ThumbsUp } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tooltip } from '@/components/ui/tooltip'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { fetchMe } from '@/lib/api-me'
import { appNavActivityIsVisible } from '@/routes/app/-app-tools'
import {
  OUTCOME_BADGE_VARIANT,
  useActivityConversations,
  useActivityQueueCount,
  useActivitySummary,
  type ConversationBand,
} from '@/features/chat-activity'
import { CalibrationPanel } from '@/features/chat-activity/CalibrationPanel'
import * as m from '@/paraglide/messages'
import {
  BANDS,
  DAYS,
  ofSet,
  OUTCOMES,
  parseActivitySearch,
  RATINGS,
  stringifyActivitySearch,
  type ActivitySearch,
} from './-search'

// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the knowledge admin's conversation work
// queue. Filters are URL search state (a filtered list is shareable) and the
// list itself is server-paginated by cursor, so there is no client-side search.

// Language and failure-category codes are data, not copy: rendered as-is.
const LANGUAGES = ['nl', 'en', 'de', 'fr']
const FAILURE_CATEGORIES = [
  'retrieval_miss',
  'retrieval_wrong',
  'generation_error',
  'policy_refusal',
  'scope_mismatch',
  'user_confusion',
] as const
const CAUSES = ['knowledge_missing', 'knowledge_wrong', 'behaviour'] as const
const SORTS = ['newest', 'worst'] as const

const CAUSE_LABEL: Record<(typeof CAUSES)[number], () => string> = {
  knowledge_missing: m.activity_cause_knowledge_missing,
  knowledge_wrong: m.activity_cause_knowledge_wrong,
  behaviour: m.activity_cause_behaviour,
}

const SORT_LABEL: Record<(typeof SORTS)[number], () => string> = {
  newest: m.activity_sort_newest,
  worst: m.activity_sort_worst,
}

const BAND_BADGE_VARIANT: Record<ConversationBand, 'success' | 'secondary' | 'warning'> = {
  high: 'success',
  medium: 'secondary',
  low: 'warning',
  unknown: 'secondary',
}

const BAND_LABEL: Record<ConversationBand, () => string> = {
  high: m.activity_band_high,
  medium: m.activity_band_medium,
  low: m.activity_band_low,
  unknown: m.activity_band_unknown,
}

const OUTCOME_LABEL: Record<string, () => string> = {
  resolved: m.activity_outcome_resolved,
  partially_resolved: m.activity_outcome_partially_resolved,
  escalated: m.activity_outcome_escalated,
  unresolved: m.activity_outcome_unresolved,
  abandoned_early: m.activity_outcome_abandoned_early,
  out_of_scope: m.activity_outcome_out_of_scope,
}

export const Route = createFileRoute('/app/knowledge/activity/')({
  validateSearch: parseActivitySearch,
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <ActivityPage />
      </RoleGuard>
    </ProductGuard>
  ),
})

export function ActivityPage() {
  const search = Route.useSearch()
  const navigate = useNavigate({ from: '/app/knowledge/activity/' })
  const { user } = useCurrentUser()
  // Same capability gate as /app/gaps: admins bypass through hasCapability.
  const hasActivityCapability = user?.hasCapability('kb.activity') === true
  // The screen also needs the tenant unlocks the sidebar checks
  // (appNavActivityIsVisible, -app-tools.ts): capability alone lets the nav
  // item stay hidden while the underlying queries still 403 without this.
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasActivityCapability,
  })
  const isUnlocked = appNavActivityIsVisible({
    hasCapability: (cap) => user?.hasCapability(cap) === true,
    unlockedFeatures: meQuery.data?.platform_unlocked_features ?? [],
  })

  const list = useActivityConversations({
    days: search.days,
    widgetId: search.widget_id,
    language: search.language,
    judgeOutcome: search.judge_outcome,
    failureCategory: search.failure_category,
    reviewStatus: search.review_status,
    cause: search.cause,
    band: search.band,
    rating: search.rating,
    queue: search.queue,
    sort: search.sort,
    cursor: search.cursor,
  })
  const queueCount = useActivityQueueCount()
  const summary = useActivitySummary(search.days)

  if (!hasActivityCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl/none font-semibold text-gray-900">
            {m.activity_page_title()}
          </h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-600">{m.capability_tooltip_knowledge_only()}</p>
        </Tooltip>
      </div>
    )
  }

  if (meQuery.isLoading) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }

  if (meQuery.isError) {
    // A failed /api/me read is not a missing unlock: say so and offer a retry.
    return (
      <PageContainer width="6xl" gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }

  if (!isUnlocked) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.activity_unlock_required()} />
      </PageContainer>
    )
  }

  // Every filter change clears the cursor: a filtered list restarts at its
  // first page, which is what makes the resulting URL shareable.
  const setFilters = (patch: Partial<ActivitySearch>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch, cursor: undefined }) })

  const items = list.data?.pages.flatMap((page) => page.items) ?? []
  const nextCursor = list.data?.pages.at(-1)?.next_cursor ?? null

  return (
    <PageContainer width="6xl" gap="6">
      <div>
        <PageHeader title={m.activity_page_title()} />
        <PageIntro className="mt-1">
          <p>{m.activity_page_intro()}</p>
          {queueCount.data ? (
            <p className="text-xs text-[var(--color-muted-foreground)]">
              {queueCount.data.count === 1
                ? m.activity_queue_count_one()
                : m.activity_queue_count_other({ count: String(queueCount.data.count) })}
            </p>
          ) : null}
        </PageIntro>
      </div>

      {summary.isError ? (
        <QueryErrorState error={summary.error} onRetry={() => void summary.refetch()} />
      ) : summary.data ? (
        <CalibrationPanel summary={summary.data} />
      ) : null}

      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="activity-days">{m.activity_filter_days()}</Label>
          <Select
            id="activity-days"
            value={String(search.days)}
            onChange={(e) => setFilters({ days: Number(e.target.value) })}
            className="w-auto"
          >
            {DAYS.map((days) => (
              <option key={days} value={String(days)}>
                {days}d
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-judge">{m.activity_filter_judge_outcome()}</Label>
          <Select
            id="activity-judge"
            value={search.judge_outcome ?? ''}
            onChange={(e) => setFilters({ judge_outcome: e.target.value || undefined })}
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            {OUTCOMES.map((outcome) => (
              <option key={outcome} value={outcome}>
                {(OUTCOME_LABEL[outcome] ?? (() => outcome))()}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-review">{m.activity_filter_review_status()}</Label>
          <Select
            id="activity-review"
            value={search.review_status ?? ''}
            onChange={(e) =>
              setFilters({
                review_status: ofSet(['unreviewed', 'reviewed'] as const, e.target.value),
              })
            }
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            <option value="unreviewed">{m.activity_review_unreviewed()}</option>
            <option value="reviewed">{m.activity_review_reviewed()}</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-band">{m.activity_filter_band()}</Label>
          <Select
            id="activity-band"
            value={search.band ?? ''}
            onChange={(e) => setFilters({ band: ofSet(BANDS, e.target.value) })}
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            {BANDS.map((band) => (
              <option key={band} value={band}>
                {BAND_LABEL[band]()}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-rating">{m.activity_filter_rating()}</Label>
          <Select
            id="activity-rating"
            value={search.rating ?? ''}
            onChange={(e) => setFilters({ rating: ofSet(RATINGS, e.target.value) })}
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            <option value="thumbsUp">{m.activity_rating_thumbs_up()}</option>
            <option value="thumbsDown">{m.activity_rating_thumbs_down()}</option>
            <option value="none">{m.activity_rating_none()}</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-language">{m.activity_filter_language()}</Label>
          <Select
            id="activity-language"
            value={search.language ?? ''}
            onChange={(e) => setFilters({ language: e.target.value || undefined })}
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            {LANGUAGES.map((language) => (
              <option key={language} value={language}>
                {language}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-failure-category">
            {m.activity_filter_failure_category()}
          </Label>
          <Select
            id="activity-failure-category"
            value={search.failure_category ?? ''}
            onChange={(e) =>
              setFilters({ failure_category: ofSet(FAILURE_CATEGORIES, e.target.value) })
            }
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            {FAILURE_CATEGORIES.map((category) => (
              <option key={category} value={category}>
                {category}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-cause">{m.activity_review_cause_label()}</Label>
          <Select
            id="activity-cause"
            value={search.cause ?? ''}
            onChange={(e) => setFilters({ cause: ofSet(CAUSES, e.target.value) })}
            className="w-auto"
          >
            <option value="">{m.activity_filter_all()}</option>
            {CAUSES.map((cause) => (
              <option key={cause} value={cause}>
                {CAUSE_LABEL[cause]()}
              </option>
            ))}
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="activity-sort">{m.activity_filter_sort()}</Label>
          <Select
            id="activity-sort"
            value={search.sort}
            onChange={(e) => setFilters({ sort: ofSet(SORTS, e.target.value) ?? 'newest' })}
            className="w-auto"
          >
            {SORTS.map((sort) => (
              <option key={sort} value={sort}>
                {SORT_LABEL[sort]()}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex items-center gap-2 pb-2">
          <Switch
            id="activity-queue"
            checked={search.queue}
            onCheckedChange={(checked) => setFilters({ queue: checked })}
          />
          <Label htmlFor="activity-queue">{m.activity_filter_queue()}</Label>
        </div>
        {search.widget_id ? (
          // The admin widget tab is what puts widget_id in the URL, and the app
          // role may not list widgets: the chip names it from the rows on
          // screen and falls back to the id while the list is still empty.
          <div className="flex items-center gap-1 pb-2">
            <Badge variant="secondary">{items[0]?.widget_name ?? search.widget_id}</Badge>
            <Button
              variant="link"
              size="sm"
              className="px-0"
              onClick={() => setFilters({ widget_id: undefined })}
            >
              {m.activity_filter_widget_clear()}
            </Button>
          </div>
        ) : null}
      </div>

      {list.isError ? (
        <QueryErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : list.isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : items.length === 0 ? (
        <ListEmptyState icon={MessageSquare} title={m.activity_empty()} />
      ) : (
        <>
          <DataTable className="table-fixed">
            <DataTableHeader>
              <DataTableRow>
                <DataTableHead>{m.activity_col_question()}</DataTableHead>
                <DataTableHead className="w-20">{m.activity_col_language()}</DataTableHead>
                <DataTableHead className="w-28">{m.activity_col_band()}</DataTableHead>
                <DataTableHead className="w-44">{m.activity_col_judge()}</DataTableHead>
                <DataTableHead className="w-24">{m.activity_col_ratings()}</DataTableHead>
                <DataTableHead className="w-36">{m.activity_col_review()}</DataTableHead>
                <DataTableHead align="right" className="w-36 whitespace-nowrap">
                  {m.activity_col_time()}
                </DataTableHead>
              </DataTableRow>
            </DataTableHeader>
            <DataTableBody>
              {items.map((item) => (
                <DataTableRow
                  key={item.id}
                  interactive
                  onClick={() =>
                    void navigate({
                      to: '/app/knowledge/activity/$conversationId',
                      params: { conversationId: String(item.id) },
                      // One flat string, never a nested object (main.tsx's
                      // stringifySearch only supports flat values) — the
                      // detail page parses it back for its back link.
                      search: { back: stringifyActivitySearch(search) },
                    })
                  }
                >
                  <DataTableCell title={item.first_user_query ?? undefined}>
                    {/* Clamp a child block, not the cell: -webkit-box on a td breaks table layout. */}
                    <div className="line-clamp-3">{item.first_user_query ?? '—'}</div>
                  </DataTableCell>
                  <DataTableCell className="text-gray-600">{item.language ?? '—'}</DataTableCell>
                  <DataTableCell>
                    {item.worst_band ? (
                      <Badge variant={BAND_BADGE_VARIANT[item.worst_band]}>
                        {BAND_LABEL[item.worst_band]()}
                      </Badge>
                    ) : (
                      '—'
                    )}
                  </DataTableCell>
                  <DataTableCell>
                    {item.judge ? (
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <Badge variant={OUTCOME_BADGE_VARIANT[item.judge.outcome] ?? 'secondary'}>
                          {(OUTCOME_LABEL[item.judge.outcome] ?? (() => item.judge!.outcome))()}
                        </Badge>
                        {item.judge.failure_category ? (
                          <span className="text-xs text-gray-600">
                            {item.judge.failure_category}
                          </span>
                        ) : null}
                      </span>
                    ) : (
                      '—'
                    )}
                  </DataTableCell>
                  <DataTableCell>
                    <span className="inline-flex items-center gap-3 tabular-nums">
                      <span
                        role="img"
                        aria-label={m.activity_thumbs_up()}
                        className="inline-flex items-center gap-1"
                      >
                        <ThumbsUp
                          aria-hidden="true"
                          className="h-3.5 w-3.5 text-[var(--color-success-text)]"
                        />
                        {item.ratings.up}
                      </span>
                      <span
                        role="img"
                        aria-label={m.activity_thumbs_down()}
                        className="inline-flex items-center gap-1"
                      >
                        <ThumbsDown
                          aria-hidden="true"
                          className="h-3.5 w-3.5 text-[var(--color-destructive)]"
                        />
                        {item.ratings.down}
                      </span>
                    </span>
                  </DataTableCell>
                  <DataTableCell className="text-gray-600">
                    {item.review.status === 'reviewed'
                      ? (item.review.worst_verdict ?? m.activity_review_reviewed())
                      : m.activity_review_unreviewed()}
                  </DataTableCell>
                  <DataTableCell align="right" className="whitespace-nowrap tabular-nums text-gray-600">
                    {formatRelativeTime(item.last_message_at)}
                  </DataTableCell>
                </DataTableRow>
              ))}
            </DataTableBody>
          </DataTable>
          {nextCursor ? (
            <div className="flex justify-center">
              <Button
                variant="outline"
                size="sm"
                disabled={list.isFetchingNextPage}
                onClick={() => {
                  void list.fetchNextPage()
                  setFilters({ cursor: nextCursor })
                }}
              >
                {m.activity_load_more()}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </PageContainer>
  )
}

/** Relative timestamps for the list; older than a week falls back to a date. */
function formatRelativeTime(isoString: string): string {
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
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
