import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  Plus, ChevronRight, User, Building2, FolderOpen, FileText, Zap,
  Search, Trash2, Globe, RefreshCw, Loader2,
} from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive } from '@icons-pack/react-simple-icons'
import { Badge } from '@/components/ui/badge'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { SyncStatusBadge } from './$kbSlug/-kb-helpers'
import type { ConnectorSummary } from './$kbSlug/-kb-types'

export const Route = createFileRoute('/app/knowledge/')({
  component: () => (
    <ProductGuard product="knowledge">
      <SourcesPage />
    </ProductGuard>
  ),
})

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  description: string | null
  visibility: string
  owner_type: string
}

interface KBsResponse { knowledge_bases: KnowledgeBase[] }
interface KBStatsSummary { items: number; connectors: number }
interface KBStatsSummaryResponse { stats: Record<string, KBStatsSummary> }

const CONNECTOR_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  github: SiGithub,
  notion: SiNotion,
  google_drive: SiGoogledrive,
  web_crawler: Globe,
  ms_docs: FileText,
}

function SourcesPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const [expandedSlugs, setExpandedSlugs] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')

  const { data: kbsData, isLoading, error, refetch } = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases'],
    queryFn: () => apiFetch<KBsResponse>('/api/app/knowledge-bases', token),
    enabled: !!token,
  })

  const { data: statsData } = useQuery<KBStatsSummaryResponse>({
    queryKey: ['app-knowledge-bases-stats-summary'],
    queryFn: () => apiFetch<KBStatsSummaryResponse>('/api/app/knowledge-bases/stats-summary', token),
    enabled: !!token,
  })

  const statsBySlug = statsData?.stats ?? {}
  const allKbs = kbsData?.knowledge_bases ?? []

  const filteredKbs = searchQuery
    ? allKbs.filter((kb) =>
        kb.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        kb.slug.toLowerCase().includes(searchQuery.toLowerCase()),
      )
    : allKbs

  function toggleExpand(slug: string) {
    setExpandedSlugs((prev) => {
      const next = new Set(prev)
      if (next.has(slug)) next.delete(slug)
      else next.add(slug)
      return next
    })
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-[26px] font-display-bold text-gray-900">
          {m.sources_page_title()}
        </h1>
        <button
          type="button"
          onClick={() => void navigate({ to: '/app/knowledge/new' })}
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.sources_new_collection()}
        </button>
      </div>
      <p className="text-sm text-gray-400 mb-6">
        {m.sources_page_subtitle()}
      </p>

      {/* Search */}
      <div className="relative mb-6">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        <input
          type="text"
          placeholder={m.sources_search_placeholder()}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full rounded-lg border border-gray-200 bg-white py-2.5 pl-10 pr-4 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-gray-400"
        />
      </div>

      {/* Collection list */}
      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : filteredKbs.length === 0 && !searchQuery ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <FolderOpen className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">{m.sources_empty_title()}</p>
          <p className="text-sm text-gray-400 mt-1">{m.sources_empty_description()}</p>
          <button
            type="button"
            onClick={() => void navigate({ to: '/app/knowledge/new' })}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {m.sources_empty_cta()}
          </button>
        </div>
      ) : filteredKbs.length === 0 && searchQuery ? (
        <p className="text-center text-sm text-gray-400 py-12">
          Geen resultaten voor &ldquo;{searchQuery}&rdquo;
        </p>
      ) : (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {filteredKbs.map((kb) => (
            <CollectionRow
              key={kb.id}
              kb={kb}
              stats={statsBySlug[kb.slug]}
              expanded={expandedSlugs.has(kb.slug)}
              onToggle={() => toggleExpand(kb.slug)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/* ── Collection row ────────────────────────────────────────────────── */

function CollectionRow({
  kb,
  stats,
  expanded,
  onToggle,
}: {
  kb: KnowledgeBase
  stats: KBStatsSummary | undefined
  expanded: boolean
  onToggle: () => void
}) {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const sourceCount = stats?.connectors ?? 0
  const itemCount = stats?.items ?? 0
  const queryClient = useQueryClient()
  const [confirmingDeleteKb, setConfirmingDeleteKb] = useState(false)
  const [confirmingDeleteConnectorId, setConfirmingDeleteConnectorId] = useState<string | null>(null)

  // Lazy-load connectors when expanded
  const { data: connectors, isLoading: connectorsLoading } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kb.slug],
    queryFn: () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kb.slug}/connectors/`, token),
    enabled: !!token && expanded,
  })

  const deleteKbMutation = useMutation({
    mutationFn: async () => apiFetch(`/api/app/knowledge-bases/${kb.slug}`, token, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
    },
    onError: (err) => queryLogger.error('KB delete failed', { slug: kb.slug, err }),
  })

  const syncConnectorMutation = useMutation({
    mutationFn: async (connectorId: string) =>
      apiFetch(`/api/app/knowledge-bases/${kb.slug}/connectors/${connectorId}/sync`, token, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kb.slug] })
    },
    onError: (err, id) => queryLogger.error('Connector sync failed', { kb: kb.slug, connectorId: id, err }),
  })

  const deleteConnectorMutation = useMutation({
    mutationFn: async (connectorId: string) =>
      apiFetch(`/api/app/knowledge-bases/${kb.slug}/connectors/${connectorId}`, token, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kb.slug] })
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      setConfirmingDeleteConnectorId(null)
    },
    onError: (err, id) => queryLogger.error('Connector delete failed', { kb: kb.slug, connectorId: id, err }),
  })

  const kbIcon =
    kb.owner_type === 'user' ? (
      <User className="h-4 w-4" />
    ) : kb.slug === 'org' ? (
      <Building2 className="h-4 w-4" />
    ) : (
      <FolderOpen className="h-4 w-4" />
    )

  return (
    <div>
      {/* Row */}
      <div className="flex items-center gap-3 py-3.5 px-2">
        {/* Expand toggle */}
        <button
          type="button"
          onClick={onToggle}
          className="p-0.5 text-gray-400 hover:text-gray-900 transition-colors"
        >
          <ChevronRight
            className={`h-4 w-4 transition-transform duration-150 ${expanded ? 'rotate-90' : ''}`}
          />
        </button>

        {/* Icon */}
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400">
          {kbIcon}
        </div>

        {/* Name + count */}
        <Link
          to="/app/knowledge/$kbSlug/overview"
          params={{ kbSlug: kb.slug }}
          className="flex-1 min-w-0 group"
        >
          <span className="text-[15px] font-display text-gray-900 group-hover:underline">
            {kb.name}
          </span>
          <span className="ml-2 text-xs text-gray-400">
            {sourceCount === 1
              ? m.sources_count_one()
              : m.sources_count({ count: String(sourceCount) })}
          </span>
        </Link>

        {/* Sync badge */}
        {itemCount > 0 && (
          <Badge variant="success">
            {m.sources_indexed()}
          </Badge>
        )}

        {/* Actions */}
        <InlineDeleteConfirm
          isConfirming={confirmingDeleteKb}
          isPending={deleteKbMutation.isPending}
          label={`Collectie "${kb.name}" verwijderen?`}
          cancelLabel="Annuleren"
          onConfirm={() => {
            deleteKbMutation.mutate()
            setConfirmingDeleteKb(false)
          }}
          onCancel={() => setConfirmingDeleteKb(false)}
        >
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation()
                void navigate({
                  to: '/app/knowledge/$kbSlug/add-source',
                  params: { kbSlug: kb.slug },
                })
              }}
              className="flex items-center gap-1 rounded px-2 py-1 text-xs font-medium text-gray-500 hover:text-gray-900 hover:bg-gray-50 transition-colors"
            >
              <Plus className="h-3.5 w-3.5" />
              Add
            </button>
            <Tooltip label="Verwijder collectie">
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirmingDeleteKb(true)
                }}
                aria-label="Delete collection"
                className="p-1.5 text-gray-300 hover:text-[var(--color-destructive)] transition-colors"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </Tooltip>
          </div>
        </InlineDeleteConfirm>
      </div>

      {/* Expanded: sources list */}
      {expanded && (
        <div className="pl-[52px] pb-3 space-y-0.5">
          {connectorsLoading ? (
            <div className="space-y-1">
              {[1, 2].map((i) => (
                <div key={i} className="h-9 rounded-lg bg-gray-50 animate-pulse" />
              ))}
            </div>
          ) : connectors && connectors.length > 0 ? (
            connectors.map((c) => {
              const Icon = CONNECTOR_ICONS[c.connector_type] ?? Zap
              const isSyncing =
                syncConnectorMutation.isPending && syncConnectorMutation.variables === c.id
              const isDeletingConnector =
                deleteConnectorMutation.isPending && deleteConnectorMutation.variables === c.id
              return (
                <InlineDeleteConfirm
                  key={c.id}
                  isConfirming={confirmingDeleteConnectorId === c.id}
                  isPending={isDeletingConnector}
                  label={`Bron "${c.name || c.connector_type}" verwijderen?`}
                  cancelLabel="Annuleren"
                  onConfirm={() => deleteConnectorMutation.mutate(c.id)}
                  onCancel={() => setConfirmingDeleteConnectorId(null)}
                >
                  <div className="flex items-center gap-3 rounded-lg px-3 py-2 hover:bg-gray-50 transition-colors">
                    <Icon className="h-4 w-4 text-gray-400 shrink-0" />
                    <span className="text-sm text-gray-700 flex-1 truncate">
                      {c.name || c.connector_type}
                    </span>
                    <SyncStatusBadge status={c.last_sync_status} lastSyncAt={c.last_sync_at} />
                    <div className="flex items-center gap-1 shrink-0">
                      <Tooltip label="Synchroniseren">
                        <button
                          type="button"
                          disabled={isSyncing}
                          onClick={(e) => {
                            e.stopPropagation()
                            syncConnectorMutation.mutate(c.id)
                          }}
                          aria-label="Sync connector"
                          className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors disabled:opacity-40"
                        >
                          {isSyncing ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <RefreshCw size={14} />
                          )}
                        </button>
                      </Tooltip>
                      <Tooltip label="Bron verwijderen">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            setConfirmingDeleteConnectorId(c.id)
                          }}
                          aria-label="Delete connector"
                          className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      </Tooltip>
                    </div>
                  </div>
                </InlineDeleteConfirm>
              )
            })
          ) : (
            <p className="text-xs text-gray-400 px-3 py-2">
              {m.sources_no_sources()}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
