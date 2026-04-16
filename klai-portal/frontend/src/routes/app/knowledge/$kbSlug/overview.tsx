import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen, FileText, BarChart2, AlertTriangle, Database, Search, GitBranch,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { DashboardSection } from './-kb-helpers'
import type { KnowledgeBase, KBStats } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/overview')({
  component: OverviewTab,
})

function OverviewTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token
  const { user } = useCurrentUser()

  // These queries reuse the same queryKeys as the parent layout -- TanStack Query
  // serves cached data without re-fetching.
  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })

  const { data: stats } = useQuery<KBStats>({
    queryKey: ['kb-stats', kbSlug],
    queryFn: async () => apiFetch<KBStats>(`/api/app/knowledge-bases/${kbSlug}/stats`, token),
    enabled: !!token && !!kb,
  })

  if (!kb) return null

  const docsLabel =
    stats?.docs_count == null
      ? m.knowledge_detail_docs_none()
      : stats.docs_count === 1
        ? m.knowledge_detail_docs_count_one()
        : m.knowledge_detail_docs_count({ count: String(stats.docs_count) })

  return (
    <div className="space-y-8">
      <DashboardSection icon={BookOpen} title={m.knowledge_detail_section_docs()}>
        {!kb.docs_enabled ? (
          <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_detail_docs_not_enabled()}</p>
        ) : (
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-[var(--color-muted-foreground)]" />
              <span className="text-sm text-[var(--color-foreground)]">{docsLabel}</span>
            </div>
            {kb.gitea_repo_slug && (
              <Link to="/app/docs/$kbSlug" params={{ kbSlug: kb.slug }} search={{ page: undefined }}>
                <Button variant="outline" size="sm">{m.knowledge_detail_view_in_docs()}</Button>
              </Link>
            )}
          </div>
        )}
      </DashboardSection>

      <DashboardSection icon={BarChart2} title={m.knowledge_detail_section_stats()}>
        <div className="flex gap-8 mb-5">
          <div>
            <p className="text-xs text-[var(--color-muted-foreground)] uppercase tracking-wide mb-1">{m.knowledge_detail_stats_search_index()}</p>
            <p className="text-sm font-medium text-[var(--color-foreground)]">
              {stats?.volume != null
                ? m.knowledge_detail_volume({ count: String(stats.volume) })
                : m.knowledge_detail_volume_unknown()}
            </p>
          </div>
          <div>
            <p className="text-xs text-[var(--color-muted-foreground)] uppercase tracking-wide mb-1">{m.knowledge_detail_stats_queries()}</p>
            <p className="text-sm font-medium text-[var(--color-foreground)]">
              {stats?.usage_last_30d != null
                ? m.knowledge_detail_usage({ count: String(stats.usage_last_30d) })
                : m.knowledge_detail_usage_unknown()}
            </p>
          </div>
          {user?.isAdmin === true && stats?.org_gap_count_7d != null && (
            <Link to="/app/gaps" className="group">
              <div>
                <p className="text-xs text-[var(--color-muted-foreground)] uppercase tracking-wide mb-1 flex items-center gap-1">
                  <AlertTriangle className="h-3 w-3" />
                  {m.gaps_overview_tile()}
                </p>
                <p className="text-sm font-medium text-[var(--color-foreground)] group-hover:text-[var(--color-accent)] transition-colors">
                  {stats.org_gap_count_7d}
                </p>
              </div>
            </Link>
          )}
        </div>

        {/* Breakdown per database */}
        <div>
          <p className="text-xs text-[var(--color-muted-foreground)] uppercase tracking-wide mb-2">
            {m.knowledge_detail_volume_breakdown_title()}
          </p>
          <div className="grid grid-cols-3 gap-3">
            <div className="flex items-start gap-2 rounded-lg border border-[var(--color-border)] p-3">
              <Database className="h-4 w-4 text-[var(--color-accent)] mt-0.5 shrink-0" />
              <div>
                <p className="text-xs font-medium text-[var(--color-foreground)]">
                  {m.knowledge_detail_volume_sources()}
                </p>
                <p className="text-sm text-[var(--color-foreground)]">
                  {stats?.source_page_count != null
                    ? m.knowledge_detail_volume_sources_count({ count: String(stats.source_page_count) })
                    : m.knowledge_detail_volume_unavailable()}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-2 rounded-lg border border-[var(--color-border)] p-3">
              <Search className="h-4 w-4 text-[var(--color-accent)] mt-0.5 shrink-0" />
              <div>
                <p className="text-xs font-medium text-[var(--color-foreground)]">
                  {m.knowledge_detail_volume_search_chunks()}
                </p>
                <p className="text-sm text-[var(--color-foreground)]">
                  {stats?.vector_chunk_count != null
                    ? m.knowledge_detail_volume_search_chunks_count({ count: String(stats.vector_chunk_count) })
                    : m.knowledge_detail_volume_unavailable()}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-2 rounded-lg border border-[var(--color-border)] p-3">
              <GitBranch className="h-4 w-4 text-[var(--color-accent)] mt-0.5 shrink-0" />
              <div>
                <p className="text-xs font-medium text-[var(--color-foreground)]">
                  {m.knowledge_detail_volume_graph()}
                </p>
                <p className="text-sm text-[var(--color-foreground)]">
                  {stats?.graph_entity_count != null && stats?.graph_edge_count != null
                    ? m.knowledge_detail_volume_graph_count({
                        entities: String(stats.graph_entity_count),
                        edges: String(stats.graph_edge_count),
                      })
                    : m.knowledge_detail_volume_unavailable()}
                </p>
              </div>
            </div>
          </div>
        </div>
      </DashboardSection>
    </div>
  )
}
