import { Fragment, useState, type ReactNode } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, MessageSquare, ThumbsDown, ThumbsUp } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { MultiSelect } from '@/components/ui/multi-select'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import { BorderedRowActionIconButton } from '@/components/ui/row-action'
import { Select } from '@/components/ui/select'
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
  BANDS,
  BAND_LABEL,
  CAUSES,
  CAUSE_LABEL,
  FAILURE_CATEGORIES,
  FAILURE_CATEGORY_LABEL,
  OUTCOMES,
  OUTCOME_BADGE_VARIANT,
  OUTCOME_LABEL,
  VERDICT_LABEL,
  useActivityConversations,
  useActivityQueueCount,
  useActivitySummary,
  type ConversationBand,
} from '@/features/chat-activity'
import { CalibrationPanel } from '@/features/chat-activity/CalibrationPanel'
import * as m from '@/paraglide/messages'
import {
  DAYS,
  ofSet,
  parseActivitySearch,
  RATINGS,
  stringifyActivitySearch,
  type ActivitySearch,
} from './-search'

// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the knowledge admin's conversation work
// queue. Filters are URL search state (a filtered list is shareable) and the
// list itself is server-paginated by cursor, so there is no client-side search.

// Language codes are data, not copy: rendered as-is.
const LANGUAGES = ['nl', 'en', 'de', 'fr']
const SORTS = ['newest', 'worst'] as const

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

// No verdict->variant mapping exists yet in badgeVariant.ts, so this stays a
// small local map (SPEC-KNOWLEDGE-ACTIVITY-001 §4.3 expanded row).
const VERDICT_BADGE_VARIANT: Record<string, 'success' | 'warning' | 'destructive' | 'secondary'> = {
  correct: 'success',
  incomplete: 'warning',
  wrong: 'destructive',
  not_a_fault: 'secondary',
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

/** A labelled cluster of filter controls (SPEC §4.3: Signalen/Gebruikersfeedback/
    Beoordeling), so the source of each filter signal reads at a glance. */
function FilterGroup({ caption, children }: { caption: string; children: ReactNode }) {
  return (
    <div className="space-y-2">
      <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-muted-foreground)]">
        {caption}
      </p>
      <div className="flex flex-wrap items-end gap-4">{children}</div>
    </div>
  )
}

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
    sort: search.sort,
    cursor: search.cursor,
  })
  const queueCount = useActivityQueueCount()
  const summary = useActivitySummary(search.days)

  // Which rows show their judge/review detail block; local-only (not URL
  // state — a rendering detail, not a shareable filter).
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const toggleExpanded = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

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

  const outcomeOptions = OUTCOMES.map((value) => ({ value, label: OUTCOME_LABEL[value]() }))
  const failureCategoryOptions = FAILURE_CATEGORIES.map((value) => ({
    value,
    label: FAILURE_CATEGORY_LABEL[value](),
  }))
  const causeOptions = CAUSES.map((value) => ({ value, label: CAUSE_LABEL[value]() }))
  const bandOptions = BANDS.map((value) => ({ value, label: BAND_LABEL[value]() }))

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

      <div className="space-y-4">
        <FilterGroup caption={m.activity_filter_group_signals()}>
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
            <Label htmlFor="activity-band">{m.activity_filter_band()}</Label>
            <MultiSelect
              id="activity-band"
              options={bandOptions}
              value={search.band}
              onChange={(value) => setFilters({ band: value as ConversationBand[] })}
              placeholder={m.activity_filter_all()}
              className="w-48"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="activity-judge">{m.activity_filter_judge_outcome()}</Label>
            <MultiSelect
              id="activity-judge"
              options={outcomeOptions}
              value={search.judge_outcome}
              onChange={(value) => setFilters({ judge_outcome: value })}
              placeholder={m.activity_filter_all()}
              className="w-56"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="activity-failure-category">{m.activity_filter_failure_category()}</Label>
            <MultiSelect
              id="activity-failure-category"
              options={failureCategoryOptions}
              value={search.failure_category}
              onChange={(value) => setFilters({ failure_category: value })}
              placeholder={m.activity_filter_all()}
              className="w-56"
            />
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
        </FilterGroup>

        <FilterGroup caption={m.activity_filter_group_feedback()}>
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
        </FilterGroup>

        <FilterGroup caption={m.activity_filter_group_review()}>
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
            <Label htmlFor="activity-cause">{m.activity_review_cause_label()}</Label>
            <MultiSelect
              id="activity-cause"
              options={causeOptions}
              value={search.cause}
              onChange={(value) => setFilters({ cause: value })}
              placeholder={m.activity_filter_all()}
              className="w-48"
            />
          </div>
        </FilterGroup>

        <div className="flex flex-wrap items-end gap-4">
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
                <DataTableHead className="w-12" />
                <DataTableHead>{m.activity_col_question()}</DataTableHead>
                <DataTableHead className="w-20">{m.activity_col_language()}</DataTableHead>
                <DataTableHead className="w-28">{m.activity_col_band()}</DataTableHead>
                <DataTableHead className="w-44">{m.activity_col_judge()}</DataTableHead>
                <DataTableHead className="w-40">{m.activity_col_ratings()}</DataTableHead>
                <DataTableHead className="w-40">{m.activity_col_review()}</DataTableHead>
                <DataTableHead align="right" className="w-36 whitespace-nowrap">
                  {m.activity_col_time()}
                </DataTableHead>
              </DataTableRow>
            </DataTableHeader>
            <DataTableBody>
              {items.map((item) => {
                const isOpen = expanded.has(item.id)
                return (
                  <Fragment key={item.id}>
                    <DataTableRow
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
                      <DataTableCell onClick={(e) => e.stopPropagation()}>
                        <BorderedRowActionIconButton
                          action={isOpen ? 'collapse' : 'expand'}
                          label={isOpen ? m.activity_details_hide() : m.activity_details_show()}
                          aria-expanded={isOpen}
                          onClick={() => toggleExpanded(item.id)}
                        />
                      </DataTableCell>
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
                                {(FAILURE_CATEGORY_LABEL[item.judge.failure_category] ??
                                  (() => item.judge!.failure_category as string))()}
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
                        {item.review.status === 'reviewed' ? (
                          <div className="space-y-0.5">
                            <div>
                              {item.review.worst_verdict
                                ? (VERDICT_LABEL[item.review.worst_verdict] ??
                                    (() => item.review.worst_verdict as string))()
                                : m.activity_review_reviewed()}
                            </div>
                            {item.review.causes.length > 0 ? (
                              <div className="text-xs text-gray-500">
                                {item.review.causes
                                  .map((cause) => (CAUSE_LABEL[cause] ?? (() => cause))())
                                  .join(', ')}
                              </div>
                            ) : null}
                          </div>
                        ) : (
                          m.activity_review_unreviewed()
                        )}
                      </DataTableCell>
                      <DataTableCell align="right" className="whitespace-nowrap tabular-nums text-gray-600">
                        {formatRelativeTime(item.last_message_at)}
                      </DataTableCell>
                    </DataTableRow>
                    {isOpen ? (
                      <DataTableRow>
                        <DataTableCell colSpan={8} className="bg-gray-50 align-top">
                          <div className="space-y-3 py-1">
                            {item.judge ? (
                              <div className="space-y-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge variant={OUTCOME_BADGE_VARIANT[item.judge.outcome] ?? 'secondary'}>
                                    {(OUTCOME_LABEL[item.judge.outcome] ?? (() => item.judge!.outcome))()}
                                  </Badge>
                                  {item.judge.failure_category ? (
                                    <span className="text-xs text-gray-600">
                                      {(FAILURE_CATEGORY_LABEL[item.judge.failure_category] ??
                                        (() => item.judge!.failure_category as string))()}
                                    </span>
                                  ) : null}
                                  {item.judge.confidence ? (
                                    <span className="text-xs text-gray-500">
                                      {m.activity_judge_confidence({
                                        level: (BAND_LABEL[item.judge.confidence as ConversationBand] ??
                                          (() => item.judge!.confidence as string))(),
                                      })}
                                    </span>
                                  ) : null}
                                </div>
                                {item.judge.reasoning ? (
                                  <p className="text-xs leading-5 text-gray-600">{item.judge.reasoning}</p>
                                ) : null}
                              </div>
                            ) : null}
                            <div className="space-y-2">
                              {item.review.reviews.length === 0 ? (
                                <p className="text-xs text-gray-600">{m.activity_reviews_empty()}</p>
                              ) : (
                                item.review.reviews.map((review, i) => (
                                  <div
                                    key={`${item.id}-review-${i}`}
                                    className="flex flex-wrap items-center gap-2 text-xs"
                                  >
                                    <Badge variant={VERDICT_BADGE_VARIANT[review.verdict] ?? 'secondary'}>
                                      {(VERDICT_LABEL[review.verdict] ?? (() => review.verdict))()}
                                    </Badge>
                                    {review.cause !== 'none' ? (
                                      <span className="text-gray-600">
                                        {(CAUSE_LABEL[review.cause] ?? (() => review.cause))()}
                                      </span>
                                    ) : null}
                                    {/* Name and time render independently: a review outlives a
                                        deleted reviewer (reviewer_name null) and keeps its date. */}
                                    {review.reviewer_name ? (
                                      <span className="text-gray-500">
                                        {m.activity_review_by({ name: review.reviewer_name })}
                                      </span>
                                    ) : null}
                                    {review.reviewed_at ? (
                                      <span className="text-gray-500">{formatRelativeTime(review.reviewed_at)}</span>
                                    ) : null}
                                    {review.note ? <span className="text-gray-700">{review.note}</span> : null}
                                  </div>
                                ))
                              )}
                            </div>
                            <Button asChild variant="outline" size="sm">
                              <Link
                                to="/app/knowledge/activity/$conversationId"
                                params={{ conversationId: String(item.id) }}
                                search={{ back: stringifyActivitySearch(search) }}
                              >
                                {m.activity_open_conversation()}
                              </Link>
                            </Button>
                          </div>
                        </DataTableCell>
                      </DataTableRow>
                    ) : null}
                  </Fragment>
                )
              })}
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
