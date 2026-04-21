import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Pencil, Trash2, Plus, Puzzle } from 'lucide-react'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { useMcpServers, mcpServersQueryKey, type McpServer } from './_api'

export const Route = createFileRoute('/admin/mcps/')({
  component: McpsListPage,
})

function McpsListPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const { data, isLoading, isError } = useMcpServers()

  const [confirmingDeactivateId, setConfirmingDeactivateId] = useState<string | null>(null)

  const deactivateMutation = useMutation({
    mutationFn: async (server: McpServer) => {
      return apiFetch(`/api/mcp-servers/${server.id}`, {
        method: 'PUT',
        body: JSON.stringify({ enabled: false, env: {} }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: mcpServersQueryKey })
    },
    onError: (err, server) => {
      queryLogger.error('MCP deactivate failed', { serverId: server.id, err })
    },
    onSettled: () => {
      setConfirmingDeactivateId(null)
    },
  })

  const enabledServers = data?.servers.filter((s) => s.enabled) ?? []

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-[26px] font-display-bold text-gray-900">{m.admin_mcps_title()}</h1>
        <button
          type="button"
          onClick={() => navigate({ to: '/admin/mcps/new' })}
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.admin_mcps_add_button()}
        </button>
      </div>
      <p className="text-sm text-gray-400 mb-6">{m.admin_mcps_subtitle()}</p>

      {isError ? (
        <p className="py-8 text-sm text-[var(--color-destructive)]">{m.admin_mcps_load_error()}</p>
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : enabledServers.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-16 text-center">
          <Puzzle className="h-10 w-10 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-medium text-gray-900">{m.admin_mcps_no_servers()}</p>
          <button
            type="button"
            onClick={() => navigate({ to: '/admin/mcps/new' })}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {m.admin_mcps_add_button()}
          </button>
        </div>
      ) : (
        <div className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {enabledServers.map((server) => {
            const displayName = server.display_name || server.id
            const isDeactivating =
              deactivateMutation.isPending && deactivateMutation.variables?.id === server.id
            const isConfirming = confirmingDeactivateId === server.id

            return (
              <div
                key={server.id}
                className="flex items-center gap-3 px-2 py-3.5 hover:bg-gray-50 transition-colors"
              >
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50">
                  <Puzzle size={16} strokeWidth={1.75} className="text-gray-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 truncate">{displayName}</span>
                    {server.managed && (
                      <span className="rounded-full bg-gray-50 px-2 py-0.5 text-[10px] font-medium text-gray-700">
                        {m.admin_mcps_builtin()}
                      </span>
                    )}
                  </div>
                  {server.description && (
                    <p className="text-xs text-gray-400 truncate">{server.description}</p>
                  )}
                </div>
                {!server.managed && (
                  <InlineDeleteConfirm
                    isConfirming={isConfirming}
                    isPending={isDeactivating}
                    label={m.admin_mcps_deactivate_confirm({ name: displayName })}
                    cancelLabel={m.admin_mcps_cancel()}
                    onConfirm={() => deactivateMutation.mutate(server)}
                    onCancel={() => setConfirmingDeactivateId(null)}
                  >
                    <div className="flex items-center gap-1 shrink-0">
                      <Tooltip label={m.admin_mcps_edit()}>
                        <button
                          type="button"
                          onClick={() =>
                            navigate({
                              to: '/admin/mcps/$serverId',
                              params: { serverId: server.id },
                            })
                          }
                          aria-label={m.admin_mcps_edit()}
                          className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                        >
                          <Pencil size={14} />
                        </button>
                      </Tooltip>
                      <Tooltip label={m.admin_mcps_deactivate()}>
                        <button
                          type="button"
                          onClick={() => setConfirmingDeactivateId(server.id)}
                          aria-label={m.admin_mcps_deactivate()}
                          className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors"
                        >
                          <Trash2 size={14} />
                        </button>
                      </Tooltip>
                    </div>
                  </InlineDeleteConfirm>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
