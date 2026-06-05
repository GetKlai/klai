import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { RowActionGroup, BorderedRowActionIconButton } from '@/components/ui/row-action'
import {
  DataTable,
  DataTableHeader,
  DataTableBody,
  DataTableRow,
  DataTableHead,
  DataTableCell,
} from '@/components/ui/data-table'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useMcpServers } from './_api'

export const Route = createFileRoute('/admin/mcps/new')({
  component: McpsNewPage,
})

function McpsNewPage() {
  const navigate = useNavigate()
  const { data, isLoading, isError, error, refetch } = useMcpServers()

  // Managed servers are always enabled and live in the main list - never in the picker.
  const availableServers = data?.servers.filter((s) => !s.enabled && !s.managed) ?? []

  function handleBack() {
    void navigate({ to: '/admin/mcps' })
  }

  return (
    <div className="mx-auto max-w-6xl px-6 pt-4 pb-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.admin_mcps_new_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {m.admin_mcps_new_subtitle()}
          </p>
        </div>
        <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_mcps_back()}
        </Button>
      </div>

      {isError ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(m.admin_mcps_load_error())}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <ListLoadingState label={m.admin_mcps_loading()} />
      ) : availableServers.length === 0 ? (
        <ListEmptyState title={m.admin_mcps_new_empty()} />
      ) : (
        <DataTable>
          <DataTableHeader>
            <DataTableRow>
              <DataTableHead>{m.admin_mcps_col_name()}</DataTableHead>
              <DataTableHead>{m.admin_mcps_col_description()}</DataTableHead>
              <DataTableHead align="right" aria-label={m.admin_mcps_col_actions()} />
            </DataTableRow>
          </DataTableHeader>
          <DataTableBody>
            {availableServers.map((server) => (
              <DataTableRow key={server.id}>
                <DataTableCell>
                  <span className="font-medium">{server.display_name || server.id}</span>
                </DataTableCell>
                <DataTableCell className="text-gray-400">{server.description}</DataTableCell>
                <DataTableCell align="right">
                  <RowActionGroup>
                    <BorderedRowActionIconButton
                      action="add"
                      label={m.admin_mcps_add_button()}
                      onClick={() =>
                        navigate({
                          to: '/admin/mcps/$serverId',
                          params: { serverId: server.id },
                        })
                      }
                    />
                  </RowActionGroup>
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>
      )}
    </div>
  )
}
