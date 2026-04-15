import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { FileText, Zap, Users } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import type { KnowledgeBase, KBStats } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/overview')({
  component: OverviewTab,
})

function OverviewTab() {
  const { kbSlug } = Route.useParams()
  const auth = useAuth()
  const token = auth.user?.access_token

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

  const items = stats?.volume ?? 0
  const sources = stats?.source_page_count ?? 0
  const usage = stats?.usage_last_30d ?? 0

  return (
    <div className="space-y-6">
      {/* Simple stats */}
      <div className="grid grid-cols-3 gap-4">
        <div className="rounded-lg border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <FileText className="h-4 w-4 text-gray-400" />
            <span className="text-xs text-gray-400">Bestanden</span>
          </div>
          <p className="text-2xl font-semibold text-gray-900">{items}</p>
        </div>
        <div className="rounded-lg border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="h-4 w-4 text-gray-400" />
            <span className="text-xs text-gray-400">Bronnen</span>
          </div>
          <p className="text-2xl font-semibold text-gray-900">{sources}</p>
        </div>
        <div className="rounded-lg border border-gray-200 p-4">
          <div className="flex items-center gap-2 mb-2">
            <Users className="h-4 w-4 text-gray-400" />
            <span className="text-xs text-gray-400">Gebruikt (30 dagen)</span>
          </div>
          <p className="text-2xl font-semibold text-gray-900">{usage}</p>
        </div>
      </div>

      {/* Quick actions */}
      <div className="flex gap-3">
        <Link
          to="/app/knowledge/$kbSlug/connectors"
          params={{ kbSlug }}
          className="flex items-center gap-2 rounded-lg border border-gray-200 px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 transition-colors"
        >
          <Zap className="h-4 w-4" />
          Bron toevoegen
        </Link>
      </div>
    </div>
  )
}
