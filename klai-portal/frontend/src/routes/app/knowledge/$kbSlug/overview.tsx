import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  FileText,
  Zap,
  Plus,
  Globe,
  ExternalLink,
  File,
  RefreshCw,
  Loader2,
  Trash2,
} from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive } from '@icons-pack/react-simple-icons'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { SyncStatusBadge } from './-kb-helpers'
import type {
  KnowledgeBase,
  KBStats,
  ConnectorSummary,
  PersonalItemsResponse,
} from './-kb-types'

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
  const myUserId = auth.user?.profile?.sub
  const { user: currentUser } = useCurrentUser()
  const isAdmin = currentUser?.isAdmin === true
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [confirmingDeleteKb, setConfirmingDeleteKb] = useState(false)
  const [confirmingDeleteConnectorId, setConfirmingDeleteConnectorId] = useState<string | null>(
    null,
  )
  const [confirmingDeleteFileId, setConfirmingDeleteFileId] = useState<string | null>(null)

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
    queryFn: async () =>
      apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token),
    enabled: !!token && !!kb,
  })

  // Personal items API returns ALL of the user's personal artifacts without
  // a kb_slug filter. Only query on the canonical "Persoonlijk" KB
  // (slug === `personal-${myUserId}`) so newly created user-owned collections
  // don't show cross-KB file bleed.
  const isDefaultPersonal = !!myUserId && kb?.slug === `personal-${myUserId}`
  const { data: filesData } = useQuery<PersonalItemsResponse>({
    queryKey: ['personal-knowledge', kbSlug],
    queryFn: async () =>
      apiFetch<PersonalItemsResponse>('/api/knowledge/personal/items', token),
    enabled: !!token && isDefaultPersonal,
  })

  // ── Mutations ────────────────────────────────────────────────────────

  const deleteKbMutation = useMutation({
    mutationFn: async () =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}`, token, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge' })
    },
    onError: (err) => queryLogger.error('KB delete failed', { slug: kbSlug, err }),
  })

  const syncAllMutation = useMutation({
    mutationFn: async (connectorIds: string[]) => {
      await Promise.all(
        connectorIds.map((id) =>
          apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}/sync`, token, {
            method: 'POST',
          }),
        ),
      )
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    },
    onError: (err) => queryLogger.error('Sync all failed', { slug: kbSlug, err }),
  })

  const syncConnectorMutation = useMutation({
    mutationFn: async (connectorId: string) =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${connectorId}/sync`, token, {
        method: 'POST',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    },
  })

  const deleteConnectorMutation = useMutation({
    mutationFn: async (connectorId: string) =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${connectorId}`, token, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
      setConfirmingDeleteConnectorId(null)
    },
  })

  const deleteFileMutation = useMutation({
    mutationFn: async (artifactId: string) =>
      apiFetch(`/api/knowledge/personal/items/${artifactId}`, token, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['personal-knowledge', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['kb-stats', kbSlug] })
      setConfirmingDeleteFileId(null)
    },
    onError: (err, id) =>
      queryLogger.error('File delete failed', { kb: kbSlug, artifactId: id, err }),
  })

  if (!kb) return null

  const items = stats?.volume ?? 0
  const sourceList = connectors ?? []
  const files = filesData?.items ?? []

  const isMyPersonalKb =
    kb.owner_type === 'user' && !!myUserId && kb.slug === `personal-${myUserId}`
  const isOthersPersonalKb =
    kb.owner_type === 'user' && !!myUserId && kb.slug !== `personal-${myUserId}`
  const canManageKb = isAdmin || !isOthersPersonalKb

  return (
    <div className="space-y-8">
      {/* Quick stats + KB-level actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <Zap className="h-4 w-4" />
            <span>
              <strong className="text-gray-900">
                {sourceList.length + files.length || items}
              </strong>{' '}
              bronnen
            </span>
          </div>
          {items > 0 && (
            <div className="flex items-center gap-2 text-sm text-gray-400">
              <FileText className="h-4 w-4" />
              <span>
                <strong className="text-gray-900">{items}</strong> chunks
              </span>
            </div>
          )}
          {isMyPersonalKb && (
            <span className="text-[10px] font-medium uppercase tracking-wider text-gray-400">
              Mijn
            </span>
          )}
        </div>

        {canManageKb && (
          <div className="flex items-center gap-2">
            {sourceList.length > 0 && (
              <Tooltip label="Alle bronnen synchroniseren">
                <button
                  type="button"
                  disabled={syncAllMutation.isPending}
                  onClick={() => syncAllMutation.mutate(sourceList.map((c) => c.id))}
                  className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors disabled:opacity-50"
                >
                  {syncAllMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                  Sync
                </button>
              </Tooltip>
            )}
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
              <button
                type="button"
                onClick={() => setConfirmingDeleteKb(true)}
                className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 hover:text-[var(--color-destructive)] transition-colors"
              >
                <Trash2 className="h-3.5 w-3.5" />
                Verwijder collectie
              </button>
            </InlineDeleteConfirm>
          </div>
        )}
      </div>

      {/* Bronnen — unified: files + connectors. "Alles is een bron." */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">Bronnen</h2>
          <Link
            to="/app/knowledge/$kbSlug/add-source"
            params={{ kbSlug }}
            className="flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors"
          >
            <Plus className="h-3.5 w-3.5" />
            Bron toevoegen
          </Link>
        </div>

        {sourceList.length === 0 && files.length === 0 && items === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-200 py-10 text-center">
            <Zap className="h-8 w-8 text-gray-300 mx-auto mb-3" />
            <p className="text-sm font-medium text-gray-900">Nog geen bronnen</p>
            <p className="text-xs text-gray-400 mt-1 max-w-sm mx-auto">
              Upload bestanden of verbind externe bronnen zoals Notion of Google Drive.
            </p>
            <Link
              to="/app/knowledge/$kbSlug/add-source"
              params={{ kbSlug }}
              className="inline-flex items-center gap-1.5 mt-4 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
            >
              <Plus className="h-4 w-4" />
              Eerste bron toevoegen
            </Link>
          </div>
        ) : sourceList.length === 0 && files.length === 0 ? (
          /* Items exist (e.g. Block editor docs or legacy content) but no
             connectors / uploads. Show a summary row so the count isn't
             contradicted by an empty list. */
          <div className="rounded-lg border border-gray-200 px-4 py-3 flex items-center gap-3">
            <FileText className="h-5 w-5 text-gray-400" />
            <div className="flex-1">
              <p className="text-sm font-medium text-gray-900">
                {items} {items === 1 ? 'bron' : 'bronnen'} geïndexeerd
              </p>
              <p className="text-xs text-gray-400">Via documenten of eerdere imports</p>
            </div>
            {kb.docs_enabled && kb.gitea_repo_slug && (
              <Link
                to="/app/docs/$kbSlug"
                params={{ kbSlug: kb.slug }}
                className="text-xs font-medium text-gray-700 hover:text-gray-900 transition-colors"
              >
                Openen →
              </Link>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {/* Connectors */}
            {sourceList.map((c) => {
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
                  <div className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-3 hover:bg-gray-50 transition-colors">
                    <Icon className="h-5 w-5 text-gray-400 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {c.name || c.connector_type}
                      </p>
                      {c.last_sync_at && (
                        <p className="text-xs text-gray-400">Laatst gesynchroniseerd</p>
                      )}
                    </div>
                    <SyncStatusBadge status={c.last_sync_status} />
                    {canManageKb && (
                      <div className="flex items-center gap-1 shrink-0">
                        <Tooltip label="Synchroniseren">
                          <button
                            type="button"
                            disabled={isSyncing}
                            onClick={() => syncConnectorMutation.mutate(c.id)}
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
                            onClick={() => setConfirmingDeleteConnectorId(c.id)}
                            aria-label="Delete connector"
                            className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors"
                          >
                            <Trash2 size={14} />
                          </button>
                        </Tooltip>
                      </div>
                    )}
                  </div>
                </InlineDeleteConfirm>
              )
            })}

            {/* Files (same list, under connectors) */}
            {files.slice(0, 20).map((f) => {
              const isDeletingFile =
                deleteFileMutation.isPending && deleteFileMutation.variables === f.id
              return (
                <InlineDeleteConfirm
                  key={`file-${f.id}`}
                  isConfirming={confirmingDeleteFileId === f.id}
                  isPending={isDeletingFile}
                  label={`Bestand "${f.path}" verwijderen?`}
                  cancelLabel="Annuleren"
                  onConfirm={() => deleteFileMutation.mutate(f.id)}
                  onCancel={() => setConfirmingDeleteFileId(null)}
                >
                  <div className="flex items-center gap-3 rounded-lg border border-gray-200 px-4 py-3 hover:bg-gray-50 transition-colors">
                    <File className="h-5 w-5 text-gray-400 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">{f.path}</p>
                      <p className="text-xs text-gray-400">Upload</p>
                    </div>
                    {isMyPersonalKb && (
                      <Tooltip label="Bestand verwijderen">
                        <button
                          type="button"
                          onClick={() => setConfirmingDeleteFileId(f.id)}
                          aria-label="Delete file"
                          className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors shrink-0"
                        >
                          <Trash2 size={14} />
                        </button>
                      </Tooltip>
                    )}
                  </div>
                </InlineDeleteConfirm>
              )
            })}
            {files.length > 20 && (
              <Link
                to="/app/knowledge/$kbSlug/items"
                params={{ kbSlug }}
                className="block text-center text-xs text-gray-400 hover:text-gray-900 py-2 transition-colors"
              >
                + {files.length - 20} meer bronnen
              </Link>
            )}
          </div>
        )}
      </div>

      {/* Documenten link */}
      {kb.docs_enabled && kb.gitea_repo_slug && (
        <div>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">
              Documenten
            </h2>
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
