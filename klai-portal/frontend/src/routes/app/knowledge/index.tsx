import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { BookOpen, FileText, Plus, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'

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

interface Notebook {
  id: string
  name: string
  description: string | null
  sources_count: number
  created_at: string
}

interface NotebookListResponse {
  items: Notebook[]
  total: number
}

const FOCUS_BASE = '/research/v1'

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, {
    day: 'numeric',
    month: 'short',
  })
}

// ---------------------------------------------------------------------------
// Collection Card
// ---------------------------------------------------------------------------

function CollectionCard({
  kb,
  stats,
  isDefault,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  isDefault: boolean
}) {
  return (
    <Link
      to="/app/knowledge/$kbSlug/overview"
      params={{ kbSlug: kb.slug }}
      className={`group flex flex-col gap-3 rounded-xl border p-5 transition-shadow hover:shadow-md ${
        isDefault
          ? 'border-l-[3px] border-l-[var(--color-rl-accent)] bg-[var(--color-secondary)]'
          : 'bg-[var(--color-secondary)]'
      }`}
    >
      <div className="flex items-center gap-2">
        {isDefault && (
          <span className="h-2 w-2 rounded-full bg-[var(--color-success)]" />
        )}
        <span className="font-medium text-[var(--color-foreground)] group-hover:text-[var(--color-rl-accent)] transition-colors">
          {kb.name}
        </span>
      </div>
      <div className="space-y-0.5 text-xs text-[var(--color-muted-foreground)]">
        {stats && stats.items > 0 && (
          <p>{m.knowledge_card_items({ count: String(stats.items) })}</p>
        )}
        {stats && stats.connectors > 0 && (
          <p>{m.knowledge_card_connectors({ count: String(stats.connectors) })}</p>
        )}
      </div>
      <span className="mt-auto text-xs text-[var(--color-rl-accent-dark)] group-hover:underline">
        {m.knowledge_card_manage()} →
      </span>
    </Link>
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

  const [menuOpen, setMenuOpen] = useState(false)

  // Knowledge bases
  const { data: kbsData, isLoading: kbsLoading, error: kbsError, refetch: refetchKbs } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => {
      try {
        return await apiFetch<KBsResponse>('/api/app/knowledge-bases', token)
      } catch (err) {
        queryLogger.warn('Knowledge bases fetch failed', { err })
        throw err
      }
    },
    enabled: !!token,
  })

  // Stats
  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: async () => {
      try {
        return await apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary', token)
      } catch (err) {
        queryLogger.warn('KB stats summary fetch failed', { err })
        throw err
      }
    },
    enabled: !!token,
  })

  // Notebooks (Focus)
  const hasChat = currentUser?.isAdmin || currentUser?.products.includes('chat')
  const { data: notebooksData } = useQuery<NotebookListResponse>({
    queryKey: ['focus-notebooks'],
    queryFn: async () => apiFetch<NotebookListResponse>(`${FOCUS_BASE}/notebooks`, token),
    enabled: !!token && !!hasChat,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []

  const personalKb = allKbs.find(
    (kb) => kb.slug === `personal-${myUserId}` && kb.owner_type === 'user',
  )
  const orgKb = allKbs.find(
    (kb) => kb.slug === 'org' && kb.owner_type === 'org',
  )
  const defaultSlugs = new Set([personalKb?.slug, orgKb?.slug].filter(Boolean))
  const otherKbs = allKbs.filter((kb) => !defaultSlugs.has(kb.slug))

  // Notebooks list
  const notebooks = notebooksData?.items ?? []

  // Docs-enabled KBs
  const docKbs = allKbs.filter((kb) => kb.docs_enabled)

  // Combined notes + docs list, sorted by recent
  type NoteItem = { type: 'notebook'; id: string; name: string; date: string } | { type: 'document'; slug: string; name: string }
  const noteItems: NoteItem[] = [
    ...notebooks.map((nb): NoteItem => ({ type: 'notebook', id: nb.id, name: nb.name, date: nb.created_at })),
    ...docKbs.map((kb): NoteItem => ({ type: 'document', slug: kb.slug, name: kb.name })),
  ]

  return (
    <div className="p-8 space-y-8 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
          {m.knowledge_page_title()}
        </h1>
        <div className="relative">
          <Button size="sm" onClick={() => setMenuOpen((v) => !v)}>
            <Plus className="h-4 w-4 mr-2" />
            {m.knowledge_new_button()}
            <ChevronDown className="h-3 w-3 ml-1" />
          </Button>
          {menuOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
              <div className="absolute right-0 top-full z-50 mt-1.5 w-48 rounded-xl border border-[var(--color-border)] bg-[var(--color-background)] py-1.5 shadow-lg">
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); void navigate({ to: '/app/knowledge/new' }) }}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-[var(--color-secondary)] transition-colors text-left"
                >
                  <Plus className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                  {m.knowledge_new_source()}
                </button>
                {hasChat && (
                  <button
                    type="button"
                    onClick={() => { setMenuOpen(false); void navigate({ to: '/app/focus/new' }) }}
                    className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-[var(--color-secondary)] transition-colors text-left"
                  >
                    <BookOpen className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                    {m.knowledge_new_notebook()}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Collections */}
      {kbsError ? (
        <QueryErrorState
          error={kbsError instanceof Error ? kbsError : new Error(String(kbsError))}
          onRetry={() => void refetchKbs()}
        />
      ) : kbsLoading ? (
        <div className="flex flex-col gap-2 pt-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-24 rounded-xl bg-[var(--color-secondary)] animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          {/* Section: Collecties */}
          <div>
            <h2 className="mb-4 text-sm font-medium text-[var(--color-muted-foreground)] uppercase tracking-[0.04em]">
              {m.knowledge_section_sources()}
            </h2>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {personalKb && (
                <CollectionCard kb={personalKb} stats={statsBySlug[personalKb.slug]} isDefault />
              )}
              {orgKb && (
                <CollectionCard kb={orgKb} stats={statsBySlug[orgKb.slug]} isDefault />
              )}
              {otherKbs.map((kb) => (
                <CollectionCard key={kb.id} kb={kb} stats={statsBySlug[kb.slug]} isDefault={false} />
              ))}
            </div>
          </div>

          {/* Section: Notities & documenten */}
          {noteItems.length > 0 && (
            <div>
              <h2 className="mb-4 text-sm font-medium text-[var(--color-muted-foreground)] uppercase tracking-[0.04em]">
                {m.knowledge_section_notes()}
              </h2>
              <div className="border-t border-b border-[var(--color-border)]">
                {noteItems.map((item) => (
                  <Link
                    key={item.type === 'notebook' ? item.id : item.slug}
                    to={
                      item.type === 'notebook'
                        ? '/app/focus/$notebookId'
                        : '/app/docs/$kbSlug'
                    }
                    params={
                      item.type === 'notebook'
                        ? { notebookId: item.id }
                        : { kbSlug: item.slug }
                    }
                    className="flex items-center gap-3 border-b border-[var(--color-border)] last:border-b-0 py-3 px-1 hover:bg-[var(--color-secondary)]/50 transition-colors"
                  >
                    {item.type === 'notebook' ? (
                      <BookOpen size={16} strokeWidth={1.5} className="text-[var(--color-muted-foreground)] shrink-0" />
                    ) : (
                      <FileText size={16} strokeWidth={1.5} className="text-[var(--color-muted-foreground)] shrink-0" />
                    )}
                    <span className="text-sm font-medium text-[var(--color-foreground)] truncate">
                      {item.name}
                    </span>
                    {item.type === 'notebook' && 'date' in item && (
                      <span className="ml-auto text-xs text-[var(--color-muted-foreground)] tabular-nums whitespace-nowrap">
                        {formatDate(item.date)}
                      </span>
                    )}
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
