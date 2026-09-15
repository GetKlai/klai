import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, BookOpen, Check, MessageCircle, PlusCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
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
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { Tooltip } from '@/components/ui/tooltip'
import { PageContainer } from '@/components/ui/page-container'
import { appNavActivityIsVisible } from '@/routes/app/-app-tools'

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
  gap_type: string
  top_score: number | null
  nearest_kb_slug: string | null
  occurrence_count: number
  last_occurred: string
  language: string | null
  source: 'automatic' | 'review'
  conversation_id: number | null
  resolved_at: string | null
  resolved_by: 'rescorer' | 'review' | 'manual' | null
  resolved_by_name: string | null
}

/** Relative timestamps for the closed-row line; same approach as
    knowledge/activity/index.tsx (not shared -- the two routes don't share a
    lib module today and this is the only other caller). */
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

/** "rescorer" and "beoordeling/review" are fixed labels; only 'manual' names
    the actual colleague who closed it. */
function closerLabel(gap: GapRow): string {
  if (gap.resolved_by === 'rescorer') return m.gaps_resolved_by_rescorer()
  if (gap.resolved_by === 'review') return m.gaps_resolved_by_review()
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
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasActivityCapability,
  })
  const canDrillIntoActivity = appNavActivityIsVisible({
    hasCapability: (cap) => user?.hasCapability(cap) === true,
    unlockedFeatures: me?.platform_unlocked_features ?? [],
  })
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
    enabled: auth.isAuthenticated && hasGapsCapability,
    retry: false,
  })

  const { data: kbsData } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases-for-gaps'],
    queryFn: async () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated && hasGapsCapability,
    retry: false,
  })

  const closeMutation = useMutation({
    mutationFn: (gap: GapRow) =>
      apiFetch<{ resolved: number }>('/api/app/gaps/resolve', {
        method: 'POST',
        body: JSON.stringify({
          query_text: gap.query_text,
          gap_type: gap.gap_type,
          language: gap.language,
        }),
      }),
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
  // The API groups gaps by (question, type, language), so the language filter
  // options come from what is actually loaded rather than a fixed nl/en list;
  // the current search value is kept even if the loaded page has no row for it.
  const languageOptions = Array.from(
    new Set(gaps.map((gap) => gap.language).filter((code): code is string => Boolean(code))),
  ).sort()
  if (language && !languageOptions.includes(language)) languageOptions.push(language)

  return (
    <PageContainer width="3xl">
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl font-display-bold text-gray-900">
            {m.gaps_page_title()}
          </h1>
        </div>
        <Button variant="outline" size="sm" asChild>
          <Link to="/app/knowledge">
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.knowledge_page_intro_heading()}
          </Link>
        </Button>
      </div>

      <p className="text-gray-600 mb-6 leading-relaxed">
        {m.gaps_index_card_body()}
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
      ) : gaps.length === 0 ? (
        <ListEmptyState icon={AlertTriangle} title={m.gaps_empty_state()} />
      ) : (
        <DataTable className="table-fixed">
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.gaps_column_query()}</DataTableHead>
              <DataTableHead className="w-24">{m.gaps_column_type()}</DataTableHead>
              <DataTableHead className="w-20">{m.gaps_column_language()}</DataTableHead>
              <DataTableHead className="w-28">{m.gaps_column_source()}</DataTableHead>
              <DataTableHead className="w-32">{m.gaps_column_nearest_kb()}</DataTableHead>
              <DataTableHead align="right" className="w-20">{m.gaps_column_count()}</DataTableHead>
              <DataTableHead align="right" className="w-28">{m.gaps_column_last()}</DataTableHead>
              <DataTableHead align="right" className="w-36" />
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {gaps.map((gap) => {
              // The API groups gaps by (question, type, language); the key must
              // include language or two gaps with the same question and type in
              // different languages collide and clobber each other's row state.
              const rowKey = `${gap.query_text}|${gap.gap_type}|${gap.language ?? ''}`
              const isResolved = gap.resolved_at != null
              return (
                <DataTableRow key={rowKey} confirming={closingKey === rowKey}>
                  <DataTableCell className="truncate" title={gap.query_text}>
                    <div className="truncate">{gap.query_text}</div>
                    {isResolved && (
                      <div className="mt-1 flex items-center gap-2">
                        <Badge variant="secondary">{m.gaps_status_resolved()}</Badge>
                        <span className="text-xs text-gray-500">
                          {m.gaps_resolved_line({
                            time: formatRelativeTime(gap.resolved_at!),
                            closer: closerLabel(gap),
                          })}
                        </span>
                      </div>
                    )}
                  </DataTableCell>
                  <DataTableCell>
                    <Badge variant={gap.gap_type === 'hard' ? 'destructive' : 'warning'}>
                      {gap.gap_type === 'hard' ? m.gaps_type_hard() : m.gaps_type_soft()}
                    </Badge>
                  </DataTableCell>
                  <DataTableCell className="text-gray-600">{gap.language ?? '–'}</DataTableCell>
                  <DataTableCell>
                    <Badge variant={gap.source === 'review' ? 'info' : 'secondary'}>
                      {gap.source === 'review' ? m.gaps_source_review() : m.gaps_source_automatic()}
                    </Badge>
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
                        {gap.conversation_id != null && canDrillIntoActivity && (
                          <BorderedRowActionIconButton
                            asChild
                            tone="neutral"
                            label={m.gaps_action_open_conversation()}
                          >
                            <Link
                              to="/app/knowledge/activity/$conversationId"
                              search={{ list: null }}
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
      )}
    </PageContainer>
  )
}
