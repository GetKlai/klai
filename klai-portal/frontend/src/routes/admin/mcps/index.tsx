import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { BorderedRowActionIconButton, RowActionGroup } from '@/components/ui/row-action'
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
  const { data, isLoading, error, refetch } = useMcpServers()

  const [confirmingDeactivateId, setConfirmingDeactivateId] = useState<string | null>(null)

  // Reuse the existing PUT endpoint with {enabled: false, env: {}}. The backend
  // accepts this as a deactivation and triggers the tenant container restart.
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
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-6">
      <PageHeader
        title={m.admin_mcps_title()}
        description={m.admin_mcps_subtitle()}
        actions={
          <Button size="sm" onClick={() => navigate({ to: '/admin/mcps/new' })}>
            {m.admin_mcps_add_button()}
          </Button>
        }
      />

      <PageIntro>
        <p>{m.admin_mcps_intro_body()}</p>
        <p>{m.admin_mcps_intro_runtime()}</p>
      </PageIntro>

      {error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_mcps_loading()} />
      ) : enabledServers.length === 0 ? (
        <ListEmptyState title={m.admin_mcps_no_servers()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead className="w-48">{m.admin_mcps_col_name()}</DataTableHead>
              <DataTableHead>{m.admin_mcps_col_description()}</DataTableHead>
              <DataTableHead align="right" className="w-28">
                {m.admin_mcps_col_actions()}
              </DataTableHead>
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {enabledServers.map((server) => {
              const displayName = server.display_name || server.id
              const isDeactivating =
                deactivateMutation.isPending &&
                deactivateMutation.variables?.id === server.id
              const isConfirming = confirmingDeactivateId === server.id
              const openEdit = () =>
                navigate({
                  to: '/admin/mcps/$serverId',
                  params: { serverId: server.id },
                })

              return (
                <DataTableRow
                  key={server.id}
                  interactive={!server.managed}
                  confirming={isConfirming}
                  onClick={server.managed ? undefined : () => void openEdit()}
                >
                  <DataTableCell>
                    <div className="flex items-center gap-2">
                      <span className="font-medium">{displayName}</span>
                      {server.managed && (
                        <Badge variant="secondary">{m.admin_mcps_builtin()}</Badge>
                      )}
                    </div>
                  </DataTableCell>
                  <DataTableCell className="text-gray-400">
                    {server.description}
                  </DataTableCell>
                  <DataTableCell
                    align="right"
                    className="w-28"
                    onClick={server.managed ? undefined : (e) => e.stopPropagation()}
                  >
                    {server.managed ? null : (
                      <InlineDeleteConfirm
                        isConfirming={isConfirming}
                        isPending={isDeactivating}
                        label={m.admin_mcps_deactivate_confirm({ name: displayName })}
                        cancelLabel={m.admin_mcps_cancel()}
                        onConfirm={() => deactivateMutation.mutate(server)}
                        onCancel={() => setConfirmingDeactivateId(null)}
                      >
                        <RowActionGroup>
                          <BorderedRowActionIconButton
                            label={m.admin_mcps_edit()}
                            action="edit"
                            onClick={() => openEdit()}
                          />
                          <BorderedRowActionIconButton
                            label={m.admin_mcps_deactivate()}
                            action="delete"
                            onClick={() => setConfirmingDeactivateId(server.id)}
                          />
                        </RowActionGroup>
                      </InlineDeleteConfirm>
                    )}
                  </DataTableCell>
                </DataTableRow>
              )
            })}
          </DataTableBody>
        </DataTable>
      )}
    </div>
  )
}
