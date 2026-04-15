import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { FileText, Zap, Plus, Globe, ExternalLink } from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive } from '@icons-pack/react-simple-icons'
import { apiFetch } from '@/lib/apiFetch'
import { SyncStatusBadge } from './-kb-helpers'
import type { KnowledgeBase, KBStats, ConnectorSummary } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/overview')({
  component: OverviewTab,
})

const CONNECTOR_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  github: SiGithub,
  notion: SiNotion,
  google_drive: SiGoogledrive,
  web_crawler: Globe,
  ms_docs: FileText,
}

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

  const { data: connectors } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token),
    enabled: !!token && !!kb,
  })

  if (!kb) return null

  const items = stats?.volume ?? 0
  const sourceList = connectors ?? []

  return (
    <div className="space-y-8">
      {/* Quick stats */}
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <FileText className="h-4 w-4" />
          <span><strong className="text-gray-900">{items}</strong> bestanden</span>
        </div>
        <div className="flex items-center gap-2 text-sm text-gray-400">
          <Zap className="h-4 w-4" />
          <span><strong className="text-gray-900">{sourceList.length}</strong> bronnen</span>
        </div>
      </div>

      {/* Connected sources — like Superdock */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">Verbonden bronnen</h2>
          <Link
            to="/app/knowledge/$kbSlug/connectors"
            params={{ kbSlug }}
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            Bron toevoegen
          </Link>
        </div>

        {sourceList.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-200 py-10 text-center">
            <Zap className="h-8 w-8 text-gray-300 mx-auto mb-3" />
            <p className="text-sm font-medium text-gray-900">Nog geen bronnen</p>
            <p className="text-xs text-gray-400 mt-1 max-w-sm mx-auto">
              Verbind bronnen zoals Notion, Google Drive of een website om je kennis te vullen.
            </p>
            <Link
              to="/app/knowledge/$kbSlug/connectors"
              params={{ kbSlug }}
              className="inline-flex items-center gap-1.5 mt-4 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
            >
              <Plus className="h-4 w-4" />
              Eerste bron toevoegen
            </Link>
          </div>
        ) : (
          <div className="space-y-2">
            {sourceList.map((c) => {
              const Icon = CONNECTOR_ICONS[c.connector_type] ?? Zap
              return (
                <div key={c.id} className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-3">
                  <Icon className="h-5 w-5 text-gray-400 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900 truncate">{c.name || c.connector_type}</p>
                    {c.last_sync_at && (
                      <p className="text-xs text-gray-400">Laatst gesynchroniseerd</p>
                    )}
                  </div>
                  <SyncStatusBadge status={c.last_sync_status} />
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Documents — block editor link */}
      {kb.docs_enabled && kb.gitea_repo_slug && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">Documenten</h2>
          </div>
          <Link
            to="/app/docs/$kbSlug"
            params={{ kbSlug: kb.slug }}
            className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-3 hover:bg-gray-50 transition-colors"
          >
            <FileText className="h-5 w-5 text-gray-400" />
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-900">Block editor</p>
              <p className="text-xs text-gray-400">Schrijf en bewerk documenten in deze collectie</p>
            </div>
            <ExternalLink className="h-4 w-4 text-gray-300" />
          </Link>
        </div>
      )}
    </div>
  )
}
