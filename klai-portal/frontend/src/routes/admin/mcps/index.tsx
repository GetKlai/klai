import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
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
  const auth = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const token = auth.user?.access_token ?? ''

  const { data, isLoading, isError } = useMcpServers(token)

  const [confirmingDeactivateId, setConfirmingDeactivateId] = useState<string | null>(null)

  // Reuse the existing PUT endpoint with {enabled: false, env: {}}. The backend
  // accepts this as a deactivation and triggers the tenant container restart.
  const deactivateMutation = useMutation({
    mutationFn: async (server: McpServer) => {
      return apiFetch(`/api/mcp-servers/${server.id}`, token, {
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
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">{m.admin_mcps_title()}</h1>
          <p className="mt-1 text-sm text-gray-400">{m.admin_mcps_subtitle()}</p>
        </div>
        <button
          type="button"
          onClick={() => navigate({ to: '/admin/mcps/new' })}
          className="flex items-center gap-1.5 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
        >
          <Plus className="h-4 w-4" />
          {m.admin_mcps_add_button()}
        </button>
      </div>

      {isError ? (
        <p className="py-8 text-sm text-[var(--color-destructive)]">
          {m.admin_mcps_load_error()}
        </p>
      ) : isLoading ? (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 rounded-lg bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : enabledServers.length === 0 ? (
        <div className="flex flex-col items-center gap-5 rounded-lg border border-gray-200 py-16 px-6">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-gray-50">
            <Puzzle size={24} strokeWidth={1.5} className="text-gray-300" />
          </div>
          <div className="text-center space-y-2 max-w-md">
            <p className="text-base font-medium text-gray-900">{m.admin_mcps_no_servers()}</p>
          </div>
          <button
            type="button"
            onClick={() => navigate({ to: '/admin/mcps/new' })}
            className="rounded-lg bg-gray-900 px-5 py-2.5 text-sm font-medium text-white hover:bg-gray-800 transition-colors"
          >
            {m.admin_mcps_add_button()}
          </button>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {enabledServers.map((server) => {
            const displayName = server.display_name || server.id
            const isDeactivating =
              deactivateMutation.isPending && deactivateMutation.variables?.id === server.id
            const isConfirming = confirmingDeactivateId === server.id

            return (
              <div
                key={server.id}
                className="rounded-lg border border-gray-200 p-5 hover:shadow-sm transition-shadow"
              >
                <div className="flex items-start justify-between mb-2">
                  <h3 className="text-sm font-semibold text-gray-900">{displayName}</h3>
                  {!server.managed && (
                    <InlineDeleteConfirm
                      isConfirming={isConfirming}
                      isPending={isDeactivating}
                      label={m.admin_mcps_deactivate_confirm({ name: displayName })}
                      cancelLabel={m.admin_mcps_cancel()}
                      onConfirm={() => deactivateMutation.mutate(server)}
                      onCancel={() => setConfirmingDeactivateId(null)}
                    >
                      <div className="flex items-center gap-1">
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
                            className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-50 transition-colors"
                          >
                            <Pencil size={14} />
                          </button>
                        </Tooltip>
                        <Tooltip label={m.admin_mcps_deactivate()}>
                          <button
                            type="button"
                            onClick={() => setConfirmingDeactivateId(server.id)}
                            aria-label={m.admin_mcps_deactivate()}
                            className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-50 transition-colors"
                          >
                            <Trash2 size={14} />
                          </button>
                        </Tooltip>
                      </div>
                    </InlineDeleteConfirm>
                  )}
                </div>
                {server.description && (
                  <p className="text-xs text-gray-400 line-clamp-2 mb-3">{server.description}</p>
                )}
                {server.managed && (
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-gray-50 px-2.5 py-0.5 text-[10px] font-medium text-gray-700">
                      {m.admin_mcps_builtin()}
                    </span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
