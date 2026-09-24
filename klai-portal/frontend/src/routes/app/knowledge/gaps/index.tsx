import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, FileText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
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
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { Tooltip } from '@/components/ui/tooltip'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { appNavGapsIsVisible } from '@/routes/app/-app-tools'
import { TranscriptImportDialog } from './_components/TranscriptImportDialog'
import { audienceLabel, caseSourceLabel, diagnosisLabel } from './-support-helpers'
import { gapGroupDigest } from './-gap-identity'
import { closerLabel, formatRelativeTime, type GapRow, type GapsResponse, type KBsResponse } from './-gap-types'

type GapSource = GapRow['source']
type GapsSearch = { days?: number; language?: string; include_resolved?: boolean }
const VALID_DAYS = new Set([7, 14, 30, 60, 90])
// BCP-47-ish: 2-3 letter language subtag, optional 2-4 letter script/region
// subtag, or the "undetermined" code. Matches the values the backend groups
// gaps by instead of a fixed nl/en allowlist. Not a visible filter any more
// (SPEC-KNOWLEDGE-ACTIVITY-001 §4.9 three-filter redesign), but a bookmarked
// ?language= URL still forwards to the fetch.
const LANGUAGE_CODE_RE = /^[a-z]{2,3}(-[A-Za-z]{2,4})?$/
const isValidLanguageCode = (value: unknown): value is string =>
  typeof value === 'string' && (value === 'und' || LANGUAGE_CODE_RE.test(value))

export const Route = createFileRoute('/app/knowledge/gaps/')({
  validateSearch: (search: Record<string, unknown>): GapsSearch => ({
    days: VALID_DAYS.has(Number(search.days)) ? Number(search.days) : undefined,
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

export function GapsPage() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  // SPEC-PORTAL-UNIFY-KB-001: kb.gaps capability gate.
  // Admins bypass through hasCapability; users without kb.gaps see a grayed unavailable state.
  const hasGapsCapability = user?.hasCapability('kb.gaps') === true
  // The tenant unlocks decide both whether this screen is on at all
  // (knowledge_gaps, off by default while it is unfinished) and the sidebar.
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasGapsCapability,
  })
  const me = meQuery.data
  const gapsUnlocked = appNavGapsIsVisible({
    hasCapability: (cap) => user?.hasCapability(cap) === true,
    unlockedFeatures: me?.platform_unlocked_features ?? [],
  })
  const navigate = useNavigate({ from: '/app/knowledge/gaps/' })

  const {
    days: daysParam,
    language: languageParam,
    include_resolved: includeResolvedParam,
  } = Route.useSearch()
  const days = daysParam ?? 30
  const language = languageParam ?? ''
  const includeResolved = includeResolvedParam ?? false
  // Client-side only: the API does not group by source, so narrowing to one
  // source never needs a refetch and does not need to survive a bookmark.
  const [source, setSource] = useState<GapSource | ''>('')

  const { data, isLoading, isError, error, refetch } = useQuery<GapsResponse>({
    queryKey: ['app-gaps', days, language, includeResolved],
    queryFn: async () => {
      const params = new URLSearchParams({ days: String(days), limit: '100' })
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

  // The unlock read decides everything below: show its own loading and
  // error states instead of an empty gaps list while it is unresolved.
  if (meQuery.isLoading) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (meQuery.isError) {
    return (
      <PageContainer width="6xl" gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }
  if (me !== undefined && !gapsUnlocked) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.gaps_unlock_required()} />
      </PageContainer>
    )
  }

  const gaps = data?.gaps ?? []
  const rows = source ? gaps.filter((gap) => gap.source === source) : gaps

  const headerActions = (
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
  )

  return (
    <PageContainer width="6xl" gap="6">
      <PageHeader title={m.gaps_page_title()} description={m.gaps_index_card_body()} actions={headerActions} />

      {/* Filters: bron, periode, status (SPEC-KNOWLEDGE-ACTIVITY-001 §4.9) */}
      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="gaps-filter-source">{m.gaps_filter_source()}</Label>
          <Select
            id="gaps-filter-source"
            value={source}
            onChange={(e) => setSource(e.target.value as GapSource | '')}
            className="w-auto"
          >
            <option value="">{m.gaps_filter_all()}</option>
            <option value="automatic">{m.gaps_source_automatic()}</option>
            <option value="review">{m.gaps_source_review()}</option>
            <option value="judge">{m.gaps_source_judge()}</option>
            <option value="support">{m.gaps_source_support()}</option>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="gaps-filter-days">{m.gaps_filter_days()}</Label>
          <Select
            id="gaps-filter-days"
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
          <Label htmlFor="gaps-filter-status">{m.gaps_filter_status()}</Label>
          <Select
            id="gaps-filter-status"
            value={includeResolved ? 'all' : 'open'}
            onChange={(e) =>
              void navigate({ search: (prev) => ({ ...prev, include_resolved: e.target.value === 'all' || undefined }) })
            }
            className="w-auto"
          >
            <option value="open">{m.gaps_status_open()}</option>
            <option value="all">{m.gaps_filter_all()}</option>
          </Select>
        </div>
      </div>

      {/* Table */}
      {isError ? (
        <QueryErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : rows.length === 0 ? (
        <ListEmptyState icon={AlertTriangle} title={m.gaps_empty_state()} />
      ) : (
        <div className="overflow-x-auto">
          <DataTable className="table-fixed min-w-4xl">
            <DataTableHeader>
              <DataTableRow>
                <DataTableHead>{m.gaps_column_subject()}</DataTableHead>
                <DataTableHead align="right" className="w-28">{m.gaps_column_occurrences()}</DataTableHead>
                <DataTableHead className="w-40">{m.gaps_column_source()}</DataTableHead>
                <DataTableHead className="w-32">{m.gaps_column_status()}</DataTableHead>
              </DataTableRow>
            </DataTableHeader>
            <DataTableBody>
              {rows.map((gap) => {
                const groupKey = gapGroupDigest(gap)
                const isResolved = gap.resolved_at != null
                return (
                  <DataTableRow
                    key={groupKey}
                    interactive
                    onClick={() => void navigate({ to: '/app/knowledge/gaps/$groupKey', params: { groupKey } })}
                  >
                    <DataTableCell className="truncate" title={gap.query_text}>
                      <Link
                        to="/app/knowledge/gaps/$groupKey"
                        params={{ groupKey }}
                        className="block truncate font-medium text-rl-dark"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {gap.query_text}
                      </Link>
                      {gap.source === 'support' && gap.diagnosis && (
                        <div className="mt-1 truncate text-xs text-gray-500">
                          {diagnosisLabel(gap.diagnosis)}
                          {gap.audience ? ` · ${audienceLabel(gap.audience)}` : ''}
                        </div>
                      )}
                    </DataTableCell>
                    <DataTableCell align="right" className="font-medium tabular-nums">
                      {gap.occurrence_count}
                    </DataTableCell>
                    <DataTableCell>
                      <div className="flex flex-wrap gap-1">
                        {gap.source === 'support' && (gap.support_sources ?? []).length > 0
                          ? (gap.support_sources ?? []).map((src) => (
                              <Badge key={src} variant="info">{caseSourceLabel(src)}</Badge>
                            ))
                          : (
                              <Badge variant={gap.source === 'automatic' ? 'secondary' : 'info'}>
                                {gap.source === 'support'
                                  ? m.gaps_source_support()
                                  : gap.source === 'review'
                                    ? m.gaps_source_review()
                                    : gap.source === 'judge'
                                      ? `${m.gaps_source_judge()}${gap.audience ? ` · ${audienceLabel(gap.audience)}` : ''}`
                                      : m.gaps_source_automatic()}
                              </Badge>
                            )}
                      </div>
                    </DataTableCell>
                    <DataTableCell>
                      <Badge variant={isResolved ? 'secondary' : 'outline'}>
                        {isResolved ? m.gaps_status_resolved() : m.gaps_status_open()}
                      </Badge>
                      {isResolved && (
                        <div className="mt-1 text-xs text-gray-500">
                          {closerLabel(gap)
                            ? m.gaps_resolved_line({ time: formatRelativeTime(gap.resolved_at!), closer: closerLabel(gap) })
                            : m.gaps_resolved_line_no_actor({ time: formatRelativeTime(gap.resolved_at!) })}
                        </div>
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
