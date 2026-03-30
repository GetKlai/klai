import { useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, BookOpen, PlusCircle } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { STORAGE_KEYS } from '@/lib/storage'

export const Route = createFileRoute('/app/gaps/')({
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
  const token = auth.user?.access_token
  const isAdmin = sessionStorage.getItem(STORAGE_KEYS.isAdmin) === 'true'
  const navigate = useNavigate()

  const [days, setDays] = useState(30)
  const [gapType, setGapType] = useState<string>('')
  const [activePicker, setActivePicker] = useState<string | null>(null)

  const { data, isLoading } = useQuery<GapsResponse>({
    queryKey: ['app-gaps', token, days, gapType],
    queryFn: async () => {
      const params = new URLSearchParams({ days: String(days), limit: '100' })
      if (gapType) params.set('gap_type', gapType)
      const res = await fetch(`${API_BASE}/api/app/gaps?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        queryLogger.warn('Gaps fetch failed', { status: res.status })
        throw new Error(`${res.status}`)
      }
      return res.json() as Promise<GapsResponse>
    },
    enabled: !!token && isAdmin,
    retry: false,
  })

  const { data: kbsData } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases-for-gaps', token],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error(`${res.status}`)
      return res.json() as Promise<KBsResponse>
    },
    enabled: !!token && isAdmin,
    retry: false,
  })

  const orgKbs = (kbsData?.knowledge_bases ?? []).filter((kb) => kb.owner_type === 'org')

  if (!isAdmin) {
    return (
      <div className="p-8 max-w-2xl">
        <p className="text-[var(--color-muted-foreground)]">Admin access required.</p>
      </div>
    )
  }

  const gaps = data?.gaps ?? []

  return (
    <div className="p-8 max-w-4xl">
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <AlertTriangle className="h-7 w-7 text-[var(--color-purple-deep)]" />
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
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
            onChange={(e) => setDays(Number(e.target.value))}
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
            onChange={(e) => setGapType(e.target.value)}
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
        <Card>
          <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.gaps_column_query()}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.gaps_column_type()}
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.gaps_column_nearest_kb()}
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.gaps_column_count()}
                  </th>
                  <th className="px-4 py-3 text-right text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.gaps_column_last()}
                  </th>
                  <th className="px-4 py-3 w-10" />
                </tr>
              </thead>
              <tbody>
                {gaps.map((gap, i) => {
                  const rowKey = `${gap.query_text}-${gap.gap_type}`
                  return (
                    <tr
                      key={rowKey}
                      className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                    >
                      <td className="px-6 py-3 text-[var(--color-purple-deep)] max-w-xs truncate">
                        {gap.query_text}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${GAP_TYPE_CLASSES[gap.gap_type] ?? ''}`}>
                          {gap.gap_type === 'hard' ? m.gaps_type_hard() : m.gaps_type_soft()}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-[var(--color-muted-foreground)]">
                        {gap.nearest_kb_slug ?? '\u2014'}
                      </td>
                      <td className="px-4 py-3 text-right font-medium text-[var(--color-purple-deep)]">
                        {gap.occurrence_count}
                      </td>
                      <td className="px-4 py-3 text-right text-[var(--color-muted-foreground)]">
                        {new Date(gap.last_occurred).toLocaleDateString()}
                      </td>
                      <td className="px-4 py-3 text-right">
                        {gap.gap_type === 'soft' && gap.nearest_kb_slug ? (
                          <button
                            onClick={() =>
                              void navigate({
                                to: '/app/docs/$kbSlug',
                                params: { kbSlug: gap.nearest_kb_slug! },
                              })
                            }
                            aria-label={m.gaps_action_add()}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 ml-auto"
                          >
                            <PlusCircle className="h-3.5 w-3.5" />
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
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 ml-auto"
                          >
                            <BookOpen className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
