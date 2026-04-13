import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { BookOpen, FileText, Plus, ChevronDown, ChevronRight } from 'lucide-react'
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

interface KBsResponse { knowledge_bases: KnowledgeBase[] }
interface KBStatsSummary { items: number; connectors: number; gaps_7d: number; usage_30d: number }
interface KBStatsSummaryResponse { stats: Record<string, KBStatsSummary> }
interface Notebook { id: string; name: string; description: string | null; sources_count: number; created_at: string }
interface NotebookListResponse { items: Notebook[]; total: number }

const FOCUS_BASE = '/research/v1'

function formatDate(isoString: string): string {
  return datetime(getLocale(), isoString, { day: 'numeric', month: 'short' })
}

function KnowledgePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const myUserId = auth.user?.profile?.sub
  const navigate = useNavigate()
  const { user: currentUser } = useCurrentUser()
  const [menuOpen, setMenuOpen] = useState(false)

  const { data: kbsData, isLoading, error, refetch } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => {
      try { return await apiFetch<KBsResponse>('/api/app/knowledge-bases', token) }
      catch (err) { queryLogger.warn('Knowledge bases fetch failed', { err }); throw err }
    },
    enabled: !!token,
  })

  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: async () => {
      try { return await apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary', token) }
      catch (err) { queryLogger.warn('KB stats summary fetch failed', { err }); throw err }
    },
    enabled: !!token,
  })

  const hasChat = currentUser?.isAdmin || currentUser?.products.includes('chat')
  const { data: notebooksData } = useQuery<NotebookListResponse>({
    queryKey: ['focus-notebooks'],
    queryFn: async () => apiFetch<NotebookListResponse>(`${FOCUS_BASE}/notebooks`, token),
    enabled: !!token && !!hasChat,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []
  const personalKb = allKbs.find((kb) => kb.slug === `personal-${myUserId}` && kb.owner_type === 'user')
  const orgKb = allKbs.find((kb) => kb.slug === 'org' && kb.owner_type === 'org')
  const defaultSlugs = new Set([personalKb?.slug, orgKb?.slug].filter(Boolean))
  const otherKbs = allKbs.filter((kb) => !defaultSlugs.has(kb.slug))
  const notebooks = notebooksData?.items ?? []
  const docKbs = allKbs.filter((kb) => kb.docs_enabled)

  type NoteItem = { type: 'notebook'; id: string; name: string; date: string } | { type: 'document'; slug: string; name: string }
  const noteItems: NoteItem[] = [
    ...notebooks.map((nb): NoteItem => ({ type: 'notebook', id: nb.id, name: nb.name, date: nb.created_at })),
    ...docKbs.map((kb): NoteItem => ({ type: 'document', slug: kb.slug, name: kb.name })),
  ]

  const totalItems = Object.values(statsBySlug).reduce((sum, s) => sum + s.items, 0)

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--color-foreground)]">
            {m.knowledge_page_title()}
          </h1>
          {!isLoading && totalItems > 0 && (
            <p className="mt-1 text-sm text-[var(--color-muted-foreground)]">
              {totalItems} items in {allKbs.length} collecties
            </p>
          )}
        </div>
        <div className="relative">
          <button
            type="button"
            onClick={() => setMenuOpen((v) => !v)}
            className="flex items-center gap-1.5 rounded-full border border-gray-200 px-4 py-2 text-sm text-[var(--color-foreground)] transition-colors hover:bg-gray-50"
          >
            <Plus className="h-4 w-4" />
            {m.knowledge_new_button()}
            <ChevronDown className="h-3 w-3 opacity-50" />
          </button>
          {menuOpen && (
            <>
              <div className="fixed inset-0 z-40" onClick={() => setMenuOpen(false)} />
              <div className="absolute right-0 top-full z-50 mt-2 w-48 rounded-xl border border-gray-200 bg-white py-1 shadow-lg">
                <button
                  type="button"
                  onClick={() => { setMenuOpen(false); void navigate({ to: '/app/knowledge/new' }) }}
                  className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm hover:bg-gray-50 transition-colors text-left"
                >
                  <Plus className="h-4 w-4 text-gray-400" />
                  {m.knowledge_new_source()}
                </button>
                {hasChat && (
                  <button
                    type="button"
                    onClick={() => { setMenuOpen(false); void navigate({ to: '/app/focus/new' }) }}
                    className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm hover:bg-gray-50 transition-colors text-left"
                  >
                    <BookOpen className="h-4 w-4 text-gray-400" />
                    {m.knowledge_new_notebook()}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Content */}
      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-16 rounded-xl bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="space-y-8">
          {/* Collections */}
          <div className="space-y-2">
            {personalKb && (
              <CollectionRow kb={personalKb} stats={statsBySlug[personalKb.slug]} isDefault />
            )}
            {orgKb && (
              <CollectionRow kb={orgKb} stats={statsBySlug[orgKb.slug]} isDefault />
            )}
            {otherKbs.map((kb) => (
              <CollectionRow key={kb.id} kb={kb} stats={statsBySlug[kb.slug]} isDefault={false} />
            ))}
          </div>

          {/* Notes & docs */}
          {noteItems.length > 0 && (
            <div>
              <h2 className="mb-3 text-xs font-medium text-gray-400 uppercase tracking-wider">
                {m.knowledge_section_notes()}
              </h2>
              <div className="space-y-1">
                {noteItems.map((item) => (
                  <Link
                    key={item.type === 'notebook' ? item.id : item.slug}
                    to={item.type === 'notebook' ? '/app/focus/$notebookId' : '/app/docs/$kbSlug'}
                    params={item.type === 'notebook' ? { notebookId: item.id } : { kbSlug: item.slug }}
                    className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-gray-50 transition-colors"
                  >
                    {item.type === 'notebook' ? (
                      <BookOpen size={16} strokeWidth={1.5} className="text-gray-400 shrink-0" />
                    ) : (
                      <FileText size={16} strokeWidth={1.5} className="text-gray-400 shrink-0" />
                    )}
                    <span className="text-sm text-[var(--color-foreground)] truncate">{item.name}</span>
                    {item.type === 'notebook' && 'date' in item && (
                      <span className="ml-auto text-xs text-gray-400 tabular-nums whitespace-nowrap">
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

function CollectionRow({
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
      className="group flex items-center gap-3 rounded-xl border border-gray-100 px-4 py-3.5 transition-all hover:border-gray-200 hover:shadow-sm"
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${isDefault ? 'bg-[var(--color-success)]' : 'bg-gray-300'}`} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-[var(--color-foreground)]">{kb.name}</p>
        {kb.description && (
          <p className="text-xs text-gray-400 truncate mt-0.5">{kb.description}</p>
        )}
      </div>
      <div className="flex items-center gap-4">
        {stats && stats.items > 0 && (
          <span className="text-xs text-gray-400 tabular-nums">{stats.items} items</span>
        )}
        {stats && stats.connectors > 0 && (
          <span className="text-xs text-gray-400 tabular-nums">{stats.connectors} bronnen</span>
        )}
        <ChevronRight size={16} className="text-gray-300 group-hover:text-gray-400 transition-colors" />
      </div>
    </Link>
  )
}
