import { createFileRoute } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Brain, MessageSquare, FileText, Database, Users } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
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

  return (
    <div className="p-8 max-w-2xl">
      <div className="flex items-center gap-3 mb-2">
        <Brain className="h-7 w-7 text-[var(--color-purple-deep)]" />
        <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
          {m.knowledge_page_intro_heading()}
        </h1>
      </div>
      <p className="text-[var(--color-muted-foreground)] mb-8 leading-relaxed">
        {m.knowledge_page_intro_body()}
      </p>

      <div className="flex flex-col gap-4">
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

        <Card>
          <CardContent className="pt-6">
            <div className="flex items-start gap-4">
              <div className="rounded-lg bg-[var(--color-secondary)] p-2.5 shrink-0">
                <FileText className="h-5 w-5 text-[var(--color-purple-deep)]" />
              </div>
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h2 className="font-semibold text-[var(--color-purple-deep)]">
                    {m.knowledge_page_docs_heading()}
                  </h2>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--color-secondary)] text-[var(--color-muted-foreground)] border border-[var(--color-border)]">
                    {m.knowledge_page_docs_coming_soon()}
                  </span>
                </div>
                <p className="text-sm text-[var(--color-muted-foreground)] leading-relaxed">
                  {m.knowledge_page_docs_body()}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
