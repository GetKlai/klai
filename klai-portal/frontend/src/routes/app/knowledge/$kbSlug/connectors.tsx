import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  RefreshCw, Trash2, Loader2, Plus, Pencil,
  Globe, GitBranch, HardDrive, FileText,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
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

// Notion brand icon (Simple Icons path — no background, uses currentColor)
function NotionIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793c.28 0 .047-.28-.046-.326L17.86 1.968c-.42-.326-.981-.7-2.055-.607L3.01 2.295c-.466.046-.56.28-.374.466zm.793 3.08v13.904c0 .747.373 1.027 1.214.98l14.523-.84c.841-.046.935-.56.935-1.167V6.354c0-.606-.233-.933-.748-.887l-15.177.887c-.56.047-.747.327-.747.933zm14.337.745c.093.42 0 .84-.42.888l-.7.14v10.264c-.608.327-1.168.514-1.635.514-.748 0-.935-.234-1.495-.933l-4.577-7.186v6.952L12.21 19s0 .84-1.168.84l-3.222.186c-.093-.186 0-.653.327-.746l.84-.233V9.854L7.822 9.76c-.094-.42.14-1.026.793-1.073l3.456-.233 4.764 7.279v-6.44l-1.215-.139c-.093-.514.234-.887.653-.933z"/>
    </svg>
  )
}

export const Route = createFileRoute('/app/knowledge/$kbSlug/connectors')({
  component: ConnectorsTab,
})

type ConnectorTypeInfo = { label: string; IconComponent: React.ComponentType<{ className?: string }> }

const CONNECTOR_TYPE_MAP: Record<string, ConnectorTypeInfo> = {
  github:       { label: 'GitHub',       IconComponent: GitBranch },
  web_crawler:  { label: 'Web',          IconComponent: Globe },
  notion:       { label: 'Notion',       IconComponent: NotionIcon },
  google_drive: { label: 'Google Drive', IconComponent: HardDrive },
  ms_docs:      { label: 'MS Docs',      IconComponent: FileText },
}

function ConnectorTypeBadge({ type }: { type: string }) {
  const info = CONNECTOR_TYPE_MAP[type]
  const Icon = info?.IconComponent ?? FileText
  return (
    <span className="inline-flex items-center gap-1 text-xs text-[var(--color-muted-foreground)]">
      <Icon className="h-3.5 w-3.5 shrink-0" />
      {info?.label ?? type}
    </span>
  )
}

function ConnectorsTab() {
  const { kbSlug } = Route.useParams()
  const navigate = useNavigate({ from: Route.fullPath })
  const auth = useAuth()
  const token = auth.user?.access_token
  const queryClient = useQueryClient()
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [syncingIds, setSyncingIds] = useState<Set<string>>(new Set())

  const { data: kb } = useQuery<KnowledgeBase>({
    queryKey: ['app-knowledge-base', kbSlug],
    queryFn: async () => apiFetch<KnowledgeBase>(`/api/app/knowledge-bases/${kbSlug}`, token),
    enabled: !!token,
  })
  const { data: members } = useQuery<MembersResponse>({
    queryKey: ['kb-members', kbSlug],
    queryFn: async () => apiFetch<MembersResponse>(`/api/app/knowledge-bases/${kbSlug}/members`, token),
    enabled: !!token,
  })
  const myUserId = auth.user?.profile?.sub
  const isCreator = !!(myUserId && kb?.created_by === myUserId)
  const isOwner = isCreator || !!(myUserId && members?.users.some((u) => u.user_id === myUserId && u.role === 'owner'))

  const { data: connectors = [], isLoading } = useQuery<ConnectorSummary[]>({
    queryKey: ['kb-connectors-portal', kbSlug],
    queryFn: async () => apiFetch<ConnectorSummary[]>(`/api/app/knowledge-bases/${kbSlug}/connectors/`, token),
    enabled: !!token,
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
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}`, token, { method: 'DELETE' })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', kbSlug] }),
  })

  async function handleSync(id: string) {
    setSyncingIds((prev) => new Set([...prev, id]))
    try {
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${id}/sync`, token, { method: 'POST' })
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
    return <p className="py-4 text-sm text-[var(--color-muted-foreground)]">{m.admin_connectors_loading()}</p>
  }

  return (
    <div className="space-y-3">
      {connectors.length > 0 && (
        <Card>
          <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.admin_connectors_col_name()}</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.admin_connectors_col_type()}</th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">{m.admin_connectors_col_status()}</th>
                  {isOwner && <th className="px-4 py-2.5 w-20" />}
                </tr>
              </thead>
              <tbody>
                {connectors.map((c, i) => {
                  const isSyncing = syncingIds.has(c.id)
                  const isRunning = c.last_sync_status?.toUpperCase() === 'RUNNING'
                  return (
                    <tr key={c.id} className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}>
                      <td className="px-4 py-2.5 font-medium text-[var(--color-purple-deep)]">{c.name}</td>
                      <td className="px-4 py-2.5"><ConnectorTypeBadge type={c.connector_type} /></td>
                      <td className="px-4 py-2.5"><SyncStatusBadge status={c.last_sync_status} lastSyncAt={c.last_sync_at} /></td>
                      {isOwner && (
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-1">
                            <Tooltip label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}>
                              <button
                                disabled={isSyncing || isRunning}
                                onClick={() => void handleSync(c.id)}
                                aria-label={isSyncing || isRunning ? m.admin_connectors_syncing() : m.admin_connectors_action_sync()}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-accent)] hover:opacity-70 disabled:opacity-40"
                              >
                                {isSyncing || isRunning ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                              </button>
                            </Tooltip>
                            <Tooltip label={m.admin_connectors_action_edit()}>
                              <button
                                onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/edit-connector/$connectorId', params: { kbSlug, connectorId: c.id } })}
                                aria-label={m.admin_connectors_action_edit()}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] hover:opacity-70"
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </button>
                            </Tooltip>
                            <Tooltip label={m.admin_connectors_action_delete()}>
                              <button
                                onClick={() => setConfirmingDeleteId(c.id)}
                                aria-label={m.admin_connectors_action_delete()}
                                className="flex h-7 w-7 items-center justify-center text-[var(--color-destructive)] hover:opacity-70"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
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
          </CardContent>
        </Card>
      )}

      {connectors.length === 0 && (
        <p className="text-sm text-[var(--color-muted-foreground)]">{m.knowledge_detail_connectors_empty()}</p>
      )}

      {isOwner && (
        <Button size="sm" variant="outline" onClick={() => void navigate({ to: '/app/knowledge/$kbSlug/add-connector', params: { kbSlug } })}>
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
