import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { MessageSquare, Database, Users, BookOpen, Plus, Lock, AlertTriangle } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
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

interface KnowledgeStats {
  personal_count: number
  org_count: number
}

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  docs_enabled: boolean
  owner_type: string
  owner_user_id: string | null
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

interface GapSummary {
  total_7d: number
  hard_7d: number
  soft_7d: number
}

function KBCard({ kb }: { kb: KnowledgeBase }) {
  return (
    <Link to="/app/knowledge/$kbSlug" params={{ kbSlug: kb.slug }}>
      <Card className="hover:border-[var(--color-foreground)] transition-colors cursor-pointer">
        <CardContent className="pt-4 pb-4">
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-[var(--color-secondary)] p-2 shrink-0">
              {kb.owner_type === 'user'
                ? <Lock className="h-4 w-4 text-[var(--color-foreground)]" />
                : <BookOpen className="h-4 w-4 text-[var(--color-foreground)]" />}
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="font-semibold text-[var(--color-foreground)] truncate">{kb.name}</h3>
              {kb.description && (
                <p className="text-sm text-[var(--color-muted-foreground)] truncate mt-0.5">{kb.description}</p>
              )}
              <p className="text-xs text-[var(--color-muted-foreground)] mt-1">
                {kb.visibility === 'public'
                  ? m.knowledge_page_kb_visibility_public()
                  : m.knowledge_page_kb_visibility_internal()}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  )
}

function KnowledgePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const myUserId = auth.user?.profile?.sub

  const { data: stats, isLoading: statsLoading } = useQuery<KnowledgeStats>({
    queryKey: ['knowledge-stats'],
    queryFn: async () => {
      try {
        return await apiFetch<KnowledgeStats>(`/api/knowledge/stats`, token)
      } catch (err) {
        queryLogger.warn('Knowledge stats fetch failed', { err })
        throw err
      }
    },
    enabled: !!token,
    retry: false,
  })

  const { user: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.isAdmin === true

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

  const allKbs = kbsData?.knowledge_bases ?? []
  const orgKbs = allKbs.filter((kb) => kb.owner_type === 'org')
  const personalKbs = allKbs.filter(
    (kb) => kb.owner_type === 'user' && kb.owner_user_id === myUserId,
  )

  return (
    <div className="p-8 max-w-2xl">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-[var(--color-foreground)]">
            {m.knowledge_page_intro_heading()}
          </h1>
        </div>
      </div>
      <p className="text-[var(--color-muted-foreground)] mb-8 leading-relaxed">
        {m.knowledge_page_intro_body()}
      </p>

      {kbsError ? (
        <QueryErrorState error={kbsError instanceof Error ? kbsError : new Error(String(kbsError))} onRetry={() => void refetchKbs()} />
      ) : <div className="flex flex-col gap-6">
        {/* Personal knowledge base (chat RAG) */}
        <Link to="/app/knowledge/$kbSlug" params={{ kbSlug: 'personal' }}>
        <Card className="hover:border-[var(--color-foreground)] transition-colors cursor-pointer">
          <CardContent className="pt-6">
            <div className="flex items-start gap-4">
              <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0">
                <MessageSquare className="h-5 w-5 text-[var(--color-foreground)]" />
              </div>
              <div className="flex-1">
                <h2 className="font-semibold text-[var(--color-foreground)] mb-1">
                  {m.knowledge_page_personal_heading()}
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed mb-3">
                  {m.knowledge_page_personal_body()}
                </p>
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                  <p className="text-sm font-medium text-[var(--color-foreground)]">
                    {statsLoading ? (
                      <span className="inline-block h-4 w-32 rounded bg-[var(--color-secondary)] animate-pulse" />
                    ) : stats != null && stats.personal_count > 0 ? (
                      m.knowledge_page_stat_personal({ count: String(stats.personal_count) })
                    ) : (
                      m.knowledge_page_stat_personal_empty()
                    )}
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
        </Link>

        {/* Org-wide knowledge */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start gap-4">
              <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0">
                <Users className="h-5 w-5 text-[var(--color-foreground)]" />
              </div>
              <div className="flex-1">
                <h2 className="font-semibold text-[var(--color-foreground)] mb-1">
                  {m.knowledge_page_org_heading()}
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed mb-3">
                  {m.knowledge_page_org_body()}
                </p>
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                  <p className="text-sm font-medium text-[var(--color-foreground)]">
                    {statsLoading ? (
                      <span className="inline-block h-4 w-32 rounded bg-[var(--color-secondary)] animate-pulse" />
                    ) : stats != null && stats.org_count > 0 ? (
                      m.knowledge_page_stat_org({ count: String(stats.org_count) })
                    ) : (
                      m.knowledge_page_stat_org_empty()
                    )}
                  </p>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Named org KBs */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-[var(--color-foreground)]">
              {m.knowledge_page_kbs_heading()}
            </h2>
            <Button variant="outline" size="sm" asChild>
              <Link to="/app/knowledge/new">
                <Plus className="h-4 w-4 mr-1" />
                {m.knowledge_page_kbs_create()}
              </Link>
            </Button>
          </div>

          {kbsLoading ? (
            <div className="flex flex-col gap-3">
              {[1, 2].map((i) => (
                <div key={i} className="h-20 rounded-lg bg-[var(--color-secondary)] animate-pulse" />
              ))}
            </div>
          ) : orgKbs.length === 0 ? (
            <Card>
              <CardContent className="pt-6 pb-6 text-center">
                <BookOpen className="h-8 w-8 text-[var(--color-muted-foreground)] mx-auto mb-2" />
                <p className="text-sm text-[var(--color-muted-foreground)]">
                  {m.knowledge_page_kbs_empty()}
                </p>
              </CardContent>
            </Card>
          ) : (
            <div className="flex flex-col gap-3">
              {orgKbs.map((kb) => <KBCard key={kb.id} kb={kb} />)}
            </div>
          )}
        </div>

        {/* Personal named KBs */}
        {(personalKbs.length > 0 || !kbsLoading) && (
          <div>
            <div className="flex items-center justify-between mb-3">
              <h2 className="font-semibold text-[var(--color-foreground)]">
                {m.knowledge_page_personal_kbs_heading()}
              </h2>
            </div>

            {kbsLoading ? (
              <div className="h-20 rounded-lg bg-[var(--color-secondary)] animate-pulse" />
            ) : personalKbs.length === 0 ? (
              <Card>
                <CardContent className="pt-6 pb-6 text-center">
                  <Lock className="h-8 w-8 text-[var(--color-muted-foreground)] mx-auto mb-2" />
                  <p className="text-sm text-[var(--color-muted-foreground)]">
                    {m.knowledge_page_kbs_empty()}
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="flex flex-col gap-3">
                {personalKbs.map((kb) => <KBCard key={kb.id} kb={kb} />)}
              </div>
            )}
          </div>
        )}

        {/* Knowledge Gaps (admin only) */}
        {isAdmin && gapSummary != null && (
          <Link to="/app/gaps">
            <Card className="hover:border-[var(--color-foreground)] transition-colors cursor-pointer">
              <CardContent className="pt-6">
                <div className="flex items-start gap-4">
                  <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0">
                    <AlertTriangle className="h-5 w-5 text-[var(--color-foreground)]" />
                  </div>
                  <div className="flex-1">
                    <h2 className="font-semibold text-[var(--color-foreground)] mb-1">
                      {m.gaps_index_card_heading()}
                    </h2>
                    <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed mb-3">
                      {m.gaps_index_card_body()}
                    </p>
                    {gapSummary.total_7d === 0 ? (
                      <p className="text-sm text-[var(--color-muted-foreground)]">
                        {m.gaps_index_card_none()}
                      </p>
                    ) : (
                      <div className="flex items-center gap-4">
                        <span className="text-sm font-medium text-[var(--color-foreground)]">
                          {gapSummary.total_7d} total
                        </span>
                        {gapSummary.hard_7d > 0 && (
                          <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--color-destructive)]/10 text-[var(--color-destructive)]">
                            {gapSummary.hard_7d} {m.gaps_type_hard()}
                          </span>
                        )}
                        {gapSummary.soft_7d > 0 && (
                          <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-[var(--color-warning)]/10 text-[var(--color-warning)]">
                            {gapSummary.soft_7d} {m.gaps_type_soft()}
                          </span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          </Link>
        )}
      </div>}
    </div>
  )
}
