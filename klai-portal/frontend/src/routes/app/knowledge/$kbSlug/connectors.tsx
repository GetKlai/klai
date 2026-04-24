import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect } from 'react'
import {
  RefreshCw, Trash2, Loader2, Plus, Pencil, Globe, FileText, CheckCircle2, AlertTriangle, X,
} from 'lucide-react'
import { SiGithub, SiNotion, SiGoogledrive } from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { SyncStatusBadge } from './-kb-helpers'
import type { ConnectorSummary, KnowledgeBase, MembersResponse } from './-kb-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/connectors')({
  validateSearch: (search: Record<string, unknown>) => ({
    oauth: typeof search.oauth === 'string' ? search.oauth : undefined,
  }),
  component: ConnectorsTab,
})

type ConnectorTypeInfo = { label: () => string; IconComponent: React.ComponentType<{ className?: string }> }

// Paraglide message functions — keeps the type labels i18n-driven instead of
// hard-coded strings. The key on the right is the Paraglide-generated function
// (see klai-portal/frontend/messages/*.json).
const CONNECTOR_TYPE_MAP: Record<string, ConnectorTypeInfo> = {
  github:       { label: m.admin_connectors_type_github,       IconComponent: SiGithub },
  web_crawler:  { label: m.admin_connectors_type_website,      IconComponent: Globe },
  notion:       { label: m.admin_connectors_type_notion,       IconComponent: SiNotion },
  google_drive: { label: m.admin_connectors_type_google_drive, IconComponent: SiGoogledrive },
  ms_docs:      { label: m.admin_connectors_type_ms_docs,      IconComponent: FileText },
}

/** OAuth-backed connector types that support the /api/oauth/{provider}/authorize reconnect flow. */
const OAUTH_RECONNECTABLE = new Set<string>(['google_drive', 'ms_docs'])

function ConnectorsTab() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate({ from: Route.fullPath })
  const auth = useAuth()
  const queryClient = useQueryClient()
  const { oauth } = Route.useSearch()
  const [showOAuthBanner, setShowOAuthBanner] = useState(oauth === 'connected')
  const [showOAuthFailedBanner, setShowOAuthFailedBanner] = useState(oauth === 'failed')
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)

  // Clean up the ?oauth= param from the URL after mounting so a reload doesn't re-show the banner.
  useEffect(() => {
    if (oauth === 'connected' || oauth === 'failed') {
      window.history.replaceState({}, '', window.location.pathname)
    }
  }, [oauth])
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())
  // Per-connector reconnect state: tracks which connector is mid-redirect and
  // which failed so the UI can show a spinner / error message without stale
  // state bleeding to other rows.
  const [reconnectingId, setReconnectingId] = useState<string | null>(null)
  const [reconnectErrorId, setReconnectErrorId] = useState<string | null>(null)

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
    enabled: auth.isAuthenticated,
  })
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
    enabled: auth.isAuthenticated,
  })
  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  const { data: connectors = [], isLoading } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`),
    enabled: auth.isAuthenticated,
    refetchInterval: (query) => {
      const data = query.state.data
      if (Array.isArray(data) && data.some((c) => c.last_sync_status === 'RUNNING' || c.last_sync_status === 'running')) {
        return 5000
      }
      return false
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}`, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] }),
  })

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}/sync`, { method: 'POST' })
      queryClient.setQueryData(['kb-connectors-portal', kbSlug], (old: ConnectorSummary[] | undefined) =>
        old?.map((c) => c.id === id ? { ...c, last_sync_status: 'running' } : c)
      )
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    } catch {
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    } finally {
      setSyncingIds((prev) => { const next = new Set(prev); next.delete(id); return next })
    }
  }

  // SPEC-KB-MS-DOCS-001 reconnect-signal: when sync_engine catches
  // OAuthReconnectRequiredError it marks the connector AUTH_ERROR. User
  // recovers by triggering a fresh OAuth authorize flow — same endpoint
  // the add-connector and edit-connector pages use.
  async function handleReconnect(connectorType: string, connectorId: string) {
    setReconnectErrorId(null)
    setReconnectingId(connectorId)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        `/api/oauth/${encodeURIComponent(connectorType)}/authorize?kb_slug=${encodeURIComponent(kbSlug)}&connector_id=${encodeURIComponent(connectorId)}`,
      )
      window.location.assign(authorize_url)
      // Intentionally don't clear `reconnectingId` on success: the navigation
      // unmounts this tree, so the spinner stays visible until the redirect
      // completes (vs. briefly flashing back to the Reconnect button).
    } catch {
      setReconnectingId(null)
      setReconnectErrorId(connectorId)
      // Also refetch in case the error was a stale-session 401 — the
      // connectors list will refresh with up-to-date status.
      void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] })
    }
  }

  if (isLoading) {
    return <p className="py-4 text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
  }

  return (
    <div className="space-y-3">
      {showOAuthBanner && (
        <div className="flex gap-2 items-center rounded-lg border border-[var(--color-success)]/30 bg-[var(--color-success)]/5 p-3 text-xs text-[var(--color-success)]">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{m.admin_connectors_oauth_success()}</span>
          <button onClick={() => setShowOAuthBanner(false)} aria-label="Dismiss" className="hover:opacity-70 transition-opacity">
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
      {showOAuthFailedBanner && (
        <div className="flex gap-2 items-center rounded-lg border border-[var(--color-destructive)]/30 bg-[var(--color-destructive)]/5 p-3 text-xs text-[var(--color-destructive)]">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{m.admin_connectors_oauth_failed()}</span>
          <button onClick={() => setShowOAuthFailedBanner(false)} aria-label="Dismiss" className="hover:opacity-70 transition-opacity">
            <X className="h-3 w-3" />
          </button>
        </div>
      )}
      {connectors.length > 0 && (
        <table className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="py-3 pr-2 w-6" />
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                {m.admin_connectors_col_name()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-28">
                {m.admin_connectors_col_type()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-32">
                {m.admin_connectors_col_status()}
              </th>
              {isOwner && <th className="py-3 text-right w-28" />}
            </tr>
          </thead>
          <tbody>
            {connectors.map((c) => {
              const info = CONNECTOR_TYPE_MAP[c.connector_type]
              const Icon = info?.IconComponent ?? FileText
              const typeLabel = info?.label() ?? c.connector_type
              const isSyncing = syncingIds.has(c.id)
              const isRunning = c.last_sync_status?.toUpperCase() === 'RUNNING'
              return (
                <tr key={c.id} className="border-b border-[var(--color-border)] last:border-b-0">
                  <td className="py-4 pr-2 align-top w-6">
                    <Tooltip className="leading-none mt-px" label={typeLabel}>
                      <Icon className="h-4 w-4 text-[var(--color-muted-foreground)]" />
                    </Tooltip>
                  </td>
                  <td className="py-4 pr-4 align-top">
                    <span className="font-medium text-[var(--color-foreground)]">{c.name}</span>
                  </td>
                  <td className="py-4 pr-4 align-top w-28">
                    <span className="text-xs text-[var(--color-muted-foreground)]">{typeLabel}</span>
                  </td>
                  <td className="py-4 pr-4 align-top w-32">
                    <SyncStatusBadge status={c.last_sync_status} lastSyncAt={c.last_sync_at} />
                    {isOwner
                      && c.last_sync_status?.toUpperCase() === 'AUTH_ERROR'
                      && OAUTH_RECONNECTABLE.has(c.connector_type) && (
                      <div className="mt-1.5 space-y-1">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={reconnectingId === c.id}
                          onClick={() => void handleReconnect(c.connector_type, c.id)}
                          className="h-7 text-xs"
                        >
                          {reconnectingId === c.id ? (
                            <Loader2 className="h-3 w-3 animate-spin" />
                          ) : null}
                          {m.admin_connectors_reconnect_action()}
                        </Button>
                        {reconnectErrorId === c.id && (
                          <p className="text-xs text-[var(--color-destructive)]">
                            {m.admin_connectors_reconnect_error()}
                          </p>
                        )}
                      </div>
                    )}
                    {c.last_sync_documents_ok != null && c.last_sync_documents_ok > 0 && (
                      <p className="mt-0.5 text-xs text-[var(--color-muted-foreground)] tabular-nums">
                        {c.last_sync_documents_ok.toLocaleString()} {m.connectors_documents_indexed()}
                      </p>
                    )}
                  </td>
                  {isOwner && (
                    <td className="py-4 align-top text-right w-28">
                      <div className="flex items-start justify-end gap-2 mt-px">
                        <Tooltip label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}>
                          <button
                            disabled={isSyncing || isRunning}
                            onClick={() => void handleSync(c.id)}
                            aria-label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}
                            className="inline-flex items-center justify-center text-[var(--color-accent)] transition-opacity hover:opacity-70 disabled:opacity-40"
                          >
                            {isSyncing || isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                          </button>
                        </Tooltip>
                        <Tooltip label={m.admin_connectors_action_edit()}>
                          <button
                            onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/edit-connector/$connectorId', params: { kbSlug, connectorId: c.id } })}
                            aria-label={m.admin_connectors_action_edit()}
                            className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                        </Tooltip>
                        <Tooltip label={m.admin_connectors_action_delete()}>
                          <button
                            onClick={() => setConfirmingDeleteId(c.id)}
                            aria-label={m.admin_connectors_action_delete()}
                            className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </Tooltip>
                      </div>
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {connectors.length === 0 && (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_detail_connectors_empty()}</p>
      )}

      {isOwner && (
        <Button size="sm" variant="outline" onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/add-source', params: { kbSlug } })}>
          <Plus className="h-4 w-4 mr-1" />
          {m.admin_connectors_add_button()}
        </Button>
      )}

      <AlertDialog open={confirmingDeleteId !== null} onOpenChange={(open) => { if (!open) setConfirmingDeleteId(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{m.admin_connectors_delete_confirm_title()}</AlertDialogTitle>
            <AlertDialogDescription>{m.admin_connectors_delete_confirm_description()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.admin_connectors_cancel()}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={() => { if (confirmingDeleteId) deleteMutation.mutate(confirmingDeleteId); setConfirmingDeleteId(null) }}
            >
              {m.admin_connectors_action_delete()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
