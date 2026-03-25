import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Brain, FileText, Globe, Lock } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { queryLogger } from '@/lib/logger'

export const Route = createFileRoute('/app/knowledge/$kbSlug')({
  component: KnowledgeDetailPage,
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  docs_enabled: boolean
  gitea_repo_slug: string | null
  owner_type: string
}

type Tab = 'docs' | 'sources' | 'stats'

function KnowledgeDetailPage() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token

  const { data: kb, isLoading, isError } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        queryLogger.warn('KB fetch failed', { slug: kbSlug, status: res.status })
        throw new Error('KB laden mislukt')
      }
      return res.json() as Promise<KnowledgeBase>
    },
    enabled: !!token,
    retry: false,
  })

  const searchParams = Route.useSearch()
  const activeTab: Tab = (searchParams.tab as Tab | undefined) ?? 'docs'

  if (isLoading) {
    return (
      <div className="p-8">
        <div className="h-8 w-48 rounded bg-[var(--color-secondary)] animate-pulse mb-4" />
        <div className="h-4 w-96 rounded bg-[var(--color-secondary)] animate-pulse" />
      </div>
    )
  }

  if (isError || !kb) {
    return (
      <div className="p-8 text-[var(--color-muted-foreground)]">
        {m.knowledge_detail_not_found()}
      </div>
    )
  }

  return (
    <div className="p-8 max-w-3xl">
      {/* Header */}
      <div className="flex items-start gap-3 mb-6">
        <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0 mt-0.5">
          <Brain className="h-5 w-5 text-[var(--color-purple-deep)]" />
        </div>
        <div className="flex-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {kb.name}
          </h1>
          {kb.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">{kb.description}</p>
          )}
          <div className="flex items-center gap-1.5 mt-2 text-xs text-[var(--color-muted-foreground)]">
            {kb.visibility === 'public' ? (
              <Globe className="h-3.5 w-3.5" />
            ) : (
              <Lock className="h-3.5 w-3.5" />
            )}
            <span>
              {kb.visibility === 'public'
                ? m.knowledge_page_kb_visibility_public()
                : m.knowledge_page_kb_visibility_internal()}
            </span>
          </div>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-[var(--color-border)] mb-6">
        {(['docs', 'sources', 'stats'] as Tab[]).map((tab) => (
          <Link
            key={tab}
            to="/app/knowledge/$kbSlug"
            params={{ kbSlug }}
            search={{ tab }}
            className={[
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab
                ? 'border-[var(--color-purple-deep)] text-[var(--color-purple-deep)]'
                : 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
            ].join(' ')}
          >
            {tab === 'docs'
              ? m.knowledge_detail_tab_docs()
              : tab === 'sources'
                ? m.knowledge_detail_tab_sources()
                : m.knowledge_detail_tab_stats()}
          </Link>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === 'docs' && (
        <div className="flex flex-col items-center gap-4 py-12 text-center text-[var(--color-muted-foreground)]">
          <FileText className="h-10 w-10" />
          <p className="text-sm">{m.knowledge_detail_docs_stub()}</p>
        </div>
      )}

      {activeTab === 'sources' && (
        <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">
          {m.knowledge_detail_sources_stub()}
        </div>
      )}

      {activeTab === 'stats' && (
        <div className="py-12 text-center text-sm text-[var(--color-muted-foreground)]">
          {m.knowledge_detail_stats_stub()}
        </div>
      )}
    </div>
  )
}
