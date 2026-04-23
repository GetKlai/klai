import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, BookOpen, PlusCircle } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
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
      <GapsPage />
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

const GAP_TYPE_CLASSES: Record<string, string> = {
  hard: 'bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]',
  soft: 'bg-[var(--color-warning)]/10 text-[var(--color-warning)]',
}

function GapsPage() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  const isAdmin = user?.isAdmin === true
  // SPEC-PORTAL-UNIFY-KB-001: kb.gaps capability gate.
  // Admins bypass; core/professional see grayed unavailable state.
  const hasGapsCapability = !user || user.hasCapability('kb.gaps')
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
    enabled: auth.isAuthenticated && isAdmin,
    retry: false,
  })

  const { data: kbsData } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases-for-gaps'],
    queryFn: async () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated && isAdmin,
    retry: false,
  })

  const orgKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => kb.owner_type === 'org')

  if (!isAdmin) {
    // Non-admins without kb.gaps capability see a grayed unavailable state (D4).
    if (!hasGapsCapability) {
      return (
        <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
          <div className="flex items-start gap-3 mb-4">
            <AlertTriangle className="h-7 w-7 text-[var(--color-foreground)]" />
            <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
              {m.gaps_page_title()}
            </h1>
          </div>
          <Tooltip label={m.capability_tooltip_knowledge_only()}>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.capability_tooltip_knowledge_only()}
            </p>
          </Tooltip>
        </div>
      )
    }
    return (
      <div className="p-6 max-w-2xl">
        <p className="text-[var(--color-muted-foreground)]">Admin access required.</p>
      </div>
    )
  }

  const gaps = data?.gaps ?? []

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-7 w-7 text-[var(--color-foreground)]" />
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

      <p className="text-[var(--color-muted-foreground)] mb-6 leading-relaxed">
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
        <div className="flex flex-col gap-2">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 rounded-lg bg-[var(--color-secondary)] animate-pulse" />
          ))}
        </div>
      ) : gaps.length === 0 ? (
        <Card>
          <CardContent className="pt-6 pb-6 text-center">
            <AlertTriangle className="h-8 w-8 text-[var(--color-muted-foreground)] mx-auto mb-2" />
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.gaps_empty_state()}
            </p>
          </CardContent>
        </Card>
      ) : (
        <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                {m.gaps_column_query()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-24">
                {m.gaps_column_type()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-36">
                {m.gaps_column_nearest_kb()}
              </th>
              <th className="py-3 pr-4 text-right text-xs font-medium text-gray-400 tracking-wide w-20">
                {m.gaps_column_count()}
              </th>
              <th className="py-3 pr-4 text-right text-xs font-medium text-gray-400 tracking-wide w-28">
                {m.gaps_column_last()}
              </th>
              <th className="py-3 text-right w-12" />
            </tr>
          </thead>
          <tbody>
            {gaps.map((gap) => {
              const rowKey = `${gap.query_text}-${gap.gap_type}`
              return (
                <tr
                  key={rowKey}
                  className="border-b border-[var(--color-border)] last:border-b-0"
                >
                  <td className="py-4 pr-4 align-top text-[var(--color-foreground)] truncate max-w-xs">
                    {gap.query_text}
                  </td>
                  <td className="py-4 pr-4 align-top w-24">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${GAP_TYPE_CLASSES[gap.gap_type] ?? ''}`}>
                      {gap.gap_type === 'hard' ? m.gaps_type_hard() : m.gaps_type_soft()}
                    </span>
                  </td>
                  <td className="py-4 pr-4 align-top text-[var(--color-muted-foreground)] w-36">
                    {gap.nearest_kb_slug ?? '\u2014'}
                  </td>
                  <td className="py-4 pr-4 align-top text-right font-medium text-[var(--color-foreground)] tabular-nums w-20">
                    {gap.occurrence_count}
                  </td>
                  <td className="py-4 pr-4 align-top text-right text-[var(--color-muted-foreground)] whitespace-nowrap tabular-nums w-28">
                    {new Date(gap.last_occurred).toLocaleDateString()}
                  </td>
                  <td className="py-4 align-top text-right w-12">
                    {gap.gap_type === 'soft' && gap.nearest_kb_slug ? (
                      <button
                        onClick={() =>
                          void navigate({
                            to: '/app/docs/$kbSlug',
                            params: { kbSlug: gap.nearest_kb_slug! },
                                                      })
                        }
                        aria-label={m.gaps_action_add()}
                        className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 ml-auto"
                      >
                        <PlusCircle className="h-4 w-4" />
                      </button>
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
                      <button
                        onClick={() => setActivePicker(rowKey)}
                        aria-label={m.gaps_action_pick_kb()}
                        className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 ml-auto"
                      >
                        <BookOpen className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
