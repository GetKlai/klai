import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, BookOpen, PlusCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
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
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { Tooltip } from '@/components/ui/tooltip'

type GapsSearch = { days?: number; gapType?: string }
const VALID_DAYS = new Set([7, 14, 30, 60, 90])

export const Route = createFileRoute('/app/gaps/')({
  validateSearch: (search: Record<string, unknown>): GapsSearch => ({
    days: VALID_DAYS.has(Number(search.days)) ? Number(search.days) : undefined,
    gapType: search.gapType === 'hard' || search.gapType === 'soft' ? (search.gapType as string) : undefined,
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

function GapsPage() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  // SPEC-PORTAL-UNIFY-KB-001: kb.gaps capability gate.
  // Admins bypass through hasCapability; users without kb.gaps see a grayed unavailable state.
  const hasGapsCapability = user?.hasCapability('kb.gaps') === true
  const navigate = useNavigate({ from: '/app/gaps/' })

  const { days: daysParam, gapType: gapTypeParam } = Route.useSearch()
  const days = daysParam ?? 30
  const gapType = gapTypeParam ?? ''
  const [activePicker, setActivePicker] = useState<string | null>(null)

  const { data, isLoading } = useQuery<GapsResponse>({
    queryKey: ['app-gaps', days, gapType],
    queryFn: async () => {
      const params = new URLSearchParams({ days: String(days), limit: '100' })
      if (gapType) params.set('gap_type', gapType)
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

  const orgKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => kb.owner_type === 'org')

  if (!hasGapsCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="page-title text-xl/none font-semibold text-gray-900">
            {m.gaps_page_title()}
          </h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-400">
            {m.capability_tooltip_knowledge_only()}
          </p>
        </Tooltip>
      </div>
    )
  }

  const gaps = data?.gaps ?? []

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10">
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.gaps_page_title()}
          </h1>
        </div>
        <Button variant="ghost" size="sm" asChild>
          <Link to="/app/knowledge">
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.knowledge_page_intro_heading()}
          </Link>
        </Button>
      </div>

      <p className="text-gray-400 mb-6 leading-relaxed">
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
              <DataTableHead className="w-36">{m.gaps_column_nearest_kb()}</DataTableHead>
              <DataTableHead align="right" className="w-20">{m.gaps_column_count()}</DataTableHead>
              <DataTableHead align="right" className="w-28">{m.gaps_column_last()}</DataTableHead>
              <DataTableHead align="right" className="w-12" />
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {gaps.map((gap) => {
              const rowKey = `${gap.query_text}-${gap.gap_type}`
              return (
                <DataTableRow key={rowKey}>
                  <DataTableCell className="truncate" title={gap.query_text}>
                    {gap.query_text}
                  </DataTableCell>
                  <DataTableCell>
                    <Badge variant={gap.gap_type === 'hard' ? 'destructive' : 'warning'}>
                      {gap.gap_type === 'hard' ? m.gaps_type_hard() : m.gaps_type_soft()}
                    </Badge>
                  </DataTableCell>
                  <DataTableCell className="text-gray-400">
                    {gap.nearest_kb_slug ?? '—'}
                  </DataTableCell>
                  <DataTableCell align="right" className="font-medium tabular-nums">
                    {gap.occurrence_count}
                  </DataTableCell>
                  <DataTableCell align="right" className="whitespace-nowrap tabular-nums text-gray-400">
                    {new Date(gap.last_occurred).toLocaleDateString()}
                  </DataTableCell>
                  <DataTableCell align="right">
                    {gap.gap_type === 'soft' && gap.nearest_kb_slug ? (
                      <RowActionGroup>
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
                      </RowActionGroup>
                    ) : activePicker === rowKey ? (
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
                    ) : (
                      <RowActionGroup>
                        <BorderedRowActionIconButton
                          icon={BookOpen}
                          tone="primary"
                          label={m.gaps_action_pick_kb()}
                          onClick={() => setActivePicker(rowKey)}
                        />
                      </RowActionGroup>
                    )}
                  </DataTableCell>
                </DataTableRow>
              )
            })}
          </DataTableBody>
        </DataTable>
      )}
    </div>
  )
}
