import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { Pencil, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
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
    <div className="p-6 space-y-6 max-w-6xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-gray-900">
            {m.admin_mcps_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {m.admin_mcps_subtitle()}
          </p>
        </div>
        <Button size="sm" onClick={() => navigate({ to: '/admin/mcps/new' })}>
          {m.admin_mcps_add_button()}
        </Button>
      </div>

      {isError ? (
        <p className="py-8 text-sm text-[var(--color-destructive)]">
          {m.admin_mcps_load_error()}
        </p>
      ) : isLoading ? (
        <p className="py-8 text-sm text-gray-400">
          {m.admin_mcps_loading()}
        </p>
      ) : enabledServers.length === 0 ? (
        <p className="py-8 text-sm text-gray-400">
          {m.admin_mcps_no_servers()}
        </p>
      ) : (
        <table className="w-full text-sm table-fixed border-t border-b border-gray-200">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-48">
                {m.admin_mcps_col_name()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em]">
                {m.admin_mcps_col_description()}
              </th>
              <th className="py-3 text-right w-28" aria-label={m.admin_mcps_col_actions()} />
            </tr>
          </thead>
          <tbody>
            {enabledServers.map((server) => {
              const displayName = server.display_name || server.id
              const isDeactivating =
                deactivateMutation.isPending &&
                deactivateMutation.variables?.id === server.id

              return (
                <tr key={server.id} className="border-b border-gray-200 last:border-b-0">
                  <td className="py-4 pr-4 align-top">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{displayName}</span>
                      {server.managed && (
                        <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.04em] text-gray-400">
                          {m.admin_mcps_builtin()}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-4 pr-4 align-top text-gray-400">
                    {server.description}
                  </td>
                  <td className="py-4 align-top text-right w-28">
                    {server.managed ? (
                      <div className="flex items-start justify-end gap-2 mt-px" />
                    ) : (
                      <InlineDeleteConfirm
                        isConfirming={confirmingDeactivateId === server.id}
                        isPending={isDeactivating}
                        label={m.admin_mcps_deactivate_confirm({ name: displayName })}
                        cancelLabel={m.admin_mcps_cancel()}
                        onConfirm={() => deactivateMutation.mutate(server)}
                        onCancel={() => setConfirmingDeactivateId(null)}
                      >
                        <div className="flex items-start justify-end gap-2 mt-px">
                          <Tooltip label={m.admin_mcps_edit()}>
                            <button
                              onClick={() =>
                                navigate({
                                  to: '/admin/mcps/$serverId',
                                  params: { serverId: server.id },
                                })
                              }
                              aria-label={m.admin_mcps_edit()}
                              className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                            >
                              <Pencil className="h-4 w-4" />
                            </button>
                          </Tooltip>
                          <Tooltip label={m.admin_mcps_deactivate()}>
                            <button
                              onClick={() => setConfirmingDeactivateId(server.id)}
                              aria-label={m.admin_mcps_deactivate()}
                              className="inline-flex items-center justify-center text-[var(--color-destructive)] transition-opacity hover:opacity-70"
                            >
                              <Trash2 className="h-4 w-4" />
                            </button>
                          </Tooltip>
                        </div>
                      </InlineDeleteConfirm>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}
