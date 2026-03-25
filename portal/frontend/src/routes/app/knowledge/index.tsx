import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Brain, MessageSquare, Database, Users, BookOpen, Plus } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { API_BASE } from '@/lib/api'
import { queryLogger } from '@/lib/logger'

export const Route = createFileRoute('/app/knowledge/')({
  component: KnowledgePage,
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
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

function KnowledgePage() {
  const auth = useAuth()
  const token = auth.user?.access_token

  const { data: stats, isLoading: statsLoading } = useQuery<KnowledgeStats>({
    queryKey: ['knowledge-stats'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/knowledge/stats`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        queryLogger.warn('Knowledge stats fetch failed', { status: res.status })
        throw new Error('Stats laden mislukt')
      }
      return res.json() as Promise<KnowledgeStats>
    },
    enabled: !!token,
    retry: false,
  })

  const { data: kbsData, isLoading: kbsLoading } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        queryLogger.warn('Knowledge bases fetch failed', { status: res.status })
        throw new Error('Kennisbanken laden mislukt')
      }
      return res.json() as Promise<KBsResponse>
    },
    enabled: !!token,
    retry: false,
  })

  const kbs = kbsData?.knowledge_bases ?? []

  return (
    <div className="p-8 max-w-2xl">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-3">
          <Brain className="h-7 w-7 text-[var(--color-purple-deep)]" />
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.knowledge_page_intro_heading()}
          </h1>
        </div>
      </div>
      <p className="text-[var(--color-muted-foreground)] mb-8 leading-relaxed">
        {m.knowledge_page_intro_body()}
      </p>

      <div className="flex flex-col gap-4">
        {/* Personal knowledge base */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start gap-4">
              <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0">
                <MessageSquare className="h-5 w-5 text-[var(--color-purple-deep)]" />
              </div>
              <div className="flex-1">
                <h2 className="font-semibold text-[var(--color-purple-deep)] mb-1">
                  {m.knowledge_page_personal_heading()}
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed mb-3">
                  {m.knowledge_page_personal_body()}
                </p>
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                  <p className="text-sm font-medium text-[var(--color-purple-deep)]">
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

        {/* Org-wide knowledge */}
        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start gap-4">
              <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0">
                <Users className="h-5 w-5 text-[var(--color-purple-deep)]" />
              </div>
              <div className="flex-1">
                <h2 className="font-semibold text-[var(--color-purple-deep)] mb-1">
                  {m.knowledge_page_org_heading()}
                </h2>
                <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed mb-3">
                  {m.knowledge_page_org_body()}
                </p>
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                  <p className="text-sm font-medium text-[var(--color-purple-deep)]">
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

        {/* Named knowledge bases */}
        <div>
          <div className="flex items-center justify-between mb-3">
            <h2 className="font-semibold text-[var(--color-purple-deep)]">
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
          ) : kbs.length === 0 ? (
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
              {kbs.map((kb) => (
                <Link key={kb.id} to="/app/knowledge/$kbSlug" params={{ kbSlug: kb.slug }}>
                  <Card className="hover:border-[var(--color-purple-deep)] transition-colors cursor-pointer">
                    <CardContent className="pt-4 pb-4">
                      <div className="flex items-start gap-3">
                        <div className="rounded-lg bg-[var(--color-secondary)] p-2 shrink-0">
                          <BookOpen className="h-4 w-4 text-[var(--color-purple-deep)]" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <h3 className="font-semibold text-[var(--color-purple-deep)] truncate">
                            {kb.name}
                          </h3>
                          {kb.description && (
                            <p className="text-sm text-[var(--color-muted-foreground)] truncate mt-0.5">
                              {kb.description}
                            </p>
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
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
