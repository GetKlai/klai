import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  RefreshCw, Trash2, Loader2, Plus, Pencil, Globe, FileText,
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
  component: ConnectorsTab,
})

type ConnectorTypeInfo = { label: string; IconComponent: React.ComponentType<{ className?: string }> }

const CONNECTOR_TYPE_MAP: Record<string, ConnectorTypeInfo> = {
  github:       { label: 'GitHub',       IconComponent: SiGithub },
  web_crawler:  { label: 'Web',          IconComponent: Globe },
  notion:       { label: 'Notion',       IconComponent: SiNotion },
  google_drive: { label: 'Google Drive', IconComponent: SiGoogledrive },
  ms_docs:      { label: 'MS Docs',      IconComponent: FileText },
}

function ConnectorsTab() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate({ from: Route.fullPath })
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`),
  })
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`),
  })
  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  const { data: connectors = [], isLoading } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`),
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

  if (isLoading) {
    return <p className="py-4 text-sm text-gray-400">{m.admin_connectors_loading()}</p>
  }

  return (
    <div className="space-y-3">
      {connectors.length > 0 && (
        <table className="w-full text-sm table-fixed border-t border-b border-gray-200">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="py-3 pr-2 w-6" />
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em]">
                {m.admin_connectors_col_name()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-28">
                {m.admin_connectors_col_type()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-32">
                {m.admin_connectors_col_status()}
              </th>
              {isOwner && <th className="py-3 text-right w-28" />}
            </tr>
          </thead>
          <tbody>
            {connectors.map((c) => {
              const info = CONNECTOR_TYPE_MAP[c.connector_type]
              const Icon = info?.IconComponent ?? FileText
              const typeLabel = info?.label ?? c.connector_type
              const isSyncing = syncingIds.has(c.id)
              const isRunning = c.last_sync_status?.toUpperCase() === 'RUNNING'
              return (
                <tr key={c.id} className="border-b border-gray-200 last:border-b-0">
                  <td className="py-4 pr-2 align-top w-6">
                    <Tooltip className="leading-none mt-px" label={typeLabel}>
                      <Icon className="h-4 w-4 text-gray-400" />
                    </Tooltip>
                  </td>
                  <td className="py-4 pr-4 align-top">
                    <span className="font-medium text-gray-900">{c.name}</span>
                  </td>
                  <td className="py-4 pr-4 align-top w-28">
                    <span className="text-xs text-gray-400">{typeLabel}</span>
                  </td>
                  <td className="py-4 pr-4 align-top w-32">
                    <SyncStatusBadge status={c.last_sync_status} lastSyncAt={c.last_sync_at} />
                  </td>
                  {isOwner && (
                    <td className="py-4 align-top text-right w-28">
                      <div className="flex items-start justify-end gap-2 mt-px">
                        <Tooltip label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}>
                          <button
                            disabled={isSyncing || isRunning}
                            onClick={() => void handleSync(c.id)}
                            aria-label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}
                            className="inline-flex items-center justify-center text-gray-700 transition-opacity hover:opacity-70 disabled:opacity-40"
                          >
                            {isSyncing || isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                          </button>
                        </Tooltip>
                        <Tooltip label={m.admin_connectors_action_edit()}>
                          <button
                            onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/edit-connector/$connectorId', params: { kbSlug, connectorId: c.id } })}
                            aria-label={m.admin_connectors_action_edit()}
                            className="inline-flex items-center justify-center text-gray-700 transition-opacity hover:opacity-70"
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
        <p className="text-sm text-gray-400">{m.knowledge_detail_connectors_empty()}</p>
      )}

      {isOwner && (
        <Button
          size="sm"
          variant="outline"
          className="rounded-lg border-gray-200 text-gray-700 hover:bg-gray-50"
          onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/add-connector', params: { kbSlug } })}
        >
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
