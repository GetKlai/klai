import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Globe, Lock, Plus, AlertTriangle, Eye, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tooltip } from '@/components/ui/tooltip'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'

export const Route = createFileRoute('/app/knowledge/')({
  component: () => (
    <ProductGuard product="knowledge">
      <KnowledgePage />
    </ProductGuard>
  ),
})

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

interface GapSummary {
  total_7d: number
  hard_7d: number
  soft_7d: number
}

// ---------------------------------------------------------------------------
// Visibility
// ---------------------------------------------------------------------------

type VisibilityMode = 'public' | 'org' | 'restricted'

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

function VisibilityIcon({ mode, className }: { mode: VisibilityMode; className?: string }) {
  switch (mode) {
    case 'public':
      return <Globe className={className} />
    case 'org':
      return <Users className={className} />
    case 'restricted':
      return <Lock className={className} />
  }
}

// ---------------------------------------------------------------------------
// KBMetaText
// ---------------------------------------------------------------------------

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
  if (stats.connectors > 0) parts.push(m.knowledge_page_meta_connectors({ count: String(stats.connectors) }))
  if (stats.usage_30d > 0) parts.push(m.knowledge_page_meta_usage({ count: String(stats.usage_30d) }))
  if (stats.gaps_7d > 0) parts.push(m.knowledge_page_meta_gaps({ count: String(stats.gaps_7d) }))
  if (parts.length === 0) return null
  return (
    <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5 truncate">
      {parts.join(' · ')}
    </p>
  )
}

// ---------------------------------------------------------------------------
// KbRow
// ---------------------------------------------------------------------------

function KbRow({
  kb,
  stats,
  isDefault,
  variant,
  subtitle,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  isDefault: boolean
  variant: 'regular' | 'personal'
  subtitle?: string
}) {
  const isPersonal = variant === 'personal'
  const mode: VisibilityMode = isPersonal ? 'restricted' : deriveVisibilityMode(kb)
  const label = visibilityLabel(mode)
  const description = subtitle ?? kb.description

  return (
    <tr className="border-b border-[var(--color-border)] last:border-b-0">
      <td className={`py-4 pr-4 align-top ${isDefault ? 'pl-4 shadow-[inset_3px_0_0_0_var(--color-rl-accent)]' : ''}`}>
        <div className="flex items-start gap-2">
          <VisibilityIcon mode={mode} className="h-4 w-4 mt-0.5 text-[var(--color-muted-foreground)] shrink-0" />
          <div className="min-w-0">
            <Link
              to="/app/knowledge/$kbSlug/overview"
              params={{ kbSlug: kb.slug }}
              className="font-medium text-[var(--color-foreground)] hover:underline"
            >
              {kb.name}
            </Link>
            {description && (
              <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5 truncate">
                {description}
              </p>
            )}
            <KBMetaText stats={stats} variant={variant} />
            <span className="md:hidden inline-block mt-1 text-xs text-[var(--color-muted-foreground)]">
              {label}
            </span>
          </div>
        </div>
      </td>
      <td className="hidden md:table-cell py-4 pr-4 align-top w-28">
        <span className="text-xs text-[var(--color-muted-foreground)]">{label}</span>
      </td>
      <td className="py-4 align-top text-right w-12">
        <Tooltip label={m.docs_kb_view_label()}>
          <Link
            to="/app/knowledge/$kbSlug/overview"
            params={{ kbSlug: kb.slug }}
            aria-label={m.docs_kb_view_label()}
            className="inline-flex items-center justify-center text-[var(--color-muted-foreground)] transition-opacity hover:opacity-70"
          >
            <Eye className="h-4 w-4" />
          </Link>
        </Tooltip>
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// KnowledgePage
// ---------------------------------------------------------------------------

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
    queryFn: () => apiFetch<GapSummary>('/api/app/gaps/summary', token),
    enabled: !!token && isAdmin,
    retry: false,
  })

  const {
    data: kbsData,
    isLoading: kbsLoading,
    error: kbsError,
    refetch: refetchKbs,
  } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: () => apiFetch<KBsResponse>('/api/app/knowledge-bases', token),
    enabled: !!token,
    retry: false,
  })

  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: () => apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary', token),
    enabled: !!token,
    retry: false,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []

  const personalKb = allKbs.find((kb) => kb.slug === `personal-${myUserId}` && kb.owner_type === 'user')
  const orgKb = allKbs.find((kb) => kb.slug === 'org' && kb.owner_type === 'org')
  const defaultSlugs = new Set([personalKb?.slug, orgKb?.slug].filter(Boolean))

  const createdKbs = allKbs.filter((kb) => !defaultSlugs.has(kb.slug))
  const filteredCreatedKbs = search.trim()
    ? createdKbs.filter((kb) => {
        const q = search.toLowerCase()
        return kb.name.toLowerCase().includes(q) || (kb.description?.toLowerCase().includes(q) ?? false)
      })
    : createdKbs

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {m.knowledge_page_intro_heading()}
          </h1>
          {!kbsLoading && createdKbs.length > 0 && (
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.knowledge_page_stat_org({ count: String(createdKbs.length) })}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
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

      {kbsError ? (
        <QueryErrorState
          error={kbsError instanceof Error ? kbsError : new Error(String(kbsError))}
          onRetry={() => void refetchKbs()}
        />
      ) : kbsLoading ? (
        <div className="flex flex-col gap-2 pt-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 rounded bg-[var(--color-secondary)] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-3">
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
                <th className="hidden md:table-cell py-3 pr-4 text-left text-xs font-medium text-[var(--color-rl-dark-30)] uppercase tracking-[0.04em] w-28">
                  {m.docs_kb_visibility_label()}
                </th>
                <th className="py-3 text-right w-12" />
              </tr>
            </thead>
            <tbody>
              {personalKb && (
                <KbRow
                  kb={personalKb}
                  stats={statsBySlug[personalKb.slug]}
                  isDefault
                  variant="personal"
                  subtitle={m.knowledge_page_personal_body()}
                />
              )}
              {orgKb && (
                <KbRow
                  kb={orgKb}
                  stats={statsBySlug[orgKb.slug]}
                  isDefault
                  variant="regular"
                  subtitle={m.knowledge_page_org_body()}
                />
              )}
              {filteredCreatedKbs.map((kb) => (
                <KbRow
                  key={kb.id}
                  kb={kb}
                  stats={statsBySlug[kb.slug]}
                  isDefault={false}
                  variant={kb.owner_type === 'user' ? 'personal' : 'regular'}
                />
              ))}
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
