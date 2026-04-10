import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Lock, BookOpen, Plus, AlertTriangle, Pencil, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip } from '@/components/ui/tooltip'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'

export const Route = createFileRoute('/app/knowledge/')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgePage />
    </ProductGuard>
  ),
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  docs_enabled: boolean
  owner_type: string
  owner_user_id: string | null
  default_org_role: string | null
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

interface KBStatsSummary {
  items: number
  connectors: number
  gaps_7d: number
  usage_30d: number
}

interface KBStatsSummaryResponse {
  stats: Record<string, KBStatsSummary>
}

type VisibilityMode = 'public' | 'org' | 'restricted'

// Mirrors deriveVisibilityMode() in routes/app/knowledge/$kbSlug/members.tsx.
// The backend stores `visibility` as 'public' or 'internal'; the distinction
// between "organisation-wide" and "restricted" comes from default_org_role.
function deriveVisibilityMode(kb: Pick<KnowledgeBase, 'visibility' | 'default_org_role'>): VisibilityMode {
  if (kb.visibility === 'public') return 'public'
  if (kb.default_org_role) return 'org'
  return 'restricted'
}

function visibilityLabel(mode: VisibilityMode): string {
  switch (mode) {
    case 'public':
      return m.knowledge_sharing_visibility_public()
    case 'org':
      return m.knowledge_sharing_visibility_org()
    case 'restricted':
      return m.knowledge_sharing_visibility_restricted()
  }
}

/**
 * Compact stats line shown under a KB name. Each metric is omitted
 * when its count is zero so a KB with no activity stays clean.
 * The `variant` prop controls the wording: the personal singleton
 * uses "items onthouden" to match the chat-memory concept, regular
 * KBs use the neutral "items".
 */
function KBMetaText({
  stats,
  variant = 'regular',
}: {
  stats: KBStatsSummary | undefined
  variant?: 'regular' | 'personal'
}) {
  if (!stats) return null
  const parts: string[] = []
  if (stats.items > 0) {
    parts.push(
      variant === 'personal'
        ? m.knowledge_page_meta_items_personal({ count: String(stats.items) })
        : m.knowledge_page_meta_items({ count: String(stats.items) }),
    )
  }
  if (stats.connectors > 0) {
    parts.push(m.knowledge_page_meta_connectors({ count: String(stats.connectors) }))
  }
  if (stats.usage_30d > 0) {
    parts.push(m.knowledge_page_meta_usage({ count: String(stats.usage_30d) }))
  }
  if (stats.gaps_7d > 0) {
    parts.push(m.knowledge_page_meta_gaps({ count: String(stats.gaps_7d) }))
  }
  if (parts.length === 0) return null
  return (
    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5 truncate">
      {parts.join(' · ')}
    </p>
  )
}

interface GapSummary {
  total_7d: number
  hard_7d: number
  soft_7d: number
}

function KnowledgePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const myUserId = auth.user?.profile?.sub
  const navigate = useNavigate()

  const { user: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.isAdmin === true

  const [search, setSearch] = useState('')

  const { data: gapSummary } = useQuery<GapSummary>({
    queryKey: ['gap-summary'],
    queryFn: async () => {
      try {
        return await apiFetch<GapSummary>(`/api/app/gaps/summary`, token)
      } catch (err) {
        queryLogger.warn('Gap summary fetch failed', { err })
        throw err
      }
    },
    enabled: !!token && isAdmin,
    retry: false,
  })

  const { data: kbsData, isLoading: kbsLoading, error: kbsError, refetch: refetchKbs } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => {
      try {
        return await apiFetch<KBsResponse>(`/api/app/knowledge-bases`, token)
      } catch (err) {
        queryLogger.warn('Knowledge bases fetch failed', { err })
        throw err
      }
    },
    enabled: !!token,
    retry: false,
  })

  // Per-KB aggregate stats for the list view (items, connectors, gaps, usage).
  // Runs in parallel with the KBs query; UI renders progressively as it arrives.
  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: async () => {
      try {
        return await apiFetch<KBStatsSummaryResponse>(
          `/api/app/knowledge-bases/stats-summary`,
          token,
        )
      } catch (err) {
        queryLogger.warn('Knowledge bases stats summary fetch failed', { err })
        throw err
      }
    },
    enabled: !!token,
    retry: false,
  })

  const statsBySlug = statsData?.stats ?? {}

  const allKbs = kbsData?.knowledge_bases ?? []
  const orgKbs = allKbs.filter((kb) => kb.owner_type === 'org')
  const allPersonalKbs = allKbs.filter(
    (kb) => kb.owner_type === 'user' && kb.owner_user_id === myUserId,
  )
  // First personal KB = the "default personal KB" represented by the singleton row.
  // Any extra personal KBs render in the created-KBs list below.
  const defaultPersonalKb = allPersonalKbs[0] ?? null
  const extraPersonalKbs = allPersonalKbs.slice(1)

  // Combined list of user/admin-created KBs (org-owned + extra personal).
  // The first personal KB is excluded because it is the fixed singleton row.
  const createdKbs = [...orgKbs, ...extraPersonalKbs]
  const filteredCreatedKbs = search.trim()
    ? createdKbs.filter((kb) => {
        const q = search.toLowerCase()
        return (
          kb.name.toLowerCase().includes(q) ||
          (kb.description?.toLowerCase().includes(q) ?? false)
        )
      })
    : createdKbs

  const createdCount = createdKbs.length

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.knowledge_page_intro_heading()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!kbsLoading && createdCount > 0 && m.knowledge_page_stat_org({ count: String(createdCount) })}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {/* Gaps-knop — admin only, altijd zichtbaar.
              Count-badge verschijnt alleen als er open gaps zijn. */}
          {isAdmin && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void navigate({ to: '/app/gaps' })}
              className="text-[var(--color-warning)] border-[var(--color-warning)]/40 hover:bg-[var(--color-warning)]/5"
            >
              <AlertTriangle className="h-4 w-4 mr-2" />
              {m.gaps_page_title()}
              {gapSummary != null && gapSummary.total_7d > 0 && (
                <span className="ml-2 inline-flex items-center justify-center min-w-5 h-5 px-1.5 rounded-full text-xs font-medium bg-[var(--color-warning)]/15 text-[var(--color-warning)] tabular-nums">
                  {gapSummary.total_7d}
                </span>
              )}
            </Button>
          )}
          <Button size="sm" onClick={() => void navigate({ to: '/app/knowledge/new' })}>
            <Plus className="h-4 w-4 mr-2" />
            {m.knowledge_page_kbs_create()}
          </Button>
        </div>
      </div>

      {/* KB list */}
      {kbsError ? (
        <QueryErrorState error={kbsError instanceof Error ? kbsError : new Error(String(kbsError))} onRetry={() => void refetchKbs()} />
      ) : kbsLoading ? (
        <div className="flex flex-col gap-2 pt-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 rounded bg-[var(--color-secondary)] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-4">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={m.knowledge_page_search_placeholder()}
            className="h-8 text-sm max-w-xs"
          />

          <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
            <thead>
              <tr className="border-b border-[var(--color-border)]">
                <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em]">
                  {m.docs_kb_name_label()}
                </th>
                <th className="py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-28">
                  {m.docs_kb_visibility_label()}
                </th>
                <th className="py-3 text-right w-12" />
              </tr>
            </thead>
            <tbody>
              {/* ── Vaste kennisbanken — staan altijd bovenaan, niet gefilterd door search.
                   Amber accent balk links + pl-4 indent markeert ze visueel als default. */}

              {/* Persoonlijke kennisbank — linkt naar de echte default KB als die bestaat */}
              <tr className="border-b border-[var(--color-border)]">
                <td className="py-4 pl-4 pr-4 align-top shadow-[inset_3px_0_0_0_var(--color-rl-accent)]">
                  <div className="flex items-start gap-2">
                    <Lock className="h-4 w-4 mt-0.5 text-[var(--color-muted-foreground)] shrink-0" />
                    <div className="min-w-0">
                      <Link
                        to="/app/knowledge/$kbSlug"
                        params={{ kbSlug: defaultPersonalKb?.slug ?? 'personal' }}
                        className="font-medium text-[var(--color-foreground)] hover:underline"
                      >
                        {m.knowledge_page_personal_heading()}
                      </Link>
                      <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                        {m.knowledge_page_personal_body()}
                      </p>
                      {defaultPersonalKb && (
                        <KBMetaText
                          stats={statsBySlug[defaultPersonalKb.slug]}
                          variant="personal"
                        />
                      )}
                    </div>
                  </div>
                </td>
                <td className="py-4 pr-4 align-top w-28">
                  <span className="text-xs text-[var(--color-muted-foreground)]">
                    {m.docs_kb_visibility_private()}
                  </span>
                </td>
                <td className="py-4 align-top text-right w-12">
                  {defaultPersonalKb && (
                    <div className="flex items-start justify-end gap-2 mt-px">
                      <Tooltip label={m.docs_kb_edit_label()}>
                        <button
                          onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/settings', params: { kbSlug: defaultPersonalKb.slug } })}
                          aria-label={m.docs_kb_edit_label()}
                          className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                      </Tooltip>
                    </div>
                  )}
                </td>
              </tr>

              {/* Organisatie kennisbank — altijd aanwezig */}
              <tr className="border-b border-[var(--color-border)]">
                <td className="py-4 pl-4 pr-4 align-top shadow-[inset_3px_0_0_0_var(--color-rl-accent)]">
                  <div className="flex items-start gap-2">
                    <Users className="h-4 w-4 mt-0.5 text-[var(--color-muted-foreground)] shrink-0" />
                    <div className="min-w-0">
                      <Link
                        to="/app/knowledge/$kbSlug"
                        params={{ kbSlug: 'org' }}
                        className="font-medium text-[var(--color-foreground)] hover:underline"
                      >
                        {m.knowledge_page_org_heading()}
                      </Link>
                      <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                        {m.knowledge_page_org_body()}
                      </p>
                    </div>
                  </div>
                </td>
                <td className="py-4 pr-4 align-top w-28">
                  <span className="text-xs text-[var(--color-muted-foreground)]">
                    {visibilityLabel('org')}
                  </span>
                </td>
                <td className="py-4 align-top text-right w-12" />
              </tr>

              {/* ── Aangemaakte kennisbanken — gefilterd door search ─── */}
              {filteredCreatedKbs.map((kb) => {
                const isPersonal = kb.owner_type === 'user'
                const label = isPersonal
                  ? m.docs_kb_visibility_private()
                  : visibilityLabel(deriveVisibilityMode(kb))
                return (
                  <tr key={kb.id} className="border-b border-[var(--color-border)] last:border-b-0">
                    <td className="py-4 pr-4 align-top">
                      <div className="flex items-start gap-2">
                        <BookOpen className="h-4 w-4 mt-0.5 text-[var(--color-muted-foreground)] shrink-0" />
                        <div className="min-w-0">
                          <Link
                            to="/app/knowledge/$kbSlug"
                            params={{ kbSlug: kb.slug }}
                            className="font-medium text-[var(--color-foreground)] hover:underline"
                          >
                            {kb.name}
                          </Link>
                          {kb.description && (
                            <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5 truncate">
                              {kb.description}
                            </p>
                          )}
                          <KBMetaText
                            stats={statsBySlug[kb.slug]}
                            variant={isPersonal ? 'personal' : 'regular'}
                          />
                        </div>
                      </div>
                    </td>
                    <td className="py-4 pr-4 align-top w-28">
                      <span className="text-xs text-[var(--color-muted-foreground)]">
                        {label}
                      </span>
                    </td>
                    <td className="py-4 align-top text-right w-12">
                      <div className="flex items-start justify-end gap-2 mt-px">
                        <Tooltip label={m.docs_kb_edit_label()}>
                          <button
                            onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/settings', params: { kbSlug: kb.slug } })}
                            aria-label={m.docs_kb_edit_label()}
                            className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                        </Tooltip>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {createdKbs.length > 0 && filteredCreatedKbs.length === 0 && (
            <p className="py-4 text-sm text-[var(--color-muted-foreground)]">
              {m.knowledge_page_search_empty()}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
